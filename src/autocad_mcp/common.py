"""Общие для обоих движков помощники: фильтры объектов, веса линий, геометрия."""

from __future__ import annotations

import fnmatch
import math
from typing import Any, Iterable

# допустимые веса линий AutoCAD (сотые доли мм)
LINEWEIGHTS = [0, 5, 9, 13, 15, 18, 20, 25, 30, 35, 40, 50, 53, 60, 70, 80, 90, 100, 106, 120, 140, 158, 200, 211]

# ObjectName (COM) -> тип DXF: в обоих движках типы называются одинаково
OBJECT_TO_DXF = {
    "AcDbLine": "LINE", "AcDbPolyline": "LWPOLYLINE", "AcDb2dPolyline": "POLYLINE",
    "AcDb3dPolyline": "POLYLINE", "AcDbCircle": "CIRCLE", "AcDbArc": "ARC",
    "AcDbEllipse": "ELLIPSE", "AcDbSpline": "SPLINE", "AcDbText": "TEXT", "AcDbMText": "MTEXT",
    "AcDbPoint": "POINT", "AcDbHatch": "HATCH", "AcDbBlockReference": "INSERT",
    "AcDbTable": "ACAD_TABLE", "AcDbMLeader": "MULTILEADER", "AcDbLeader": "LEADER",
    "AcDbXline": "XLINE", "AcDbRay": "RAY", "AcDbSolid": "SOLID", "AcDbRegion": "REGION",
    "AcDbWipeout": "WIPEOUT", "AcDbAttributeDefinition": "ATTDEF",
}


# формат файла по году: 2018 читают AutoCAD 2018 и новее, 2013 читают 2013-2017 и т.д.
FILE_VERSIONS = ("2018", "2013", "2010", "2007", "2004", "2000")
DXF_VERSIONS = {"2018": "R2018", "2013": "R2013", "2010": "R2010", "2007": "R2007", "2004": "R2004", "2000": "R2000"}


def version_key(version: str | int) -> str:
    """'2016' -> '2013': формат, который откроет AutoCAD указанного года (форматы меняются не каждый год)."""
    s = str(version).strip().upper().lstrip("R")
    if not s.isdigit():
        raise ValueError(f"Версия формата: год, например 2018 или 2013. Доступно: {', '.join(FILE_VERSIONS)}")
    year = int(s)
    for v in FILE_VERSIONS:
        if year >= int(v):
            return v
    raise ValueError("Самый старый поддерживаемый формат: 2000")


# английские имена типов линий -> имена в русской версии AutoCAD (acadiso.lin)
_LT_RU = {"BORDER": "рант", "CENTER": "осевая", "DASHDOT": "штрихпунктирная", "DASHED": "штриховая",
          "DIVIDE": "линия_сгиба", "DOT": "пунктирная", "HIDDEN": "невидимая", "PHANTOM": "фантом"}


def linetype_aliases(name: str) -> list[str]:
    """Имя типа линии и его вариант на другом языке (DASHED <-> штриховая, CENTER2 <-> осевая2)."""
    out = [name]
    up = name.upper()
    for en, ru in _LT_RU.items():
        for suf in ("X2", "2", ""):
            if up == en + suf:
                out.append(ru + suf)
            elif name.lower() == ru + suf.lower():
                out.append(en + suf)
    return out


def dxf_type(object_name: str) -> str:
    if object_name in OBJECT_TO_DXF:
        return OBJECT_TO_DXF[object_name]
    if object_name.endswith("Dimension"):
        return "DIMENSION"
    return object_name.removeprefix("AcDb").upper()


def match_names(value: str, pattern: str | None) -> bool:
    """Фильтр как в AutoCAD: шаблоны через запятую, * и ?, без учёта регистра."""
    if not pattern:
        return True
    v = value.upper()
    return any(fnmatch.fnmatchcase(v, p.strip().upper()) for p in pattern.split(",") if p.strip())


def bbox_in_area(bbox: list[float] | None, area: list[float] | None, crossing: bool) -> bool:
    """area=[x1,y1,x2,y2]. crossing=False: объект целиком внутри, True: хотя бы касается."""
    if not area:
        return True
    if bbox is None:
        return False
    ax1, ay1, ax2, ay2 = min(area[0], area[2]), min(area[1], area[3]), max(area[0], area[2]), max(area[1], area[3])
    x1, y1, x2, y2 = bbox
    if crossing:
        return x1 <= ax2 and x2 >= ax1 and y1 <= ay2 and y2 >= ay1
    return x1 >= ax1 and x2 <= ax2 and y1 >= ay1 and y2 <= ay2


def lineweight_code(mm: float) -> int:
    """Вес линии в мм (0.25, 0.5) -> код AutoCAD. -1 ByLayer, -2 ByBlock, -3 по умолчанию."""
    if mm in (-1, -2, -3):
        return int(mm)
    v = float(mm) * 100
    return min(LINEWEIGHTS, key=lambda c: abs(c - v))


def rnd(v: Any, nd: int = 3):
    if isinstance(v, (list, tuple)):
        return [rnd(x, nd) for x in v]
    if isinstance(v, float):
        return round(v, nd)
    return v


