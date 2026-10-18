"""Cliente Grafana via API oficial: lê o dashboard, localiza o painel pelo TÍTULO (conferindo o id esperado),
reutiliza os targets e consulta /api/ds/query. Retorna o ÚLTIMO ponto numérico real da série, convertido para
Mb/s pela unidade configurada no próprio painel (nada de estimar valores olhando o gráfico)."""
from __future__ import annotations
import copy, logging, os, re, time
try:
    import httpx
except ImportError:          # permite testar a lógica pura sem httpx instalado
    httpx = None
from .errors import GrafanaError, ConfigError

log = logging.getLogger(__name__)

# unidades de TAXA do Grafana -> fator para Mb/s
UNIT_SCALE = {"bps": 1e-6, "Kbits": 1e-3, "Mbits": 1.0, "Gbits": 1e3,
              "Bps": 8e-6, "KBs": 8e-3, "MBs": 8.0, "GBs": 8e3}

def _norm(t): return re.sub(r"[^a-z0-9]", "", (t or "").lower())

def _walk_panels(panels):
    for p in panels or []:
        yield p
        yield from _walk_panels(p.get("panels"))

def _subst(obj, variables):
    if isinstance(obj, str):
        for k, v in variables.items():
            obj = obj.replace("${%s}" % k, str(v)).replace("$%s" % k, str(v))
        return obj
    if isinstance(obj, list): return [_subst(x, variables) for x in obj]
    if isinstance(obj, dict): return {k: _subst(v, variables) for k, v in obj.items()}
    return obj

def resolve_panel(dash: dict, spec: dict) -> dict:
    panels = list(_walk_panels(dash.get("panels")))
    title, pid = spec.get("panel_title"), spec.get("panel_id")
    if title:
        m = [p for p in panels if _norm(p.get("title")) == _norm(title)]
        if len(m) != 1:
            raise GrafanaError(f"painel '{title}': {len(m)} correspondências no dashboard (esperado 1)")
        panel = m[0]
        if pid is not None and panel.get("id") != pid:
            raise GrafanaError(f"painel '{title}' tem id {panel.get('id')}, mas a configuração diz {pid}")
    elif pid is not None:
        panel = next((p for p in panels if p.get("id") == pid), None)
        if not panel: raise GrafanaError(f"painel id {pid} não encontrado")
    else:
        raise ConfigError("grafana: defina panel_title (preferível) ou panel_id")
    allowed = spec.get("allowed_panel_ids")
    if allowed and panel.get("id") not in allowed:
        raise GrafanaError(f"painel '{panel.get('title')}' (id {panel.get('id')}) fora da lista esperada {allowed}")
    return panel

def unit_scale(panel: dict, spec: dict) -> tuple[float, str]:
    if spec.get("scale") not in (None, ""):
        return float(spec["scale"]), "scale configurado"
    unit = ((panel.get("fieldConfig") or {}).get("defaults") or {}).get("unit")
    if unit in UNIT_SCALE:
        return UNIT_SCALE[unit], f"unidade do painel: {unit}"
    raise GrafanaError(f"unidade do painel '{unit}' sem conversão conhecida; defina 'scale' (fator para Mb/s)")

def last_value(frames, spec, scale) -> dict:
    for fr in frames:
        vals = fr["data"]["values"]
        if len(vals) < 2: continue
        for t, v in zip(reversed(vals[0]), reversed(vals[1])):
            if v is None: continue
            age = time.time() - t / 1000
            if age > spec.get("max_age_s", 900):
                raise GrafanaError(f"dado desatualizado ({int(age)}s)")
            return {"mbps": float(v) * scale, "ts": t / 1000, "metric": spec["metric"]}
    raise GrafanaError("série sem valores numéricos recentes")

class GrafanaClient:
    def __init__(self, base_url=None, timeout=20):
        if httpx is None: raise ConfigError("instale httpx (pip install -r requirements.txt)")
        self.base = (base_url or os.getenv("GRAFANA_URL") or "").rstrip("/")
        if not self.base: raise ConfigError("GRAFANA_URL não configurada")
        h, auth = {}, None
        if os.getenv("GRAFANA_TOKEN"): h["Authorization"] = f"Bearer {os.getenv('GRAFANA_TOKEN')}"
        elif os.getenv("GRAFANA_USER"): auth = (os.getenv("GRAFANA_USER"), os.getenv("GRAFANA_PASS", ""))
        else: raise ConfigError("Defina GRAFANA_TOKEN ou GRAFANA_USER/GRAFANA_PASS")
        if os.getenv("GRAFANA_ORG_ID"): h["X-Grafana-Org-Id"] = os.getenv("GRAFANA_ORG_ID")
        self.http = httpx.AsyncClient(base_url=self.base, headers=h, auth=auth, timeout=timeout, verify=True)
        self._cache = {}

    async def close(self): await self.http.aclose()

    async def dashboard(self, uid, max_age=60):
        c = self._cache.get(uid)
        if c and time.monotonic() - c[0] < max_age: return c[1]
        r = await self.http.get(f"/api/dashboards/uid/{uid}"); r.raise_for_status()
        dash = r.json()["dashboard"]; self._cache[uid] = (time.monotonic(), dash); return dash

    async def list_panels(self, uid) -> list[dict]:
        dash = await self.dashboard(uid, max_age=0)
        out = []
        for p in _walk_panels(dash.get("panels")):
            if p.get("type") == "row": continue
            out.append(dict(id=p.get("id"), type=p.get("type"), title=p.get("title"),
                            unit=((p.get("fieldConfig") or {}).get("defaults") or {}).get("unit"),
                            targets=[{k: v for k, v in t.items() if k != "datasource"} for t in p.get("targets", [])]))
        return out

    async def panel_value(self, spec: dict) -> dict:
        need = [k for k in ("dashboard_uid", "metric") if not spec.get(k)]
        if need: raise ConfigError(f"grafana não configurado: {', '.join(need)}")
        try:
            dash = await self.dashboard(spec["dashboard_uid"])
            panel = resolve_panel(dash, spec)
            scale, why = unit_scale(panel, spec)
            targets = [t for t in panel.get("targets", []) if not t.get("hide")]
            ref = spec.get("ref_id") or (targets[0].get("refId") if targets else None)
            queries = []
            for t in targets:
                if t.get("refId") != ref: continue
                q = _subst(copy.deepcopy(t), spec.get("variables") or {})
                q.setdefault("datasource", panel.get("datasource") or dash.get("datasource"))
                q.update(intervalMs=60000, maxDataPoints=500)
                queries.append(q)
            if not queries: raise GrafanaError("nenhum target utilizável no painel")
            r = await self.http.post("/api/ds/query", json={"queries": queries, "from": "now-30m", "to": "now"})
            r.raise_for_status()
            frames = r.json()["results"][ref]["frames"]
        except httpx.HTTPError as e:
            raise GrafanaError(f"HTTP {type(e).__name__}") from None
        except (KeyError, StopIteration, IndexError) as e:
            raise GrafanaError(f"resposta inesperada ({type(e).__name__})") from None
        return last_value(frames, spec, scale)
