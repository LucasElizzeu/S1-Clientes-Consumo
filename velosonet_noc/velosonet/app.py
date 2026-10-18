"""Orquestração: Rotina A (keep-alive, 10 min) e Rotina B (relatório horário) com APScheduler."""
from __future__ import annotations
import asyncio, logging
from datetime import datetime
from zoneinfo import ZoneInfo
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from playwright.async_api import async_playwright

from .bbu_session import BbuSession
from .collector import collect_bbu
from .config import ROOT
from .db import Database
from .grafana import GrafanaClient
from .report import render_report, build_summary, caption
from . import telegram_sender

log = logging.getLogger(__name__)

class App:
    def __init__(self, settings):
        self.s, self.tz = settings, ZoneInfo(settings["timezone"])
        self.db = Database(ROOT / "data" / "history.sqlite")
        self.sessions, self.grafana, self.pw, self.browser = {}, None, None, None
        self.run_lock = asyncio.Lock()

    async def start(self):
        self.pw = await async_playwright().start()
        pins = sorted({b.tls_spki_sha256 for b in self.s.bbus if b.tls_spki_sha256})
        args = [f"--ignore-certificate-errors-spki-list={','.join(pins)}"] if pins else []
        if pins: log.info("TLS: %d chave(s) pública(s) de BBU fixada(s) (SPKI pin)", len(pins))
        self.browser = await self.pw.chromium.launch(headless=True, args=args)
        self.sessions = {b.id: BbuSession(b, self.s, self.browser) for b in self.s.bbus}
        if self.s.get("grafana_enabled", True):
            try: self.grafana = GrafanaClient()
            except Exception as e: log.error("Grafana desabilitado: %s", e)

    async def stop(self):
        async def safe(coro):
            try: await coro
            except Exception as e: log.debug("erro ao encerrar: %s", type(e).__name__)   # ex.: driver já encerrado (Ctrl+C)
        for s in self.sessions.values(): await safe(s.close())
        if self.grafana: await safe(self.grafana.close())
        if self.browser: await safe(self.browser.close())
        if self.pw: await safe(self.pw.stop())

    # ---- Rotina A ----
    async def routine_a(self):
        async def one(s):
            try:
                res = await s.keepalive()
                if res.startswith("failed"): self.db.event(s.bbu.id, "keepalive_failed", res)
            except Exception as e:
                self.db.event(s.bbu.id, "keepalive_error", f"{type(e).__name__}: {e}")
                log.warning("keep-alive BBU %s: %s", s.bbu.id, e)
        await asyncio.gather(*(one(s) for s in self.sessions.values()))

    # ---- Rotina B ----
    async def routine_b(self, simulation=False):
        if self.run_lock.locked():
            log.warning("Rotina B ainda em execução; ciclo ignorado"); return
        async with self.run_lock:
            now = datetime.now(self.tz)
            slot = now.strftime("%Y-%m-%dT%H")
            run_id = self.db.claim_slot(slot)
            if run_id is None:
                log.info("slot %s já processado/em andamento", slot); return
            rows = await asyncio.gather(*(collect_bbu(b, self.sessions[b.id], self.s, self.grafana) for b in self.s.bbus))
            rows = list(rows)
            self.db.save_rows(run_id, rows, now.isoformat(timespec="seconds"))
            png = render_report(rows, self.s, now, ROOT / "data" / "reports" / f"{now:%Y%m%d_%H%M}.png")
            cap = caption(build_summary(rows, self.s, now))
            self.db.finish_run(run_id, png, cap)
            if self.s.get("telegram_enabled", True):
                for p in self.db.pending_runs(telegram_sender.DEST):   # inclui reenvio de pendentes recentes
                    await telegram_sender.send_photo(self.db, p["id"], p["png_path"], p["caption"])

    def schedule(self) -> AsyncIOScheduler:
        sch = AsyncIOScheduler(timezone=self.tz)
        sch.add_job(self.routine_a, "interval", minutes=self.s["keepalive_interval_min"], id="A",
                    max_instances=1, coalesce=True, misfire_grace_time=120)
        sch.add_job(self.routine_b, "cron", minute=self.s["report_minute"], id="B",
                    max_instances=1, coalesce=True, misfire_grace_time=600)
        return sch
