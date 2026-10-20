"""Parser de respostas MML Huawei (formato genérico). Valida RETCODE, contagem declarada e marcador END.
Os nomes de campo de usuários NÃO são presumidos: vêm de config/settings.yaml após validar resposta real."""
from __future__ import annotations
import re
from dataclasses import dataclass, field
from .errors import MmlError, MmlIncomplete, ParserNotConfigured

RETCODE_RE = re.compile(r"RETCODE\s*=\s*(-?\d+)\s*(.*)")
COUNT_RE = re.compile(r"\(Number of results\s*=\s*(\d+)\)", re.I)
END_RE = re.compile(r"^\s*---\s+END\s*$", re.I | re.M)
KV_RE = re.compile(r"^\s*(.+?)\s*=\s*(.*?)\s*$")

@dataclass
class Table:
    title: str
    records: list
    declared: int | None = None

@dataclass
class MmlResult:
    retcode: int | None
    message: str
    records: list[dict] = field(default_factory=list)
    declared_count: int | None = None
    has_end: bool = False
    tables: list = field(default_factory=list)   # respostas com várias tabelas (ex.: DSP NRCELLUENUMBER)

def _tabular(lines):
    for i in range(1, len(lines)):
        if re.fullmatch(r"\s*-{2,}(\s+-{2,})+\s*", lines[i]) and "=" not in lines[i - 1]:
            header, dash = lines[i - 1], lines[i]
            spans = [m.span() for m in re.finditer(r"-+", dash)]
            names = []
            for n, (a, b) in enumerate(spans):
                end = spans[n + 1][0] if n + 1 < len(spans) else None
                names.append(header[a:end].strip())
            recs = []
            for ln in lines[i + 1:]:
                if not ln.strip() or COUNT_RE.search(ln) or END_RE.search(ln):
                    break
                vals = []
                for n, (a, b) in enumerate(spans):
                    end = spans[n + 1][0] if n + 1 < len(spans) else None
                    vals.append(ln[a:end].strip())
                recs.append(dict(zip(names, vals)))
            return recs
    return []

DASH1_RE = re.compile(r"^\s*-{5,}\s*$")

def _huawei_tables(lines):
    """Formato real: título / linha de '-----' / cabeçalho / linhas / '(Number of results = N)'.
    Colunas separadas por 2+ espaços; valores alinhados à esquerda na posição do cabeçalho.
    Pode haver VÁRIAS tabelas na mesma resposta."""
    tables, i = [], 0
    while i < len(lines) - 2:
        if not DASH1_RE.match(lines[i]): i += 1; continue
        header = lines[i + 1]
        if not header.strip() or " = " in header or header.strip().startswith(("-", "(")): i += 1; continue
        cols = [(m.start(), m.group().strip()) for m in re.finditer(r"\S+(?: \S+)*", header)]
        if len(cols) < 2: i += 1; continue
        recs, declared, j = [], None, i + 2
        while j < len(lines):
            ln = lines[j]
            cm = COUNT_RE.search(ln)
            if cm: declared = int(cm.group(1)); break
            if END_RE.search(ln): break
            if not ln.strip():
                j += 1
                continue
            values = re.split(r"\s{2,}", ln.strip())
            if len(values) == len(cols):
                recs.append(dict(zip((name for _, name in cols), values)))
                j += 1
                continue
            rec = {}
            for k, (st, name) in enumerate(cols):
                end = cols[k + 1][0] if k + 1 < len(cols) else None
                rec[name] = ln[st:end].strip()
            recs.append(rec); j += 1
        if recs:
            tables.append(Table(lines[i - 1].strip() if i else "", recs, declared))
        i = j + 1
    return tables

def _vertical(lines):
    recs, cur = [], {}
    for ln in lines:
        if not ln.strip():
            if cur: recs.append(cur); cur = {}
            continue
        if RETCODE_RE.search(ln) or ln.strip().startswith(("+++", "%%", "---", "O&M")):
            continue
        m = KV_RE.match(ln)
        if m:
            k, v = m.groups()
            if k in cur: recs.append(cur); cur = {}
            cur[k] = v
    if cur: recs.append(cur)
    return recs

def parse_mml(text: str) -> MmlResult:
    lines = text.replace("\r", "").split("\n")
    rc, msg = None, ""
    for ln in lines:
        m = RETCODE_RE.search(ln)
        if m: rc, msg = int(m.group(1)), m.group(2).strip(); break
    cm = COUNT_RE.search(text)
    recs = _tabular(lines)
    tables = [] if recs else _huawei_tables(lines)
    if tables:
        return MmlResult(rc, msg, tables[0].records, tables[0].declared, bool(END_RE.search(text)), tables)
    recs = recs or _vertical(lines)
    return MmlResult(rc, msg, recs, int(cm.group(1)) if cm else None, bool(END_RE.search(text)))

def validate(res: MmlResult) -> MmlResult:
    if res.retcode is None:
        raise MmlIncomplete("RETCODE ausente na resposta")
    if res.retcode != 0:
        raise MmlError(f"RETCODE={res.retcode} {res.message}")
    if not res.has_end:
        raise MmlIncomplete("marcador END ausente (resposta truncada?)")
    for t in (res.tables or [Table("", res.records, res.declared_count)]):
        if t.declared is not None and t.declared != len(t.records):
            raise MmlIncomplete(f"tabela '{t.title}': registros={len(t.records)} difere do declarado={t.declared}")
    return res

@dataclass
class UserMetric:
    total: int
    per_cell: dict

def _to_int(v, field):
    try: return int(str(v).strip())
    except ValueError: raise MmlIncomplete(f"valor não numérico em '{field}': {v!r}")

def extract_users(res: MmlResult, spec: dict) -> UserMetric:
    mode, fld = spec.get("mode", "unconfigured"), spec.get("field")
    if mode == "unconfigured" or not fld:
        raise ParserNotConfigured("parser não configurado: valide uma resposta real e informe 'field'")
    recs = res.records
    tt = spec.get("table_title")
    if tt:   # respostas com várias tabelas: usar SOMENTE a indicada (evita dupla contagem)
        norm = lambda x: " ".join(x.lower().split())
        sel = [t for t in res.tables if norm(t.title) == norm(tt)]
        if not sel: raise MmlIncomplete(f"tabela '{tt}' ausente na resposta")
        recs = sel[0].records
    if (fld == "Total RRC Number" and recs
            and all(fld not in r and "Cell Total RRC Number" in r for r in recs)):
        fld = "Cell Total RRC Number"
    dd = spec.get("dedupe_by") or []
    if dd:
        seen, uniq = set(), []
        for r in recs:
            k = tuple(r.get(c) for c in dd)
            if k not in seen: seen.add(k); uniq.append(r)
        recs = uniq
    if any(fld not in r for r in recs) or not recs:
        raise MmlIncomplete(f"campo '{fld}' ausente na resposta")
    ck = spec.get("cell_key")
    per = {(r.get(ck) if ck else str(i)): _to_int(r[fld], fld) for i, r in enumerate(recs)}
    if mode == "field_sum":
        total = sum(_to_int(r[fld], fld) for r in recs)
    elif mode == "field_total":
        total = _to_int(recs[0][fld], fld)
    else:
        raise ParserNotConfigured(f"mode inválido: {mode}")
    return UserMetric(total, per)
