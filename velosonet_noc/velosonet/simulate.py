"""Modo simulação: gera o PNG com dados FICTÍCIOS (marcados na imagem). Não acessa BBUs, Grafana nem Telegram."""
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
from .report import render_report, caption, build_summary

def fake_rows():
    mk = lambda i, s, l, u4, u5, tr, st="ok", er=(): dict(bbu_id=i, site=s, locality=l, users4g=u4, users5g=u5,
                                                          traffic=tr, state=st, errors=list(er))
    return [mk(1, "AM_TFE_01", "Torre Claro", 128, 14, 310.4),
            mk(2, "AM_TFE_02", "São Miguel", 0, "N/A", 0.0),                      # 0 legítimo (sem usuários)
            mk(3, "AM_TFE_03", "Torre Tim", 96, None, 220.7, "partial", ["users_5g:query_failed"]),
            mk(4, "AM_TFE_04", "Torre Boa Sorte", None, None, None, "unavailable", ["login"]),
            mk(5, "AM_TFE_05", "Aeroporto", 41, "N/A", 55.2),
            mk(6, "AM_TFE_06", "Abial", 22, "N/A", None, "partial", ["grafana"])]

def run(settings, out: Path):
    now = datetime.now(ZoneInfo(settings["timezone"]))
    rows = fake_rows()
    png = render_report(rows, settings, now, out, simulation=True)
    return png, caption(build_summary(rows, settings, now), simulation=True)
