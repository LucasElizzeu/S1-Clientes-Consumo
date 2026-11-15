"""Gerador do PNG (padrão NOC) e do resumo operacional."""
from __future__ import annotations
from datetime import datetime
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

NAVY, BG, GRID, TXT, MUTED = (20, 20, 20), (0, 0, 0), (55, 55, 55), (240, 240, 240), (160, 160, 160)
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

    def u(value):
        return round(value * scale)

    def ft(size, bold=False):
        return font(u(size), bold)

    black = (0, 0, 0)
    white = (240, 240, 240)
    muted = (160, 160, 160)
    green = LEVEL["green"][0]
    border = (55, 55, 55)

    W, M = u(1100), u(40)
    top = u(210 if simulation else 165)
    header_h, row_h = u(62), u(78)
    table_bottom = top + header_h + len(rows) * row_h
    heading_top = table_bottom + u(34)
    cards_top = heading_top + u(76)
    card_h, gap = u(142), u(22)
    cards_bottom = cards_top + 3 * card_h + 2 * gap
    H = cards_bottom + u(100)

    S = build_summary(rows, settings, ts)
    img = Image.new("RGB", (W, H), black)
    d = ImageDraw.Draw(img)

    def fitted(text, size, width, bold=False):
        while size > 14:
            box = d.textbbox((0, 0), str(text), font=ft(size, bold))
            if box[2] - box[0] <= width:
                break
            size -= 1
        return ft(size, bold)

    def centered(box, text, size, color=white, bold=False):
        text = str(text)
        fnt = fitted(text, size, box[2] - box[0] - u(20), bold)
        _c(d, box, text, fnt, color)

    title = "PLANILHA 4G VELOSONET (TEF\u00c9)"
    centered((M, u(20), W-M, u(118)), title, 44, bold=True)
    d.rectangle((M, u(132), W-M, u(136)), fill=green)

    if simulation:
        d.rounded_rectangle(
            (M, u(151), W-M, u(191)),
            radius=u(6), fill=(90, 28, 34)
        )
        centered(
            (M, u(151), W-M, u(191)),
            "SIMULA\u00c7\u00c3O - DADOS FICT\u00cdCIOS",
            23, bold=True
        )

    columns = [
        ("Site", .18), ("Localidade", .29),
        ("4G", .10), ("5G", .10),
        ("Mb/s", .15), ("Status", .18)
    ]
    xs = [M]
    for _, part in columns:
        xs.append(xs[-1] + (W - 2*M) * part)
    xs[-1] = W-M

    d.rounded_rectangle(
        (M, top, W-M, table_bottom),
        radius=u(16), fill=(15, 15, 15)
    )
    d.rectangle((M, top, W-M, top+header_h), fill=(32, 32, 32))

    for i, (label, _) in enumerate(columns):
        centered(
            (xs[i], top, xs[i+1], top+header_h),
            label, 25, bold=True
        )

    y = top + header_h
    for index, row in enumerate(rows):
        background = (12, 12, 12) if index % 2 == 0 else (24, 24, 24)
        d.rectangle((M, y, W-M, y+row_h), fill=background)

        centered((xs[0], y, xs[1], y+row_h), row["site"], 25)
        centered((xs[1], y, xs[2], y+row_h), row["locality"], 27)

        for i, key in ((2, "users4g"), (3, "users5g"), (4, "traffic")):
            text, color = _cell(row, key)
            if key == "traffic" and isinstance(row[key], (int, float)):
                text = f"{row[key]:.1f}".replace(".", ",")
            centered(
                (xs[i], y, xs[i+1], y+row_h),
                text, 28, color, bold=True
            )

        color, label = LEVEL[level_of(row, settings)]
        if row["state"] == "unavailable":
            label = "Indisp."

        status_font = fitted(label, 25, xs[6]-xs[5]-u(58), True)
        bounds = d.textbbox((0, 0), label, font=status_font)
        width = bounds[2] - bounds[0]
        diameter, spacing = u(20), u(12)
        left = xs[5] + (xs[6]-xs[5]-width-diameter-spacing)/2
        cy = y + row_h/2
        d.ellipse(
            (left, cy-diameter/2, left+diameter, cy+diameter/2),
            fill=color
        )
        _c(
            d,
            (left+diameter+spacing, y,
             left+diameter+spacing+width, y+row_h),
            label, status_font, white
        )
        y += row_h


    # Arredonda a tabela inteira, incluindo cabecalho e ultima linha.
    table_box = (int(M), int(top), int(W-M), int(table_bottom))
    table_image = img.crop(table_box)
    mask = Image.new("L", table_image.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, table_image.width-1, table_image.height-1),
        radius=u(18), fill=255
    )
    d.rectangle(table_box, fill=black)
    img.paste(table_image, table_box[:2], mask)

    heading = "RESUMO OPERACIONAL"
    heading_font = fitted(heading, 30, W-2*M-u(260), True)
    bounds = d.textbbox((0, 0), heading, font=heading_font)
    heading_width = bounds[2]-bounds[0]
    center_y = heading_top + u(32)
    side_end = (W-heading_width)/2-u(26)

    if side_end > M:
        d.line((M, center_y, side_end, center_y), fill=green, width=u(3))
        d.line(
            (W-side_end, center_y, W-M, center_y),
            fill=green, width=u(3)
        )
    _c(
        d, (M, heading_top, W-M, heading_top+u(64)),
        heading, heading_font, white
    )

    def fmt(value, missing=False, unit=""):
        if value is None:
            return "n/d"
        if isinstance(value, float):
            text = f"{value:,.1f}".replace(",", "_").replace(".", ",").replace("_", ".")
        else:
            text = f"{value:,}".replace(",", ".")
        return text + unit + (" (parcial)" if missing else "")

    items = [
        ("BBUs monitoradas", str(S["total"]), green),
        ("Coleta OK", f"{S['ok']} / {S['total']}", green),
        ("Usu\u00e1rios 4G", fmt(S["u4"], S["m4"]), green),
        ("Usu\u00e1rios 5G", fmt(S["u5"], S["m5"]), green),
        ("Consumo geral", fmt(S["tr"], S["mt"], " Mb/s"), green),
        ("Status geral", LEVEL[S["overall"]][1], LEVEL[S["overall"]][0]),
    ]

    card_w = (W-2*M-gap)/2
    for index, (label, value, color) in enumerate(items):
        left = M + (index % 2)*(card_w+gap)
        yy = cards_top + (index // 2)*(card_h+gap)
        right = left+card_w

        d.rounded_rectangle(
            (left, yy, right, yy+card_h),
            radius=u(24), fill=(24, 24, 24),
            outline=border, width=u(1)
        )
        d.text(
            (left+u(26), yy+u(18)),
            label, font=ft(24, True), fill=white
        )
        value_font = fitted(value, 44, card_w-u(52), True)
        d.text(
            (left+u(26), yy+u(64)), value,
            font=value_font,
            fill=muted if value == "n/d" else color
        )

    centered(
        (M, cards_bottom+u(28), W-M, cards_bottom+u(78)),
        f"\u00daltima atualiza\u00e7\u00e3o: {ts:%d/%m/%Y %H:%M}",
        24
    )

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, "PNG", optimize=True)
    return out_path
