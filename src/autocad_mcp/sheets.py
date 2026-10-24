"""Листы (рамка + основная надпись ГОСТ 2.104, форма 1) и таблицы в виде примитивов.

Всё строится как список ops (см. constructions.py), поэтому одинаково рисуется
в обоих движках. Размеры листа задаются в мм бумаги и умножаются на масштаб 1:N.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any

L_SHEET = "SHEET"
L_TABLE = "TABLE"

PAPER = {"A4": (210, 297), "A3": (297, 420), "A2": (420, 594), "A1": (594, 841)}
STD_SCALES = [1, 2, 2.5, 4, 5, 10, 15, 20, 25, 40, 50, 75, 100, 150, 200, 250, 400, 500,
              750, 1000, 2000, 2500, 5000, 10000]

TB_W, TB_H = 185.0, 55.0  # основная надпись, форма 1
THICK = 0.5               # вес основных линий, мм
CHAR_W = 0.72             # средняя ширина символа Arial в долях высоты (с запасом)


def text_width(text: str, h: float) -> float:
    return len(str(text)) * CHAR_W * h


def fit_height(text: str, h: float, width: float) -> float:
    """Уменьшить высоту текста, чтобы он поместился в ширину ячейки."""
    w = text_width(text, h)
    return h if w <= width or not text else max(h * width / w, h * 0.35)


def paper_size(fmt: str, landscape: bool) -> tuple[float, float]:
    key = fmt.upper().replace("А", "A")  # кириллическая «А» тоже подходит
    if key not in PAPER:
        raise ValueError(f"Формат {fmt} не поддерживается. Доступно: {', '.join(PAPER)}")
    w, h = PAPER[key]
    return (float(h), float(w)) if landscape else (float(w), float(h))


def _line(p1, p2, lw: float | None = None) -> dict[str, Any]:
    op: dict[str, Any] = {"op": "line", "p1": p1, "p2": p2, "layer": L_SHEET}
    if lw:
        op["lineweight"] = lw
    return op


def _rect(x, y, w, h, lw: float | None = None) -> dict[str, Any]:
    op: dict[str, Any] = {"op": "polyline", "points": [(x, y), (x + w, y), (x + w, y + h), (x, y + h)],
                          "closed": True, "layer": L_SHEET}
    if lw:
        op["lineweight"] = lw
    return op


def _txt(x, y, text, h, align="center", layer=L_SHEET) -> dict[str, Any]:
    return {"op": "text", "pos": (x, y), "text": str(text), "height": h, "layer": layer, "align": align}


def choose_scale(area_w: float, area_h: float, field_w: float, field_h: float) -> float:
    for n in STD_SCALES:
        if area_w / n <= field_w and area_h / n <= field_h:
            return n
    return STD_SCALES[-1]


def sheet_ops(fmt: str, landscape: bool, scale: float | None, area: list[float] | None,
              fields: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Рамка формата с основной надписью вокруг области area=[x1,y1,x2,y2] (координаты модели).

    scale=None/0: подобрать стандартный масштаб, чтобы область поместилась.
    Возвращает (ops, info) где info: формат, масштаб, рамка листа в координатах модели.
    """
    pw, ph = paper_size(fmt, landscape)
    if pw < TB_W + 25:
        raise ValueError("Лист слишком узкий для основной надписи")
    # поле чертежа: внутри рамки (20 мм слева, 5 мм остальные) и над основной надписью
    fx1, fy1, fx2, fy2 = 20 + 10, 5 + TB_H + 10, pw - 5 - 10, ph - 5 - 10
    field_w, field_h = fx2 - fx1, fy2 - fy1
    if area:
        ax1, ay1, ax2, ay2 = min(area[0], area[2]), min(area[1], area[3]), max(area[0], area[2]), max(area[1], area[3])
    else:
        ax1 = ay1 = 0.0
        ax2 = ay2 = 1.0
    aw, ah = max(ax2 - ax1, 1e-6), max(ay2 - ay1, 1e-6)
    auto = not scale
    n = choose_scale(aw, ah, field_w, field_h) if auto else float(scale)
    fits = aw / n <= field_w + 1e-6 and ah / n <= field_h + 1e-6
    # лист: центр поля чертежа совпадает с центром области
    cx, cy = (ax1 + ax2) / 2, (ay1 + ay2) / 2
    ox = cx - (fx1 + fx2) / 2 * n
    oy = cy - (fy1 + fy2) / 2 * n

    def P(x, y):
        return (ox + x * n, oy + y * n)

    ops: list[dict[str, Any]] = []

    def line(x1, y1, x2, y2, thick=False):
        ops.append(_line(P(x1, y1), P(x2, y2), THICK if thick else None))

    def rect(x, y, w, h, thick=False):
        r = _rect(*P(x, y), w * n, h * n, THICK if thick else None)
        ops.append(r)

    def text(x, y, s, h, align="center", width=None):
        if s in (None, ""):
            return
        hh = fit_height(str(s), h, width) if width else h
        ops.append(_txt(*P(x, y), s, hh * n, align))

    rect(0, 0, pw, ph)                          # граница листа (тонкая)
    rect(20, 5, pw - 25, ph - 10, thick=True)   # рамка

    # основная надпись: левый нижний угол
    X, Y = pw - 5 - TB_W, 5.0
    rect(X, Y, TB_W, TB_H, thick=True)
    # левая зона 65 мм: колонки 7/10/23/15/10, строки по 5 мм
    cols = [0, 7, 17, 40, 55, 65]
    for i in range(1, 11):
        line(X, Y + 5 * i, X + 65, Y + 5 * i, thick=(i in (6, 7)))
    for c in cols[1:5]:
        line(X + c, Y + 30, X + c, Y + 55, thick=True)  # графы изменений и шапка
    for c in (17, 40, 55):
        line(X + c, Y, X + c, Y + 30, thick=True)
    line(X + 65, Y, X + 65, Y + 55, thick=True)
    # правая зона
    line(X + 65, Y + 40, X + 185, Y + 40, thick=True)    # под графой обозначения
    line(X + 65, Y + 15, X + 185, Y + 15, thick=True)    # над материалом/организацией
    line(X + 135, Y, X + 135, Y + 40, thick=True)        # наименование | лит/масса/масштаб
    line(X + 135, Y + 35, X + 185, Y + 35, thick=True)   # шапка «Лит. Масса Масштаб»
    line(X + 135, Y + 20, X + 185, Y + 20, thick=True)   # строка «Лист / Листов»
    line(X + 150, Y + 20, X + 150, Y + 40, thick=True)
    line(X + 167, Y + 20, X + 167, Y + 40, thick=True)
    line(X + 140, Y + 20, X + 140, Y + 35)
    line(X + 145, Y + 20, X + 145, Y + 35)
    line(X + 155, Y + 15, X + 155, Y + 20, thick=True)

    lab = 2.5
    for x0, x1, s in ((0, 7, "Изм."), (7, 17, "Лист"), (17, 40, "№ докум."), (40, 55, "Подп."), (55, 65, "Дата")):
        text(X + (x0 + x1) / 2, Y + 32.5, s, lab, width=x1 - x0 - 1)
    for i, s in enumerate(("Разраб.", "Пров.", "Т.контр.", "", "Н.контр.", "Утв.")):
        text(X + 1, Y + 27.5 - 5 * i, s, lab, "mleft", width=15)
    date = fields.get("date") or _dt.date.today().strftime("%d.%m.%y")
    for i, (who, key) in enumerate((("developer", 0), ("checker", 1))):
        name = fields.get(who)
        text(X + 18, Y + 27.5 - 5 * key, name, lab, "mleft", width=21)
        if name:
            text(X + 60, Y + 27.5 - 5 * key, date, lab, width=9.4)
    for x0, x1, s in ((135, 150, "Лит."), (150, 167, "Масса"), (167, 185, "Масштаб")):
        text(X + (x0 + x1) / 2, Y + 37.5, s, lab, width=x1 - x0 - 1)
    scale_txt = f"1:{n:g}"
    text(X + 176, Y + 27.5, scale_txt, 3.5, width=17)
    text(X + 136, Y + 17.5, f"Лист {fields.get('sheet') or 1}", lab, "mleft", width=18)
    text(X + 156, Y + 17.5, f"Листов {fields.get('sheets') or 1}", lab, "mleft", width=28)
    text(X + 125, Y + 47.5, fields.get("number"), 5, width=118)        # обозначение (номер)
    text(X + 100, Y + 27.5, fields.get("title"), 5, width=68)          # наименование
    text(X + 160, Y + 7.5, fields.get("organization"), 3.5, width=48)  # организация

    frame = [ox, oy, ox + pw * n, oy + ph * n]
    info = {"format": fmt.upper(), "landscape": landscape, "paper_mm": [pw, ph], "scale": n,
            "scale_text": scale_txt, "auto_scale": auto, "fits": fits, "frame": frame}
    return ops, info


