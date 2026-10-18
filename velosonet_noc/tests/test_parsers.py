"""ATENÇÃO: as respostas abaixo são SINTÉTICAS (formato genérico Huawei), usadas só para testar a lógica.
Substitua/complemente por respostas reais capturadas com `python -m velosonet capture`."""
import pytest
from velosonet.mml_parser import parse_mml, validate, extract_users
from velosonet.errors import *

TABULAR = """+++ SYNTH  2026-01-01 08:00:00
O&M #1
%%DSP XYZ:;%%
RETCODE = 0  Operation succeeded.

Display Test
------------
Cell ID  Users
-------  -----
0        5
1        7
(Number of results = 2)
---    END
"""
VERTICAL = """RETCODE = 0  Operation succeeded.

Cell ID  =  0
Users  =  5

Cell ID  =  1
Users  =  7
(Number of results = 2)
---    END
"""
DUP = TABULAR.replace("1        7", "0        5").replace("= 2", "= 2")

def spec(**k): return dict(mode="field_sum", field="Users", dedupe_by=[], cell_key="Cell ID", **k)

def test_tabular_sum():
    m = extract_users(validate(parse_mml(TABULAR)), spec()); assert m.total == 12 and m.per_cell == {"0": 5, "1": 7}
def test_vertical_sum():
    assert extract_users(validate(parse_mml(VERTICAL)), spec()).total == 12
def test_dedupe_avoids_double_count():
    s = spec(); s["dedupe_by"] = ["Cell ID"]
    assert extract_users(validate(parse_mml(DUP)), s).total == 5
def test_unconfigured_never_returns_zero():
    with pytest.raises(ParserNotConfigured): extract_users(validate(parse_mml(TABULAR)), {"mode": "unconfigured"})
def test_retcode_error():
    with pytest.raises(MmlError): validate(parse_mml("RETCODE = 1  Failed\n---    END"))
def test_truncated():
    with pytest.raises(MmlIncomplete): validate(parse_mml(TABULAR.replace("---    END", "")))
def test_count_mismatch():
    with pytest.raises(MmlIncomplete): validate(parse_mml(TABULAR.replace("= 2", "= 3")))
def test_missing_field():
    with pytest.raises(MmlIncomplete): extract_users(validate(parse_mml(TABULAR)), dict(spec(), field="Nope"))
def test_read_only_guard():
    from velosonet.bbu_session import assert_read_only
    assert_read_only("DSP S1INTERFACE")
    for bad in ("MOD CELL", "RST BRD", "DSP X; RST BRD", "ADD X"):
        with pytest.raises(UnsafeCommand): assert_read_only(bad)

# --- Estrutura do formato REAL visto na captura da BBU 1 (colunas truncadas; valores 0 e 522 vieram da tela) ---
def _real_like():
    cols = [("S1 Interface ID", 18), ("S1 Interface CP Bearer ID", 28), ("CN Operator ID", 16), ("MME Release", 14),
            ("S1 Interface Is Blocked", 26), ("Control Mode", 14), ("S1 Interface User Number", 28), ("MME Name", 0)]
    row = lambda *v: "".join(str(x).ljust(w) for x, (_, w) in zip(v, cols)).rstrip()
    head = "".join(n.ljust(w) for n, w in cols).rstrip()
    return ("+++    AM_TFE_01        2026-10-18 12:01:29\nO&M    #806355258\n%%DSP S1INTERFACE:;%%\n"
            "RETCODE = 0  Operation succeeded.\n\nDisplay S1 Interface Information\n"
            "--------------------------------\n" + head + "\n" +
            row(40000, 70000, 0, "Release 8", "TRUE", "AUTO_MODE", 0, "NULL") + "\n" +
            row(40001, 70011, 0, "Release 8", "FALSE", "AUTO_MODE", 522, "mme-tefe.epc.mnc099.mcc724.3gppnetwork.org") +
            "\n(Number of results = 2)\n\n\n---    END\n")

S1_SPEC = dict(mode="field_sum", field="S1 Interface User Number", dedupe_by=["S1 Interface ID"], cell_key="S1 Interface ID")

def test_real_like_s1interface():
    res = validate(parse_mml(_real_like()))
    assert len(res.records) == 2 and res.records[1]["MME Release"] == "Release 8"
    m = extract_users(res, S1_SPEC)
    assert m.total == 522 and m.per_cell == {"40000": 0, "40001": 522}

def test_slice_response_and_command_echo():
    from velosonet.bbu_session import slice_response
    noisy = "menu...\nCommand History: DSP S1INTERFACE\n" + _real_like() + "\nrodapé"
    r = slice_response(noisy, "DSP S1INTERFACE")
    assert r.startswith("+++") and "RETCODE = 0" in r

# --- DSP NRCELLUENUMBER: estrutura e números vistos na captura da BBU 1 (2 tabelas com as mesmas 3 células) ---
def _nr_like():
    def table(title, cols, rows, n):
        row = lambda v: "".join(str(x).ljust(w) for x, (_, w) in zip(v, cols)).rstrip()
        head = "".join(nm.ljust(w) for nm, w in cols).rstrip()
        return f"{title}\n{'-'*len(title)}\n{head}\n" + "\n".join(row(r) for r in rows) + f"\n(Number of results = {n})\n\n"
    c1 = [("NR Cell ID", 12), ("Cell Name", 20), ("Other RRC Number", 20), ("Total RRC Number", 20), ("EN-DC UE Number", 0)]
    r1 = [(591, "AM_TFE_NR_N78_S1", 9, 9, 9), (592, "AM_TFE_NR_N78_S2", 10, 10, 10), (593, "AM_TFE_NR_N78_S3", 12, 12, 12)]
    c2 = [("NR Cell ID", 12), ("Operator ID", 14), ("Cell Name", 20), ("Operator Name", 16), ("Total RRC Number", 0)]
    r2 = [(591, 0, "AM_TFE_NR_N78_S1", "VELOSO 5G", 9), (592, 0, "AM_TFE_NR_N78_S2", "VELOSO 5G", 10), (593, 0, "AM_TFE_NR_N78_S3", "VELOSO 5G", 12)]
    return ("+++    AM_TFE_01        2026-10-18 12:08:51\nO&M    #2686451803\n%%DSP NRCELLUENUMBER:;%%\n"
            "RETCODE = 0  Operation succeeded.\n\n" + table("Display NR Cell UE Access Statistics", c1, r1, 3) +
            table("Display NR Cell Operator UE Access Statistics", c2, r2, 3) + "\n---    END\n")

NR_SPEC = dict(mode="field_sum", table_title="Display NR Cell UE Access Statistics", field="Total RRC Number",
               dedupe_by=["NR Cell ID"], cell_key="NR Cell ID")

def test_nr_two_tables_no_double_count():
    res = validate(parse_mml(_nr_like()))
    assert [len(t.records) for t in res.tables] == [3, 3]
    m = extract_users(res, NR_SPEC)
    assert m.total == 31 and m.per_cell == {"591": 9, "592": 10, "593": 12}      # 62 seria dupla contagem

def test_nr_wrong_table_title_fails():
    with pytest.raises(MmlIncomplete): extract_users(validate(parse_mml(_nr_like())), dict(NR_SPEC, table_title="Outra"))

def test_nr_truncated_second_table_detected():
    with pytest.raises(MmlIncomplete): validate(parse_mml(_nr_like().replace("= 3)\n\n\n---", "= 4)\n\n\n---")))
