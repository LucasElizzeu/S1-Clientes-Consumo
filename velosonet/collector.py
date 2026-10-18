"""Coleta de uma BBU (MML + Grafana) e consolidação. Falha de uma BBU nunca derruba as demais."""
from __future__ import annotations
import asyncio, logging
from datetime import datetime
from pathlib import Path
from .config import ROOT
from .errors import *
from .mml_parser import parse_mml, validate, extract_users

log = logging.getLogger(__name__)

def _save_raw(bbu, cmd, text):
    d = ROOT / "data" / "raw"; d.mkdir(parents=True, exist_ok=True)
    name = f"{datetime.now():%Y%m%d_%H%M%S}_bbu{bbu.id}_{cmd.split()[1]}.txt"
    (d / name).write_text(text, encoding="utf-8")

def _classify(e: Exception) -> str:
    return {BbuUnavailable: "unavailable", ParserNotConfigured: "parser_not_configured",
            SelectorsNotConfigured: "not_configured", ConfigError: "not_configured",
            MmlError: "query_failed", MmlIncomplete: "incomplete"}.get(type(e), "query_failed")

async def _query_users(bbu, session, settings, key):
    spec = settings["commands"][key]
    if spec["parser"].get("mode", "unconfigured") == "unconfigured":
        raise ParserNotConfigured(f"{key}: parser não configurado")
    text = await session.run_mml(spec["command"])
    if settings.get("save_raw_responses"): _save_raw(bbu, spec["command"], text)
    return extract_users(validate(parse_mml(text)), spec["parser"])

async def collect_bbu(bbu, session, settings, grafana=None) -> dict:
    row = dict(bbu_id=bbu.id, site=bbu.site, locality=bbu.locality, users4g=None,
               users5g=("N/A" if not bbu.has_5g else None), traffic=None, traffic_metric=None,
               state="ok", errors=[], cells5g={})
    mml_fail, unavailable = 0, False

    async def mml():
        nonlocal mml_fail, unavailable
        if bbu.id in settings.get("mml_disabled_bbus", [5]):
            unavailable = True
            row["errors"].append("mml:unavailable:BBU sem acesso; consulta suspensa")
            return
        for key, attr in (("users_4g", "users4g"), ("users_5g", "users5g")):
            if key == "users_5g" and not bbu.has_5g: continue
            try:
                m = await _query_users(bbu, session, settings, key)
                row[attr] = m.total
                if key == "users_5g": row["cells5g"] = m.per_cell
            except Exception as e:
                cat = _classify(e); mml_fail += 1
                row["errors"].append(f"{key}:{cat}:{e}")
                log.warning("BBU %s %s: %s (%s)", bbu.id, key, cat, e)
                if isinstance(e, BbuUnavailable): unavailable = True; break

    async def graf():
        if not (grafana and settings.get("grafana_enabled", True)): return
        try:
            v = await grafana.panel_value(bbu.grafana)
            row["traffic"], row["traffic_metric"] = v["mbps"], v["metric"]
        except Exception as e:
            row["errors"].append(f"grafana:{type(e).__name__}:{e}")
            log.warning("BBU %s grafana: %s", bbu.id, e)

    try:
        await asyncio.wait_for(asyncio.gather(mml(), graf()), timeout=settings["bbu_timeout_s"])
    except asyncio.TimeoutError:
        row["errors"].append("timeout"); unavailable = True

    expected = 1 + int(bbu.has_5g)
    if unavailable: row["state"] = "unavailable"
    elif mml_fail >= expected: row["state"] = "failed"
    elif mml_fail or row["errors"]: row["state"] = "partial"
    return row