# ------------------------------------------------------------------ таблицы

def table_layout(header: list[str] | None, rows: list[list[Any]], h: float,
                 col_widths: list[float] | None = None, title: str | None = None) -> dict[str, Any]:
    """Размеры таблицы: ширины колонок (авто по тексту) и высота строки."""
    ncols = max([len(header or [])] + [len(r) for r in rows] + [1])
    widths: list[float] = []
    for c in range(ncols):
        if col_widths and c < len(col_widths) and col_widths[c]:
            widths.append(float(col_widths[c]))
            continue
        cells = [header[c] if header and c < len(header) else ""] + [r[c] if c < len(r) else "" for r in rows]
        widths.append(max(text_width(str(v), h) for v in cells) + 1.6 * h)
    total = sum(widths)
    if title and text_width(title, h * 1.2) + 2 * h > total:
        k = (text_width(title, h * 1.2) + 2 * h) / total
        widths = [w * k for w in widths]
    return {"widths": widths, "row_h": 2.0 * h, "ncols": ncols}


def table_ops(x: float, y: float, header: list[str] | None, rows: list[list[Any]], h: float,
              col_widths: list[float] | None = None, title: str | None = None,
              layer: str = L_TABLE) -> tuple[list[dict[str, Any]], list[float]]:
    """Таблица линиями и текстом. (x, y): левый верхний угол. Возвращает ops и габарит."""
    lay = table_layout(header, rows, h, col_widths, title)
    widths, rh = lay["widths"], lay["row_h"]
    W = sum(widths)
    lines: list[list[Any]] = ([[title]] if title else []) + ([header] if header else []) + rows
    H = rh * len(lines)
    ops: list[dict[str, Any]] = [{"op": "polyline", "points": [(x, y), (x + W, y), (x + W, y - H), (x, y - H)],
                                  "closed": True, "layer": layer}]
    for i in range(1, len(lines)):
        ops.append({"op": "line", "p1": (x, y - rh * i), "p2": (x + W, y - rh * i), "layer": layer})
    first_grid = 1 if title else 0
    cx = x
    for w in widths[:-1]:
        cx += w
        ops.append({"op": "line", "p1": (cx, y - rh * first_grid), "p2": (cx, y - H), "layer": layer})
    for i, row in enumerate(lines):
        cy = y - rh * (i + 0.5)
        if title and i == 0:
            ops.append(_txt(x + W / 2, cy, title, fit_height(title, h * 1.2, W - h), layer=layer))
            continue
        cx = x
        for c, w in enumerate(widths):
            v = row[c] if c < len(row) else ""
            if v not in (None, ""):
                ops.append(_txt(cx + w / 2, cy, v, fit_height(str(v), h, w - 0.6 * h), layer=layer))
            cx += w
    return ops, [x, y - H, x + W, y]
