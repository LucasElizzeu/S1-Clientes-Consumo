"""Cliente Grafana via API oficial: lê o dashboard, localiza o painel pelo TÍTULO (conferindo o id esperado),
reutiliza os targets e consulta /api/ds/query. Retorna o ÚLTIMO ponto numérico real da série, convertido para
Mb/s pela unidade configurada no próprio painel (nada de estimar valores olhando o gráfico)."""
from __future__ import annotations
import copy, logging, math, os, re, time, unicodedata
try:
    import httpx
except ImportError:          # permite testar a lógica pura sem httpx instalado
    httpx = None
from .errors import GrafanaError, ConfigError

log = logging.getLogger(__name__)

# unidades de TAXA do Grafana -> fator para Mb/s
UNIT_SCALE = {"bps": 1e-6, "Kbits": 1e-3, "Mbits": 1.0, "Gbits": 1e3,
              "Bps": 8e-6, "KBs": 8e-3, "MBs": 8.0, "GBs": 8e3}

def _norm(t):
    text = unicodedata.normalize("NFKD", t or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]", "", text.lower())

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

def _is_dashboard_source(ds):
    return ds == "-- Dashboard --" or (
        isinstance(ds, dict) and ds.get("uid") == "-- Dashboard --"
    )


def _dashboard_source(dash, panel):
    """Resolve the supported Dashboard card without guessing its selected series."""
    targets = panel.get("targets") or []
    if not any(_is_dashboard_source(t.get("datasource") or panel.get("datasource"))
               for t in targets):
        return None
    if len(targets) != 1 or targets[0].get("panelId") is None:
        raise GrafanaError("Dashboard: esperado um unico painel de origem")
    transforms = [t for t in panel.get("transformations", []) if not t.get("disabled")]
    if len(transforms) != 1 or transforms[0].get("id") != "filterFieldsByName":
        raise GrafanaError("Dashboard: transformacoes do cartao nao suportadas")
    opts = transforms[0].get("options") or {}
    include = opts.get("include") or {}
    names = include.get("names") or []
    if len(names) != 1 or include.get("pattern") or opts.get("exclude"):
        raise GrafanaError("Dashboard: esperado filtro com um nome exato de serie")
    reduce = (panel.get("options") or {}).get("reduceOptions") or {}
    if reduce.get("calcs") != ["last"] or reduce.get("values") or reduce.get("fields"):
        raise GrafanaError("Dashboard: redutor do cartao nao suportado (esperado last)")
    source = resolve_panel(dash, {"panel_id": targets[0]["panelId"]})
    if any(not t.get("disabled") for t in source.get("transformations", [])):
        raise GrafanaError("Dashboard: painel de origem possui transformacoes nao suportadas")
    if any(_is_dashboard_source(t.get("datasource") or source.get("datasource"))
           for t in source.get("targets", [])):
        raise GrafanaError("Dashboard: referencias encadeadas nao suportadas")
    return source, names[0]


