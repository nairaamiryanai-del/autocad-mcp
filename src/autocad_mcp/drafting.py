"""Геометрия универсальных инструментов: стены с проёмами, координационные оси.

Модуль движко-независимый, как constructions.py: возвращает примитивы
  {"op": "line", "p1", "p2", "layer"}
  {"op": "arc", "center", "r", "a1", "a2", "layer"}   (против часовой от a1 до a2, градусы)
  {"op": "circle", "center", "r", "layer"}
  {"op": "text", "pos", "text", "height", "layer", "align"}
Единицы: мм, ось Y вверх.
"""

from __future__ import annotations

import math
from typing import Any

Point = tuple[float, float]
Op = dict[str, Any]

L_WALL = "WALLS"
L_DOOR = "DOORS"
L_WINDOW = "WINDOWS"
L_AXES = "AXES"
L_AXES_MARK = "AXES_MARKS"

# ГОСТ 21.101: буквы для осей без Ё, З, Й, О, Х, Ц, Ч, Щ, Ъ, Ы, Ь
AXIS_LETTERS = "АБВГДЕЖИКЛМНПРСТУФШЭЮЯ"


def axis_letters(n: int) -> list[str]:
    """А, Б, В...; когда букв не хватает: АА, ББ... (ГОСТ 21.101)."""
    out = []
    for i in range(n):
        k, r = divmod(i, len(AXIS_LETTERS))
        out.append(AXIS_LETTERS[r] * (k + 1))
    return out


def _sub(a: Point, b: Point) -> Point:
    return (a[0] - b[0], a[1] - b[1])


def _add(a: Point, b: Point, k: float = 1.0) -> Point:
    return (a[0] + b[0] * k, a[1] + b[1] * k)


def _unit(v: Point) -> Point:
    n = math.hypot(*v)
    if n < 1e-9:
        raise ValueError("Две соседние точки стены совпадают")
    return (v[0] / n, v[1] / n)


def _dot(a: Point, b: Point) -> float:
    return a[0] * b[0] + a[1] * b[1]


def _line(p1: Point, p2: Point, layer: str) -> Op:
    return {"op": "line", "p1": p1, "p2": p2, "layer": layer}


def _offset_vertices(pts: list[Point], h: float, closed: bool) -> list[Point]:
    """Вершины линии, смещённой влево на h, с подрезкой углов (митра)."""
    n = len(pts)
    segs = n if closed else n - 1
    dirs = [_unit(_sub(pts[(i + 1) % n], pts[i])) for i in range(segs)]
    norms = [(-d[1], d[0]) for d in dirs]
    out = []
    for k in range(n):
        if not closed and k == 0:
            out.append(_add(pts[0], norms[0], h))
            continue
        if not closed and k == n - 1:
            out.append(_add(pts[k], norms[-1], h))
            continue
        na, nb = norms[(k - 1) % segs], norms[k % segs]
        c = 1 + _dot(na, nb)
        if c < 1e-6:
            raise ValueError("Стена разворачивается на 180° в одной точке")
        out.append(_add(pts[k], (na[0] + nb[0], na[1] + nb[1]), h / c))
    return out