def polygon_points(cx: float, cy: float, r: float, sides: int, rotation_deg: float = 90.0,
                   inscribed: bool = True) -> list[tuple[float, float]]:
    """Вершины правильного многоугольника. inscribed=False: r = радиус вписанной окружности."""
    if sides < 3:
        raise ValueError("sides должно быть не меньше 3")
    R = r if inscribed else r / math.cos(math.pi / sides)
    a0 = math.radians(rotation_deg)
    return [(cx + R * math.cos(a0 + 2 * math.pi * i / sides), cy + R * math.sin(a0 + 2 * math.pi * i / sides))
            for i in range(sides)]


def arc_from_3p(p1, p2, p3) -> tuple[tuple[float, float], float, float, float]:
    """Дуга через три точки (начало, точка на дуге, конец) -> центр, радиус, углы (град, против часовой)."""
    (x1, y1), (x2, y2), (x3, y3) = p1, p2, p3
    d = 2 * (x1 * (y2 - y3) + x2 * (y3 - y1) + x3 * (y1 - y2))
    if abs(d) < 1e-9:
        raise ValueError("Точки лежат на одной прямой: дугу построить нельзя")
    ux = ((x1**2 + y1**2) * (y2 - y3) + (x2**2 + y2**2) * (y3 - y1) + (x3**2 + y3**2) * (y1 - y2)) / d
    uy = ((x1**2 + y1**2) * (x3 - x2) + (x2**2 + y2**2) * (x1 - x3) + (x3**2 + y3**2) * (x2 - x1)) / d
    r = math.hypot(x1 - ux, y1 - uy)
    a1 = math.degrees(math.atan2(y1 - uy, x1 - ux)) % 360
    a2 = math.degrees(math.atan2(y2 - uy, x2 - ux)) % 360
    a3 = math.degrees(math.atan2(y3 - uy, x3 - ux)) % 360
    # средняя точка должна лежать на дуге от a1 к a3 против часовой, иначе меняем концы
    if (a2 - a1) % 360 <= (a3 - a1) % 360:
        return (ux, uy), r, a1, a3
    return (ux, uy), r, a3, a1


def angular_location(vertex, p1, p2, radius=None) -> tuple[float, float]:
    """Точка дуги углового размера: на биссектрисе угла от луча p1 к лучу p2 (против часовой)."""
    a1 = math.atan2(p1[1] - vertex[1], p1[0] - vertex[0])
    a2 = math.atan2(p2[1] - vertex[1], p2[0] - vertex[0])
    mid = a1 + ((a2 - a1) % (2 * math.pi)) / 2
    r = radius or 0.6 * min(math.dist(vertex, p1), math.dist(vertex, p2))
    return (vertex[0] + r * math.cos(mid), vertex[1] + r * math.sin(mid))


def polyline_length_area(points: Iterable[tuple[float, float]], closed: bool) -> tuple[float, float]:
    pts = list(points)
    n = len(pts)
    length = sum(math.dist(pts[i], pts[i + 1]) for i in range(n - 1))
    area = 0.0
    if closed and n > 2:
        length += math.dist(pts[-1], pts[0])
        area = abs(sum(pts[i][0] * pts[(i + 1) % n][1] - pts[(i + 1) % n][0] * pts[i][1] for i in range(n))) / 2
    return length, area


def signed_area(pts: list[tuple[float, float]]) -> float:
    n = len(pts)
    return sum(pts[i][0] * pts[(i + 1) % n][1] - pts[(i + 1) % n][0] * pts[i][1] for i in range(n)) / 2


def point_in_polygon(p, pts) -> bool:
    x, y = p
    inside = False
    n = len(pts)
    for i in range(n):
        (x1, y1), (x2, y2) = pts[i], pts[(i + 1) % n]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


def side_of(p, a, b) -> float:
    """>0: точка p слева от направления a->b."""
    return (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])


def nearest_segment(p, pts, closed: bool) -> tuple[tuple[float, float], tuple[float, float]]:
    segs = [(pts[i], pts[i + 1]) for i in range(len(pts) - 1)]
    if closed:
        segs.append((pts[-1], pts[0]))

    def dist(seg):
        (ax, ay), (bx, by) = seg
        dx, dy = bx - ax, by - ay
        L = dx * dx + dy * dy or 1e-12
        t = max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / L))
        return math.hypot(ax + t * dx - p[0], ay + t * dy - p[1])

    return min(segs, key=dist)


def offset_left(kind: str, geom: dict[str, Any], side: tuple[float, float] | None, distance: float) -> float:
    """Смещение в единой системе: >0 = влево от направления (линия, полилиния) или наружу (окружность, дуга).

    Если задана точка стороны side, знак выбирается так, чтобы копия легла в её сторону.
    Для замкнутой полилинии «внутрь/наружу» определяется по тому, внутри ли контура точка.
    """
    d = abs(distance) if side else distance
    if not side:
        return d
    if kind in ("CIRCLE", "ARC"):
        out = math.dist(side, geom["center"]) > geom["radius"]
        return d if out else -d
    if kind == "LINE":
        return d if side_of(side, geom["start"], geom["end"]) > 0 else -d
    if kind == "LWPOLYLINE":
        pts = geom["points"]
        if geom.get("closed") and len(pts) > 2:
            inside = point_in_polygon(side, pts)
            ccw = signed_area(pts) > 0
            # для контура против часовой «влево» = внутрь
            return d if inside == ccw else -d
        a, b = nearest_segment(side, pts, False)
        return d if side_of(side, a, b) > 0 else -d
    return d
