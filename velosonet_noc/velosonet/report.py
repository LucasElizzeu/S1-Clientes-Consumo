"""Gerador do PNG (padrão NOC) e do resumo operacional."""
from __future__ import annotations
from datetime import datetime
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

NAVY, BG, GRID, TXT, MUTED = (11, 42, 91), (244, 247, 251), (205, 214, 226), (24, 32, 48), (120, 130, 145)
LEVEL = {"green": ((34, 160, 84), "Normal"), "yellow": ((240, 173, 0), "Alerta"), "red": ((214, 48, 49), "Falha")}
FONTS = ["DejaVuSans{b}.ttf", "C:/Windows/Fonts/segoeui{w}.ttf", "C:/Windows/Fonts/arial{w}.ttf",
         "/usr/share/fonts/truetype/dejavu/DejaVuSans{b}.ttf", "/usr/share/fonts/truetype/liberation/LiberationSans-{R}.ttf"]

def font(size, bold=False):
    for f in FONTS:
        try:
            return ImageFont.truetype(f.format(b="-Bold" if bold else "", w="b" if bold else "", R="Bold" if bold else "Regular"), size)
        except OSError:
            continue
    return ImageFont.load_default(size)

def level_of(row, settings) -> str:
    if row["state"] in ("unavailable", "failed"): return "red"
    if row["state"] == "partial": return "yellow"
    thr = settings.get("alert_min_traffic_mbps")
    if thr is not None and row["traffic"] is not None and row["traffic"] < thr: return "yellow"
    return "green"

def _cell(row, key):
    v = row[key]
    if v == "N/A": return "N/A", MUTED
    if v is None: return ("INDISP." if row["state"] == "unavailable" else "FALHA"), LEVEL["red"][0]
    if key == "traffic": return f"{v:,.1f}".replace(",", " "), TXT
    return str(v), TXT

def build_summary(rows, settings, ts: datetime) -> dict:
    ok = sum(1 for r in rows if r["state"] == "ok")
    def tot(k):
        vals = [r[k] for r in rows if isinstance(r[k], (int, float))]
        missing = any(r[k] is None for r in rows)
        return (sum(vals) if vals else None), missing
    u4, m4 = tot("users4g"); u5, m5 = tot("users5g"); tr, mt = tot("traffic")
    levels = [level_of(r, settings) for r in rows]
    overall = "red" if levels.count("red") >= 3 else ("yellow" if "red" in levels or "yellow" in levels else "green")
    return dict(total=len(rows), ok=ok, u4=u4, m4=m4, u5=u5, m5=m5, tr=tr, mt=mt, overall=overall, ts=ts,
                sum45=bool(settings.get("sum_4g_5g")))

def caption(s, simulation=False) -> str:
    f = lambda v, m, u="": ("n/d" if v is None else (f"{v:,.1f}".replace(",", " ") if isinstance(v, float) else str(v)) + u) + (" (parcial)" if m and v is not None else "")
    head = "⚠️ SIMULAÇÃO\n" if simulation else ""
    return (f"{head}📊 4G VELOSONET (TEFÉ) — {s['ts']:%d/%m/%Y %H:%M}\n"
            f"Usuários 4G: {f(s['u4'], s['m4'])} | 5G: {f(s['u5'], s['m5'])}\n"
            f"Consumo geral: {f(s['tr'], s['mt'], ' Mb/s')}\nColeta OK: {s['ok']}/{s['total']}")

def _c(d, box, text, fnt, fill):
    x0, y0, x1, y1 = box; l, t, r, b = d.textbbox((0, 0), text, font=fnt)
    d.text((x0 + (x1 - x0 - (r - l)) / 2 - l, y0 + (y1 - y0 - (b - t)) / 2 - t), text, font=fnt, fill=fill)