def _cut(start: float, end: float, gaps: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Отрезок [start, end] минус промежутки gaps."""
    parts = [(start, end)]
    for a, b in gaps:
        nxt = []
        for s, e in parts:
            if b <= s or a >= e:
                nxt.append((s, e))
                continue
            if a > s:
                nxt.append((s, a))
            if b < e:
                nxt.append((b, e))
        parts = nxt
    return [(s, e) for s, e in parts if e - s > 1e-6]


def walls(points: list[Point], thickness: float, closed: bool = False,
          openings: list[dict[str, Any]] | None = None) -> list[Op]:
    """Стены по осевой линии с толщиной и проёмами.

    opening: {segment: № участка с 0, offset: от начала участка до края проёма по оси, width,
              type: door|window|opening, swing: left|right (куда открывается дверь относительно
              направления участка), hinge: start|end (сторона петель)}
    """
    pts = [(float(x), float(y)) for x, y in points]
    if closed and len(pts) > 2 and math.dist(pts[0], pts[-1]) < 1e-6:
        pts = pts[:-1]
    if len(pts) < 2 or (closed and len(pts) < 3):
        raise ValueError("Нужно минимум 2 точки (для замкнутого контура 3)")
    t = float(thickness)
    if t <= 0:
        raise ValueError("Толщина стены должна быть > 0")
    h = t / 2
    n = len(pts)
    segs = n if closed else n - 1
    left = _offset_vertices(pts, h, closed)
    right = _offset_vertices(pts, -h, closed)

    by_seg: dict[int, list[dict[str, Any]]] = {}
    for i, o in enumerate(openings or []):
        s = int(o.get("segment", 0))
        if not 0 <= s < segs:
            raise ValueError(f"Проём {i + 1}: участка {s} нет (участки 0..{segs - 1})")
        by_seg.setdefault(s, []).append(o)

    ops: list[Op] = []
    for i in range(segs):
        p, q = pts[i], pts[(i + 1) % n]
        d = _unit(_sub(q, p))
        nrm = (-d[1], d[0])
        length = math.dist(p, q)
        gaps = []
        for o in sorted(by_seg.get(i, []), key=lambda o: float(o.get("offset", 0))):
            a = float(o.get("offset", 0))
            w = float(o.get("width", 0))
            if w <= 0 or a < 0 or a + w > length + 1e-6:
                raise ValueError(f"Проём на участке {i}: offset {a:g} + width {w:g} не помещается в длину {length:g}")
            if gaps and a < gaps[-1][1] - 1e-6:
                raise ValueError(f"Проёмы на участке {i} пересекаются")
            gaps.append((a, a + w))
            ops += _opening(p, d, nrm, h, a, w, o)
        for side, verts in ((h, left), (-h, right)):
            a0 = _dot(_sub(verts[i], p), d)
            a1 = _dot(_sub(verts[(i + 1) % n], p), d)
            for s, e in _cut(a0, a1, gaps):
                ops.append(_line(_add(_add(p, d, s), nrm, side), _add(_add(p, d, e), nrm, side), L_WALL))
    if not closed:
        ops.append(_line(left[0], right[0], L_WALL))
        ops.append(_line(left[-1], right[-1], L_WALL))
    return ops


def _opening(p: Point, d: Point, nrm: Point, h: float, a: float, w: float, o: dict[str, Any]) -> list[Op]:
    kind = str(o.get("type", "door")).lower()
    at = lambda s, k: _add(_add(p, d, s), nrm, k * h)  # noqa: E731
    ops = [_line(at(a, 1), at(a, -1), L_WALL), _line(at(a + w, 1), at(a + w, -1), L_WALL)]  # откосы
    if kind == "window":
        for k in (1, 0, -1):
            ops.append(_line(at(a, k), at(a + w, k), L_WINDOW))
    elif kind == "door":
        side = 1 if str(o.get("swing", "left")).lower().startswith("l") else -1
        hinge_at_start = not str(o.get("hinge", "start")).lower().startswith("e")
        hs, other = (a, a + w) if hinge_at_start else (a + w, a)
        hinge = at(hs, side)
        closed_end = at(other, side)
        leaf_end = _add(hinge, nrm, side * w)
        ops.append(_line(hinge, leaf_end, L_DOOR))
        v1, v2 = _sub(leaf_end, hinge), _sub(closed_end, hinge)
        a1, a2 = math.degrees(math.atan2(v1[1], v1[0])), math.degrees(math.atan2(v2[1], v2[0]))
        if v1[0] * v2[1] - v1[1] * v2[0] < 0:  # дуга всегда против часовой
            a1, a2 = a2, a1
        ops.append({"op": "arc", "center": hinge, "r": w, "a1": a1, "a2": a2, "layer": L_DOOR})
    return ops


def axes(x_spacings: list[float], y_spacings: list[float], origin: Point = (0.0, 0.0),
         text_height: float = 300.0, overhang: float | None = None, both_sides: bool = False,
         x_labels: list[str] | None = None, y_labels: list[str] | None = None) -> tuple[list[Op], dict[str, Any]]:
    """Сетка осей: вертикальные оси (по X) с цифрами, горизонтальные (по Y) с буквами.

    Возвращает примитивы и координаты осей для размеров.
    """
    xs = [origin[0]]
    for s in x_spacings:
        xs.append(xs[-1] + float(s))
    ys = [origin[1]]
    for s in y_spacings:
        ys.append(ys[-1] + float(s))
    th = float(text_height)
    r = th * 1.1  # кружок марки: при 1:100 и тексте 3.5 мм получается ≈8 мм на бумаге (ГОСТ: 6-12)
    over = float(overhang) if overhang is not None else th * 4
    xl = x_labels or [str(i + 1) for i in range(len(xs))]
    yl = y_labels or axis_letters(len(ys))
    if len(xl) != len(xs) or len(yl) != len(ys):
        raise ValueError("Число подписей должно совпадать с числом осей")
    x0, x1, y0, y1 = xs[0] - over, xs[-1] + over, ys[0] - over, ys[-1] + over
    ops: list[Op] = []

    def mark(c: Point, label: str):
        ops.append({"op": "circle", "center": c, "r": r, "layer": L_AXES_MARK})
        ops.append({"op": "text", "pos": c, "text": label, "height": th, "layer": L_AXES_MARK, "align": "center"})

    for x, lab in zip(xs, xl):
        ops.append(_line((x, y0), (x, y1), L_AXES))
        mark((x, y0 - r), lab)
        if both_sides:
            mark((x, y1 + r), lab)
    for y, lab in zip(ys, yl):
        ops.append(_line((x0, y), (x1, y), L_AXES))
        mark((x0 - r, y), lab)
        if both_sides:
            mark((x1 + r, y), lab)
    return ops, {"x": xs, "y": ys, "x_labels": xl, "y_labels": yl, "bubble_radius": r,
                 "extent": [x0 - 2 * r, y0 - 2 * r, x1 + 2 * r, y1 + 2 * r]}