def _card_value(results, queries, name, spec, scale):
    """Select one named numeric field; never fall back to the first city."""
    matches, available = [], set()
    for query in queries:
        result = results.get(query.get("refId"))
        if not isinstance(result, dict):
            raise GrafanaError(f"Dashboard: resultado ausente para {query.get('refId')}")
        if result.get("error") or result.get("status", 200) >= 400:
            raise GrafanaError(f"Dashboard: consulta {query.get('refId')} falhou")
        for fr in result.get("frames") or []:
            schema = fr.get("schema") or {}
            fields = schema.get("fields") or []
            values = (fr.get("data") or {}).get("values") or []
            numbers = [i for i, f in enumerate(fields) if f.get("type") == "number"]
            times = [i for i, f in enumerate(fields) if f.get("type") == "time"]
            for i in numbers:
                field = fields[i]
                config = field.get("config") or {}
                candidates = {x for x in (field.get("name"), config.get("displayNameFromDS"),
                                          config.get("displayName")) if isinstance(x, str)}
                if len(numbers) == 1 and isinstance(schema.get("name"), str):
                    candidates.add(schema["name"])
                available.update(candidates)
                # The Zabbix API may expose "host: item" before the browser
                # applies setAlias($__zbx_item_name). Remove only the exact
                # literal host from this query, never an arbitrary prefix.
                host = (query.get("host") or {}).get("filter")
                if isinstance(host, str) and host and not host.startswith("/"):
                    prefix = host + ": "
                    candidates.update(x[len(prefix):] for x in list(candidates)
                                      if x.startswith(prefix))
                # Zabbix aliases can be applied by its browser plugin rather than the API.
                functions = query.get("functions") or []
                if all((f.get("def") or {}).get("name") == "setAlias" for f in functions):
                    for func in functions:
                        params = func.get("params") or []
                        if len(params) == 1 and "$__zbx_item_name" in str(params[0]):
                            candidates.update(str(params[0]).replace("$__zbx_item_name", x)
                                              for x in list(candidates))
                if name not in candidates:
                    continue
                if any((f.get("def") or {}).get("name") != "setAlias" for f in functions):
                    raise GrafanaError("Dashboard: funcao da serie selecionada nao suportada")
                if len(times) != 1 or max(i, times[0]) >= len(values):
                    raise GrafanaError("Dashboard: estrutura da serie sem tempo/valor")
                ts, vs = values[times[0]], values[i]
                if not ts or len(ts) != len(vs):
                    raise GrafanaError("Dashboard: serie vazia ou incompleta")
                if any(isinstance(t, bool) or not isinstance(t, (int, float)) or not math.isfinite(t)
                       for t in ts):
                    raise GrafanaError("Dashboard: timestamp invalido")
                index = max(range(len(ts)), key=ts.__getitem__)
                matches.append((ts[index], vs[index]))
    if len(matches) != 1:
        shown = ", ".join(sorted(available)[:12])
        raise GrafanaError(f"serie '{name}': {len(matches)} correspondencias (esperado 1); campos: {shown}")
    stamp, value = matches[0]
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise GrafanaError(f"serie '{name}': ultima leitura sem valor numerico")
    if value < 0:
        raise GrafanaError(f"serie '{name}': leitura indisponivel (codigo {value})")
    age = time.time() - stamp / 1000
    if age > spec.get("max_age_s", 900) or age < -60:
        raise GrafanaError(f"serie '{name}': timestamp fora da validade ({int(age)}s)")
    return {"mbps": float(value) * scale, "ts": stamp / 1000, "metric": spec["metric"]}


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
            shared = _dashboard_source(dash, panel)
            query_panel = shared[0] if shared else panel
            targets = query_panel.get("targets", [])
            visible = [t for t in targets if not t.get("hide")]
            ref = spec.get("ref_id") or (visible[0].get("refId") if visible else None)
            queries = []
            for t in targets:
                q = _subst(copy.deepcopy(t), spec.get("variables") or {})
                q["hide"] = False
                if not q.get("datasource"):
                    q["datasource"] = query_panel.get("datasource") or dash.get("datasource")
                q.update(intervalMs=60000, maxDataPoints=500)
                queries.append(q)
            if not queries: raise GrafanaError("nenhum target utilizável no painel")
            r = await self.http.post("/api/ds/query", json={"queries": queries, "from": "now-30m", "to": "now"})
            r.raise_for_status()
            if shared:
                return _card_value(r.json()["results"], queries, shared[1], spec, scale)
            frames = r.json()["results"][ref]["frames"]
        except httpx.HTTPStatusError as e:
            request = e.response.request
            raise GrafanaError(
                f"HTTP {e.response.status_code} {request.method} {request.url.path}"
            ) from None
        except httpx.HTTPError as e:
            raise GrafanaError(f"falha de transporte HTTP: {type(e).__name__}") from None
        except (KeyError, StopIteration, IndexError) as e:
            raise GrafanaError(f"resposta inesperada ({type(e).__name__})") from None
        return last_value(frames, spec, scale)