def render_report(rows, settings, ts: datetime, out_path: Path, simulation=False) -> Path:
    W, M = 1600, 40
    S = build_summary(rows, settings, ts)
    H = 1640
    img = Image.new("RGB", (W, H), BG); d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 170], fill=NAVY)
    d.text((M, 28), "PLANILHA CONSOLIDADA – 4G VELOSONET (TEFÉ)", font=font(50, True), fill="white")
    d.text((M, 100), f"Coleta: {ts:%d/%m/%Y  %H:%M}  (America/Manaus)", font=font(34), fill=(190, 208, 240))
    if simulation:
        d.rectangle([0, 170, W, 215], fill=LEVEL["red"][0])
        _c(d, (0, 170, W, 215), "SIMULAÇÃO — DADOS FICTÍCIOS, NÃO REPRESENTAM A REDE", font(28, True), "white")
    top = 240
    cols = [("Site", .16), ("Localidade", .24), ("Usuários 4G", .14), ("Usuários 5G", .14), ("Consumo (Mb/s)", .18), ("Status", .12)]
    tw = W - 2 * M; xs = [M]
    for _, f in cols: xs.append(xs[-1] + tw * f)
    d.rectangle([M, top, W - M, top + 70], fill=NAVY)
    for i, (n, _) in enumerate(cols):
        _c(d, (xs[i], top, xs[i + 1], top + 70), n, font(26, True), "white")
    y = top + 70
    for k, r in enumerate(rows):
        d.rectangle([M, y, W - M, y + 96], fill=(255, 255, 255) if k % 2 == 0 else (236, 241, 248), outline=GRID)
        d.text((xs[0] + 18, y + 28), r["site"], font=font(32, True), fill=TXT)
        d.text((xs[1] + 18, y + 28), r["locality"], font=font(32), fill=TXT)
        for i, key in ((2, "users4g"), (3, "users5g"), (4, "traffic")):
            t, col = _cell(r, key); _c(d, (xs[i], y, xs[i + 1], y + 96), t, font(36, True), col)
        color, lab = LEVEL[level_of(r, settings)]
        if r["state"] == "unavailable": lab = "Indisp."
        cx = xs[5] + 28; d.ellipse([cx, y + 30, cx + 36, y + 66], fill=color)
        d.text((cx + 50, y + 28), lab, font=font(28, True), fill=TXT)
        y += 96
    y += 34
    d.text((M, y), "RESUMO OPERACIONAL", font=font(38, True), fill=NAVY); y += 62
    def f(v, m, u=""):
        if v is None: return "n/d"
        s = (f"{v:,.1f}".replace(",", " ") if isinstance(v, float) else str(v)) + u
        return s + ("  (parcial)" if m else "")
    items = [("Total de BBUs monitoradas", str(S["total"])), ("BBUs com coleta bem-sucedida", f"{S['ok']} / {S['total']}"),
             ("Total de usuários 4G", f(S["u4"], S["m4"])), ("Total de usuários 5G", f(S["u5"], S["m5"])),
             ("Consumo geral", f(S["tr"], S["mt"], " Mb/s")),
             ("Última atualização", f"{ts:%d/%m/%Y %H:%M}"), ("Status geral", LEVEL[S["overall"]][1])]
    for i, (a, b) in enumerate(items):
        yy = y + i * 60
        d.text((M, yy), a, font=font(30), fill=MUTED); 
        if a == "Status geral":
            d.ellipse([M + 640, yy + 4, M + 676, yy + 40], fill=LEVEL[S["overall"]][0]); d.text((M + 690, yy), b, font=font(32, True), fill=TXT)
        else:
            d.text((M + 640, yy), b, font=font(32, True), fill=TXT)
    d.text((M, H - 50), "Legenda: FALHA = consulta falhou · INDISP. = BBU inacessível · N/A = sem 5G · 0 = sem usuários",
           font=font(24), fill=MUTED)
    out_path = Path(out_path); out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, "PNG", optimize=True)
    return out_path
