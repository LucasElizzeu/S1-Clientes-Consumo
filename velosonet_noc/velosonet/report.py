"""Gerador do PNG (padrão NOC) e do resumo operacional."""
from __future__ import annotations
from datetime import datetime
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

NAVY, BG, GRID, TXT, MUTED = (18, 43, 73), (9, 15, 25), (43, 62, 83), (232, 240, 248), (152, 173, 196)
LEVEL = {"green": ((53, 201, 124), "Normal"), "yellow": ((247, 186, 65), "Alerta"), "red": ((255, 101, 111), "Falha")}
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
    scale = int(settings.get("report_scale", 2))
    if not 1 <= scale <= 4:
        raise ValueError("report_scale deve estar entre 1 e 4")
    def u(value): return round(value * scale)
    def ft(size, bold=False): return font(u(size), bold)

    W, M = u(1600), u(40)
    top, header_h, row_h = u(240), u(70), u(96)
    summary_top = top + header_h + len(rows) * row_h + u(34)
    summary_header, summary_row = u(66), u(62)
    summary_bottom = summary_top + summary_header + 7 * summary_row + u(12)
    H = summary_bottom + u(40)
    S = build_summary(rows, settings, ts)
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, W, u(170)), fill=NAVY)
    title = "PLANILHA 4G VELOSONET (TEFÉ)"
    title_size = 48
    while d.textbbox((0, 0), title, font=ft(title_size, True))[2] > W - 2 * M and title_size > 24:
        title_size -= 1
    _c(d, (0, 0, W, u(170)), title, ft(title_size, True), "white")
    if simulation:
        d.rectangle((0, u(170), W, u(215)), fill=LEVEL["red"][0])
        _c(d, (0, u(170), W, u(215)),
           "SIMULADO ? DADOS FICTÍCIOS, NÃO REPRESENTAM A REDE",
           ft(28, True), "white")

    cols = [("Site", .16), ("Localidade", .24), ("Usuários 4G", .14),
            ("Usuários 5G", .14), ("Consumo (Mb/s)", .18), ("Status", .12)]
    total_width = sum(part for _, part in cols)
    xs = [M]
    for _, part in cols:
        xs.append(xs[-1] + (W - 2 * M) * part / total_width)
    xs[-1] = W - M
    d.rectangle((M, top, W - M, top + header_h), fill=NAVY)
    for i, (name, _) in enumerate(cols):
        _c(d, (xs[i], top, xs[i + 1], top + header_h), name, ft(26, True), "white")
    y = top + header_h
    for index, row in enumerate(rows):
        background = (19, 29, 45) if index % 2 == 0 else (24, 38, 57)
        d.rectangle((M, y, W - M, y + row_h),
                    fill=background, outline=GRID, width=u(1))
        _c(d, (xs[0], y, xs[1], y + row_h), row["site"], ft(30, True), TXT)
        _c(d, (xs[1], y, xs[2], y + row_h), row["locality"], ft(30), TXT)
        for i, key in ((2, "users4g"), (3, "users5g"), (4, "traffic")):
            text, color = _cell(row, key)
            _c(d, (xs[i], y, xs[i + 1], y + row_h), text, ft(36, True), color)
        color, label = LEVEL[level_of(row, settings)]
        if row["state"] == "unavailable":
            label = "Indisp."
        status_font = ft(26, True)
        box = d.textbbox((0, 0), label, font=status_font)
        label_width = box[2] - box[0]
        diameter, gap = u(28), u(10)
        left = xs[5] + (xs[6] - xs[5] - diameter - gap - label_width) / 2
        cy = y + row_h / 2
        d.ellipse((left, cy - diameter / 2, left + diameter, cy + diameter / 2),
                  fill=color)
        _c(d, (left + diameter + gap, y,
               left + diameter + gap + label_width, y + row_h),
           label, status_font, TXT)
        y += row_h

    d.rectangle((M, summary_top, W - M, summary_bottom),
                fill=(19, 29, 45), outline=(48, 75, 108), width=u(2))
    d.rectangle((M, summary_top, W - M, summary_top + summary_header),
                fill=(26, 62, 105))
    d.text((M + u(20), summary_top + u(12)), "RESUMO OPERACIONAL",
           font=ft(34, True), fill="white")

    def fmt(value, missing=False, unit=""):
        if value is None:
            return "n/d"
        value = f"{value:,.1f}".replace(",", " ") if isinstance(value, float) else str(value)
        return value + unit + (" (parcial)" if missing else "")

    items = [
        ("Total de BBUs monitoradas", str(S["total"])),
        ("BBUs com coleta bem-sucedida", f"{S['ok']} / {S['total']}"),
        ("Total de usuários 4G", fmt(S["u4"], S["m4"])),
        ("Total de usuários 5G", fmt(S["u5"], S["m5"])),
        ("Consumo geral", fmt(S["tr"], S["mt"], " Mb/s")),
        ("Atualização", f"{ts:%d/%m/%Y %H:%M}"),
        ("Status geral", LEVEL[S["overall"]][1]),
    ]
    y = summary_top + summary_header + u(6)
    for index, (label, value) in enumerate(items):
        yy = y + index * summary_row
        if index % 2 == 0:
            d.rectangle((M + u(2), yy, W - M - u(2), yy + summary_row),
                        fill=(24, 39, 59))
        d.text((M + u(20), yy + u(12)), label,
               font=ft(28, True), fill=TXT)
        value_x = M + u(660)
        if label == "Status geral":
            diameter = u(28)
            d.ellipse((value_x, yy + u(17), value_x + diameter,
                       yy + u(17) + diameter), fill=LEVEL[S["overall"]][0])
            value_x += u(42)
        d.text((value_x, yy + u(10)), value, font=ft(32, True), fill=(102, 185, 255))

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, "PNG", optimize=True)
    return out_path
