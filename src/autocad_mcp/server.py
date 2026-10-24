"""MCP-сервер для AutoCAD.

Два движка:
  - com   — управление запущенным AutoCAD (Windows + установленный AutoCAD)
  - ezdxf — генерация/правка DXF без AutoCAD

Выбор движка: переменная окружения AUTOCAD_MCP_ENGINE = auto|com|ezdxf (по умолч. auto).
Папка вывода: AUTOCAD_MCP_OUT (по умолч. Документы/AutoCAD-MCP).
Запуск: stdio (по умолчанию) или HTTP для любых клиентов MCP, см. autocad-mcp --help.
"""

from __future__ import annotations

import contextlib
import functools
import json
import logging
import os
import threading
import time
from typing import Any

# служебные сообщения ezdxf («creating ACAD_COLOR dictionary») пользователю не нужны
logging.getLogger("ezdxf").setLevel(logging.WARNING)
logging.getLogger("fontTools").setLevel(logging.ERROR)

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from . import constructions as C
from . import orders as O
from . import sheets as S
from .common import arc_from_3p, polygon_points
from .engine_ezdxf import EzdxfEngine
from .layout import auto_text_height

def _default_out() -> str:
    # клиент MCP запускает сервер из своей папки (часто системной): файлы кладём в Документы пользователя
    docs = os.path.join(os.path.expanduser("~"), "Documents")
    return os.path.join(docs if os.path.isdir(docs) else os.path.expanduser("~"), "AutoCAD-MCP")


OUT_DIR = os.environ.get("AUTOCAD_MCP_OUT") or _default_out()
ENGINE_PREF = (os.environ.get("AUTOCAD_MCP_ENGINE") or "auto").lower()

INSTRUCTIONS = """\
Сервер черчения в AutoCAD (режим com, Windows) или в DXF без AutoCAD (режим ezdxf, любая ОС).
Единицы: миллиметры, ось Y вверх, углы в градусах против часовой.
Порядок работы: acad_status (какой режим и версия) -> new_drawing или open_drawing -> рисование ->
render_preview (посмотреть результат PNG) -> save_drawing / export_pdf.
Много объектов сразу: draw_batch (одна команда, один шаг отмены). Повторы: array_entities.
Планы зданий: draw_axes (оси с марками), draw_walls (стены с проёмами), add_dimension_chain (цепочки размеров).
Окна, двери, витражи по замеру: draw_construction, draw_order, import_order (заказы из «К-Замер»).
Высоту надписей задаёт set_annotation_scale (для планов 250-500 мм, для окон 80-120), иначе авто.
Найти и изменить нарисованное: list_entities / select_entities -> transform_entities, set_properties, erase_entities.
Чего нет в инструментах: run_command (только com), команды на английском с префиксом _. работают в AutoCAD на любом языке.
"""

mcp = MCPServer("autocad", instructions=INSTRUCTIONS)

_engine: Any = None
# по HTTP вызовы приходят параллельно, а чертёж один: инструменты выполняются по очереди
_lock = threading.RLock()

Pts = list[list[float]]


def _make_engine(pref: str):
    """Создать движок по предпочтению. auto: COM если доступен, иначе ezdxf."""
    if pref in ("com", "auto"):
        try:
            from .engine_com import ComEngine
            if pref == "com" or ComEngine.is_available():
                return ComEngine(OUT_DIR)
        except Exception as e:
            if pref == "com":
                raise RuntimeError(f"COM-движок недоступен: {e}") from e
    return EzdxfEngine(OUT_DIR)


def engine():
    global _engine
    if _engine is None:
        _engine = _make_engine(ENGINE_PREF)
    return _engine


def _ok(data: Any) -> str:
    if isinstance(data, str):
        return data
    return json.dumps(data, ensure_ascii=False, indent=2)


def _friendly(e: Exception) -> str:
    if type(e).__name__ == "com_error":
        from .engine_com import com_message
        return f"AutoCAD вернул ошибку: {com_message(e)}"
    return str(e) or type(e).__name__


def tool(description: str):
    """Регистрация инструмента: любая ошибка уходит клиенту понятным текстом."""

    def deco(fn):
        @functools.wraps(fn)
        def wrapped(*args, **kwargs):
            try:
                with _lock:
                    return fn(*args, **kwargs)
            except ToolError:
                raise
            except Exception as e:  # noqa: BLE001
                raise ToolError(_friendly(e)) from e

        return mcp.tool(description=description)(wrapped)

    return deco


@contextlib.contextmanager
def _group(eng):
    """Несколько вызовов движка = один шаг отмены в AutoCAD."""
    if hasattr(eng, "begin_group"):
        eng.begin_group()
    try:
        yield
    finally:
        if hasattr(eng, "end_group"):
            eng.end_group()


def _pts(points: Pts, need: int = 2) -> list[tuple[float, float]]:
    pts = [(float(p[0]), float(p[1])) for p in points]
    if len(pts) < need:
        raise ValueError(f"Нужно минимум {need} точки")
    return pts


def _area(area: list[float] | None) -> list[float] | None:
    if area is None:
        return None
    if len(area) != 4:
        raise ValueError("area задаётся как [x1, y1, x2, y2]")
    return [float(v) for v in area]


def _targets(handles: list[str] | None, layer: str | None, entity_type: str | None,
             area: list[float] | None, crossing: bool = False) -> list[str]:
    if handles:
        return [str(h) for h in handles]
    if not (layer or entity_type or area):
        raise ValueError("Укажите handles или фильтр: layer, entity_type, area")
    return engine().select(layer, entity_type, _area(area), crossing)


def _out(path: str | None, stem: str, ext: str) -> str:
    if path:
        return path if path.lower().endswith(ext) else path + ext
    return os.path.join(OUT_DIR, f"{stem}_{time.strftime('%Y%m%d_%H%M%S')}{ext}")


# ---------------------------------------------------------------- инфо / режим

@tool(description="Статус: активный движок, подключён ли AutoCAD, текущий файл, число объектов.")
def acad_status() -> str:
    return _ok(engine().status())


@tool(description="Переключить движок: 'auto' (COM если есть, иначе ezdxf), 'com' (живой AutoCAD) или 'ezdxf' (без AutoCAD).")
def set_engine(engine_name: str) -> str:
    global _engine
    pref = engine_name.lower().strip()
    if pref not in ("auto", "com", "ezdxf"):
        return "engine_name должен быть auto, com или ezdxf"
    _engine = _make_engine(pref)
    return _ok({"switched_to": _engine.name, **_engine.status()})


@tool(description="Сводка по чертежу: файл, число объектов (по типам в ezdxf).")
def drawing_summary() -> str:
    return _ok(engine().summary())


# ------------------------------------------------------------------- документы

@tool(description="Создать новый пустой чертёж.")
def new_drawing() -> str:
    return _ok(engine().new_drawing())


@tool(description="Открыть существующий чертёж (DWG для COM / DXF для ezdxf) по абсолютному пути.")
def open_drawing(path: str) -> str:
    return _ok(engine().open_drawing(path))


@tool(description=(
    "Сохранить чертёж. Без пути: сохранить текущий или в папку вывода. version: год формата для старых "
    "версий AutoCAD (2018, 2013, 2010, 2007, 2004, 2000; можно год своей версии, например 2016). "
    "Возвращает путь к файлу."
))
def save_drawing(path: str | None = None, version: str | None = None) -> str:
    return _ok(engine().save_drawing(path, version))


@tool(description="Сохранить копию чертежа в DXF (текущий файл не меняется). Без пути: в папку вывода.")
def export_dxf(path: str | None = None) -> str:
    return _ok(engine().export_dxf(_out(path, "export", ".dxf")))


# -------------------------------------------------------------------- черчение

@tool(description="Отрезок из (x1,y1) в (x2,y2). layer: слой (по умолчанию '0'). Возвращает handle.")
def draw_line(x1: float, y1: float, x2: float, y2: float, layer: str = "0") -> str:
    h = engine().add_line((x1, y1), (x2, y2), layer)
    return f"Линия ({x1},{y1})-({x2},{y2}) на слое {layer}, handle {h}"


@tool(description="Полилиния по точкам [[x,y],...]. closed: замкнуть контур.")
def draw_polyline(points: Pts, closed: bool = False, layer: str = "0") -> str:
    pts = _pts(points)
    h = engine().add_polyline(pts, closed, layer)
    return f"Полилиния из {len(pts)} точек (closed={closed}) на слое {layer}, handle {h}"


@tool(description="Прямоугольник по левому нижнему углу (x,y) и размерам w×h.")
def draw_rectangle(x: float, y: float, w: float, h: float, layer: str = "0") -> str:
    hd = engine().add_polyline([(x, y), (x + w, y), (x + w, y + h), (x, y + h)], True, layer)
    return f"Прямоугольник {w}×{h} в ({x},{y}) на слое {layer}, handle {hd}"


@tool(description="Окружность: центр (cx,cy), радиус r.")
def draw_circle(cx: float, cy: float, r: float, layer: str = "0") -> str:
    h = engine().add_circle((cx, cy), r, layer)
    return f"Окружность r={r} в ({cx},{cy}) на слое {layer}, handle {h}"


@tool(description=(
    "Дуга. Либо центр (cx,cy), радиус r и углы start_angle..end_angle в градусах против часовой, "
    "либо points: три точки [[x,y] начало, [x,y] на дуге, [x,y] конец]."
))
def draw_arc(cx: float | None = None, cy: float | None = None, r: float | None = None,
             start_angle: float = 0, end_angle: float = 90, points: Pts | None = None, layer: str = "0") -> str:
    if points:
        p = _pts(points, 3)
        center, radius, a1, a2 = arc_from_3p(p[0], p[1], p[2])
    else:
        if cx is None or cy is None or not r:
            raise ValueError("Нужны cx, cy, r или points из трёх точек")
        center, radius, a1, a2 = (cx, cy), r, start_angle, end_angle
    h = engine().add_arc(center, radius, a1, a2, layer)
    return f"Дуга r={radius:.1f}, центр ({center[0]:.1f},{center[1]:.1f}), {a1:.1f}°..{a2:.1f}°, handle {h}"


@tool(description="Эллипс: центр (cx,cy), полуоси rx (по X) и ry (по Y), rotation: поворот в градусах.")
def draw_ellipse(cx: float, cy: float, rx: float, ry: float, rotation: float = 0, layer: str = "0") -> str:
    h = engine().add_ellipse((cx, cy), rx, ry, rotation, layer)
    return f"Эллипс {rx}×{ry} в ({cx},{cy}), handle {h}"


@tool(description=(
    "Правильный многоугольник: центр, радиус r, число сторон sides. inscribed=True: r до вершин, "
    "False: r до середины сторон. rotation: угол первой вершины (90 = вершина вверху)."
))
def draw_polygon(cx: float, cy: float, r: float, sides: int, rotation: float = 90, inscribed: bool = True,
                 layer: str = "0") -> str:
    h = engine().add_polyline(polygon_points(cx, cy, r, int(sides), rotation, inscribed), True, layer)
    return f"{sides}-угольник r={r} в ({cx},{cy}), handle {h}"


@tool(description=(
    "Текст в точке (x,y), шрифт Arial (кириллица). height: высота в мм; если не указана, авто по масштабу "
    "(см. set_annotation_scale). align: 'center' или 'left'."
))
def draw_text(x: float, y: float, text: str, height: float | None = None, layer: str = C.L_TEXT, align: str = "left") -> str:
    h = engine().add_text((x, y), text, height, layer, align)
    return f"Текст «{text}» в ({x},{y}), handle {h}"


@tool(description=(
    "Многострочный текст (MTEXT) от точки (x,y) (левый верх; align='center': центр). "
    "Строки через \\n, width: ширина абзаца (0 = без переноса), height: авто, если не указана."
))
def draw_mtext(x: float, y: float, text: str, height: float | None = None, width: float = 0,
               align: str = "left", layer: str = C.L_TEXT) -> str:
    h = engine().add_mtext((x, y), text, height, width, layer, align)
    return f"MTEXT в ({x},{y}), handle {h}"


@tool(description=(
    "Штриховка или заливка замкнутого контура: points [[x,y],...] или handle замкнутой полилинии/окружности/эллипса. "
    "pattern: ANSI31, ANSI37, AR-CONC, AR-B816, BRICK, GRAVEL, NET... или SOLID (заливка); "
    "scale: масштаб узора (для мм-чертежей обычно 10-50), angle: градусы, color: ACI."
))
def draw_hatch(points: Pts | None = None, handle: str | None = None, pattern: str = "ANSI31", scale: float = 20,
               angle: float = 0, color: int | None = None, layer: str = "HATCH") -> str:
    if not points and not handle:
        raise ValueError("Укажите points или handle контура")
    h = engine().add_hatch(_pts(points, 3) if points else None, handle, pattern, scale, angle, color, layer)
    return f"Штриховка {pattern.upper()} на слое {layer}, handle {h}"


@tool(description="Точка (POINT) в (x,y).")
def draw_point(x: float, y: float, layer: str = "0") -> str:
    h = engine().add_point((x, y), layer)
    return f"Точка ({x},{y}), handle {h}"


@tool(description="Сплайн через точки [[x,y],...]. closed: замкнуть.")
def draw_spline(points: Pts, closed: bool = False, layer: str = "0") -> str:
    h = engine().add_spline(_pts(points, 2), closed, layer)
    return f"Сплайн из {len(points)} точек, handle {h}"


# ------------------------------------------------- универсальное черчение

BATCH_MAX = 5000


def _num(item: dict[str, Any], *keys: str, default: float | None = None) -> float:
    for k in keys:
        if item.get(k) is not None:
            return float(item[k])
    if default is None:
        raise ValueError(f"нет параметра {keys[0]}")
    return default


def _xy(item: dict[str, Any], key: str, kx: str, ky: str) -> tuple[float, float]:
    if item.get(key) is not None:
        return (float(item[key][0]), float(item[key][1]))
    return (_num(item, kx), _num(item, ky))


def _draw_one(eng, it: dict[str, Any]) -> str:
    """Один объект пакета: тип и параметры как у отдельных инструментов draw_*."""
    t = str(it.get("type") or it.get("op") or "").lower()
    layer = str(it.get("layer") or ("TEXT" if t in ("text", "mtext") else "0"))
    if t == "line":
        return eng.add_line(_xy(it, "p1", "x1", "y1"), _xy(it, "p2", "x2", "y2"), layer, bool(it.get("dashed")))
    if t == "polyline":
        return eng.add_polyline(_pts(it["points"]), bool(it.get("closed")), layer)
    if t in ("rect", "rectangle"):
        x, y, w, h = _num(it, "x"), _num(it, "y"), _num(it, "w", "width"), _num(it, "h", "height")
        return eng.add_polyline([(x, y), (x + w, y), (x + w, y + h), (x, y + h)], True, layer)
    if t == "circle":
        return eng.add_circle(_xy(it, "center", "cx", "cy"), _num(it, "r", "radius"), layer)
    if t == "arc":
        if it.get("points"):
            p = _pts(it["points"], 3)
            c, r, a1, a2 = arc_from_3p(p[0], p[1], p[2])
        else:
            c, r = _xy(it, "center", "cx", "cy"), _num(it, "r", "radius")
            a1, a2 = _num(it, "start_angle", "a1", default=0), _num(it, "end_angle", "a2", default=90)
        return eng.add_arc(c, r, a1, a2, layer)
    if t == "ellipse":
        return eng.add_ellipse(_xy(it, "center", "cx", "cy"), _num(it, "rx"), _num(it, "ry"),
                               _num(it, "rotation", default=0), layer)
    if t == "polygon":
        pts = polygon_points(_num(it, "cx"), _num(it, "cy"), _num(it, "r"), int(_num(it, "sides")),
                             _num(it, "rotation", default=90), bool(it.get("inscribed", True)))
        return eng.add_polyline(pts, True, layer)
    if t == "point":
        return eng.add_point(_xy(it, "pos", "x", "y"), layer)
    if t == "spline":
        return eng.add_spline(_pts(it["points"]), bool(it.get("closed")), layer)
    if t == "text":
        return eng.add_text(_xy(it, "pos", "x", "y"), str(it["text"]), it.get("height"), layer,
                            str(it.get("align", "left")), _num(it, "rotation", default=0))
    if t == "mtext":
        return eng.add_mtext(_xy(it, "pos", "x", "y"), str(it["text"]), it.get("height"),
                             _num(it, "width", default=0), layer, str(it.get("align", "left")))
    if t in ("dim", "dimension"):
        p1, p2 = _xy(it, "p1", "x1", "y1"), _xy(it, "p2", "x2", "y2")
        off = _num(it, "offset", default=300)
        if it.get("aligned"):
            return eng.add_aligned_dim(p1, p2, off, C.L_DIM, it.get("text"), it.get("text_height"))
        return eng.add_linear_dim(p1, p2, off, 90 if it.get("vertical") else 0, C.L_DIM, it.get("text"),
                                  it.get("text_height"))
    if t == "hatch":
        return eng.add_hatch(_pts(it["points"], 3) if it.get("points") else None, it.get("handle"),
                             str(it.get("pattern", "ANSI31")), _num(it, "scale", default=20),
                             _num(it, "angle", default=0), it.get("color"), str(it.get("layer") or "HATCH"))
    raise ValueError(f"неизвестный тип «{t}»")


def _render(eng, ops: list[dict[str, Any]], text_height: float | None = None) -> list[str]:
    """Примитивы из drafting/constructions, включая дуги."""
    out = []
    for op in ops:
        k = op["op"]
        layer = op.get("layer", "0")
        if k == "line":
            out.append(eng.add_line(op["p1"], op["p2"], layer, op.get("dashed", False)))
        elif k == "arc":
            out.append(eng.add_arc(op["center"], op["r"], op["a1"], op["a2"], layer))
        elif k == "circle":
            out.append(eng.add_circle(op["center"], op["r"], layer))
        elif k == "polyline":
            out.append(eng.add_polyline(op["points"], op.get("closed", True), layer))
        elif k == "text":
            out.append(eng.add_text(op["pos"], op["text"], op.get("height") or text_height, layer,
                                    op.get("align", "center")))
    return out


@tool(description=(
    "ПАКЕТНОЕ РИСОВАНИЕ: много объектов одной командой (до 5000), один шаг отмены. items: список объектов "
    "{type, ...параметры, layer?, color?, linetype?, lineweight?}. Типы и параметры: "
    "line {x1,y1,x2,y2 | p1,p2}; polyline {points,closed}; rect {x,y,w,h}; circle {cx,cy,r}; "
    "arc {cx,cy,r,start_angle,end_angle | points:3}; ellipse {cx,cy,rx,ry,rotation}; polygon {cx,cy,r,sides}; "
    "point {x,y}; spline {points}; text {x,y,text,height?,align?,rotation?}; mtext {x,y,text,width?}; "
    "dim {x1,y1,x2,y2,offset,vertical?,aligned?,text?}; hatch {points,pattern,scale}. "
    "Ошибочные элементы пропускаются, их номера и причины возвращаются."
))
def draw_batch(items: list[dict[str, Any]]) -> str:
    if len(items) > BATCH_MAX:
        raise ValueError(f"За раз не больше {BATCH_MAX} объектов, разбейте на части")
    eng = engine()
    handles, errors = [], []
    with _group(eng):
        for i, it in enumerate(items):
            try:
                h = _draw_one(eng, it)
                if any(it.get(k) is not None for k in ("color", "linetype", "lineweight")) and it.get("type") != "hatch":
                    eng.set_props([h], None, it.get("color"), it.get("linetype"), it.get("lineweight"))
                handles.append(h)
            except Exception as e:  # noqa: BLE001
                errors.append({"index": i, "type": it.get("type"), "error": _friendly(e)})
    res: dict[str, Any] = {"created": len(handles), "handles": handles[:500]}
    if errors:
        res["errors"] = errors[:50]
    return _ok(res)


@tool(description=(
    "МАССИВ копий объектов (handles или фильтр layer/entity_type/area). mode='rect': rows×cols с шагом dx, dy "
    "(исходник в левом нижнем углу). mode='polar': count копий по кругу вокруг (cx,cy) на угол angle "
    "(360 = полный круг), rotate_items=False: копии не поворачиваются. Исходные объекты остаются."
))
def array_entities(mode: str = "rect", handles: list[str] | None = None, layer: str | None = None,
                   entity_type: str | None = None, area: list[float] | None = None,
                   rows: int = 1, cols: int = 1, dx: float = 0, dy: float = 0,
                   count: int = 0, cx: float = 0, cy: float = 0, angle: float = 360,
                   rotate_items: bool = True) -> str:
    import math

    eng = engine()
    hs = _targets(handles, layer, entity_type, area)
    if not hs:
        return "Под фильтр не попало ни одного объекта"
    mode = mode.lower().strip()
    if mode == "rect":
        steps = [(c * dx, r * dy) for r in range(max(1, rows)) for c in range(max(1, cols)) if (r, c) != (0, 0)]
    elif mode == "polar":
        if count < 2:
            raise ValueError("Для polar нужно count >= 2 (вместе с исходным)")
        step = angle / (count if abs(angle) >= 360 else count - 1)
        steps = [i * step for i in range(1, count)]
    else:
        raise ValueError("mode: rect или polar")
    if len(steps) * len(hs) > BATCH_MAX:
        raise ValueError(f"Получится больше {BATCH_MAX} объектов, уменьшите массив")
    new: list[str] = []
    with _group(eng):
        if mode == "rect":
            for sx, sy in steps:
                new += eng.transform(hs, "move", sx, sy, (0, 0), 0, 1, None, None, True)["new_handles"]
        elif rotate_items:
            for a in steps:
                new += eng.transform(hs, "rotate", 0, 0, (cx, cy), a, 1, None, None, True)["new_handles"]
        else:
            boxes = [b for b in (eng.get_entity(h).get("bbox") for h in hs) if b]
            gx = (min(b[0] for b in boxes) + max(b[2] for b in boxes)) / 2 if boxes else cx
            gy = (min(b[1] for b in boxes) + max(b[3] for b in boxes)) / 2 if boxes else cy
            for a in steps:
                r = math.radians(a)
                nx = cx + (gx - cx) * math.cos(r) - (gy - cy) * math.sin(r)
                ny = cy + (gx - cx) * math.sin(r) + (gy - cy) * math.cos(r)
                new += eng.transform(hs, "move", nx - gx, ny - gy, (0, 0), 0, 1, None, None, True)["new_handles"]
    return _ok({"mode": mode, "copies": len(steps), "new_entities": len(new), "new_handles": new[:500]})


def _annot_h(eng, explicit: float | None) -> float:
    return float(explicit) if explicit else float(eng._text_h(None))


@tool(description=(
    "ЦЕПОЧКА РАЗМЕРОВ по точкам [[x,y],...] (по порядку): соседние размеры на одной линии. "
    "direction: 'horizontal' (меряется по X) или 'vertical' (по Y). at: координата размерной линии "
    "(y для horizontal, x для vertical); без неё: на 3 высоты текста ниже/левее точек. "
    "total=True: общий размер дальше от объекта."
))
def add_dimension_chain(points: Pts, direction: str = "horizontal", at: float | None = None,
                        total: bool = True, text_height: float | None = None) -> str:
    eng = engine()
    pts = _pts(points)
    vertical = direction.lower().startswith("v")
    th = _annot_h(eng, text_height)
    pts.sort(key=lambda p: p[1] if vertical else p[0])
    line = at if at is not None else (min(p[0] for p in pts) if vertical else min(p[1] for p in pts)) - th * 3
    ang = 90 if vertical else 0
    hs = []
    with _group(eng):
        for a, b in zip(pts, pts[1:]):
            if abs((b[1] - a[1]) if vertical else (b[0] - a[0])) < 1e-9:
                continue
            off = line - (a[0] if vertical else a[1])
            hs.append(eng.add_linear_dim(a, b, off, ang, C.L_DIM, None, text_height))
        if total and len(pts) > 2:
            a, b = pts[0], pts[-1]
            outer = line - th * 2.5 if line <= (a[0] if vertical else a[1]) else line + th * 2.5
            hs.append(eng.add_linear_dim(a, b, outer - (a[0] if vertical else a[1]), ang, C.L_DIM, None, text_height))
    return _ok({"dimensions": len(hs), "handles": hs})


@tool(description=(
    "СТЕНЫ ПЛАНА по осевой линии points [[x,y],...] толщиной thickness (мм), closed: замкнутый контур. "
    "openings: проёмы [{segment: № участка с 0, offset: от начала участка до края проёма, width, "
    "type: door|window|opening, swing: left|right (сторона открывания двери относительно направления участка), "
    "hinge: start|end}]. Двери с дугой открывания, окна тремя линиями. Слои WALLS, DOORS, WINDOWS."
))
def draw_walls(points: Pts, thickness: float = 200, closed: bool = False,
               openings: list[dict[str, Any]] | None = None) -> str:
    from . import drafting as D

    eng = engine()
    ops = D.walls(_pts(points), thickness, closed, openings)
    for name, color in ((D.L_WALL, 7), (D.L_DOOR, 4), (D.L_WINDOW, 5)):
        eng.ensure_layer(name, color)
    with _group(eng):
        hs = _render(eng, ops)
    n_open = len(openings or [])
    return _ok({"walls_segments": len(points) - (0 if closed else 1), "openings": n_open, "entities": len(hs)})


@tool(description=(
    "КООРДИНАЦИОННЫЕ ОСИ (ГОСТ 21.101): x_spacings: шаги вертикальных осей слева направо (марки 1, 2, 3...), "
    "y_spacings: шаги горизонтальных осей снизу вверх (марки А, Б, В... без Ё, З, Й, О, Х, Ц, Ч, Щ, Ъ, Ы, Ь). "
    "x/y: положение первой оси. Штрихпунктир, марки в кружках, размеры между осями и общий. "
    "both_sides: марки с двух сторон. x_labels/y_labels: свои марки."
))
def draw_axes(x_spacings: list[float], y_spacings: list[float], x: float = 0, y: float = 0,
              text_height: float | None = None, both_sides: bool = False, dimensions: bool = True,
              x_labels: list[str] | None = None, y_labels: list[str] | None = None) -> str:
    from . import drafting as D

    eng = engine()
    span = max(sum(x_spacings or [0]), sum(y_spacings or [0]), 1000)
    th = float(text_height) if text_height else max(_annot_h(eng, None), span / 60)
    ops, info = D.axes(x_spacings, y_spacings, (x, y), th, None, both_sides, x_labels, y_labels)
    eng.set_layer(D.L_AXES, color=1, linetype="CENTER")
    eng.ensure_layer(D.L_AXES_MARK, 7)
    with _group(eng):
        hs = _render(eng, ops, th)
        # штрихпунктир в масштабе листа: высота текста 3.5 мм на бумаге -> масштаб 1:(th/3.5)
        axis_lines = [h for h, op in zip(hs, ops) if op["op"] == "line" and op["layer"] == D.L_AXES]
        eng.set_props(axis_lines, ltscale=round(th / 3.5, 2))
        if dimensions:
            xs, ys = info["x"], info["y"]
            # размеры от внешнего края кружков, чтобы выносные линии не шли через марки
            bottom, left = info["extent"][1], info["extent"][0]
            if len(xs) > 1:
                add_dimension_chain([[v, bottom] for v in xs], "horizontal", bottom - th * 2, len(xs) > 2, th)
            if len(ys) > 1:
                add_dimension_chain([[left, v] for v in ys], "vertical", left - th * 2, len(ys) > 2, th)
    return _ok({"axes_x": info["x_labels"], "axes_y": info["y_labels"], "entities": len(hs),
                "text_height": round(th), "extent": [round(v) for v in info["extent"]]})


# ---------------------------------------------------------- размеры и выноски

@tool(description=(
    "Размер между (x1,y1) и (x2,y2). По умолчанию горизонтальный; vertical=True: вертикальный; "
    "aligned=True: параллельный отрезку (наклонный). offset: смещение размерной линии (для aligned: >0 слева "
    "от направления 1→2). text: своя подпись, text_height: высота текста (иначе авто). Пересекающиеся "
    "подписи разносятся автоматически."
))
def add_dimension(x1: float, y1: float, x2: float, y2: float, offset: float = 300,
                  vertical: bool = False, text: str | None = None, text_height: float | None = None,
                  aligned: bool = False) -> str:
    if aligned:
        h = engine().add_aligned_dim((x1, y1), (x2, y2), offset, C.L_DIM, text, text_height)
        return f"Параллельный размер ({x1},{y1})-({x2},{y2}), handle {h}"
    h = engine().add_linear_dim((x1, y1), (x2, y2), offset, 90 if vertical else 0, C.L_DIM, text, text_height)
    return f"Размер {'верт.' if vertical else 'гор.'} ({x1},{y1})-({x2},{y2}), handle {h}"


@tool(description=(
    "Радиус или диаметр (diameter=True) окружности/дуги: центр (cx,cy) и r, либо handle окружности/дуги. "
    "angle: направление выноски в градусах."
))
def add_radial_dimension(cx: float | None = None, cy: float | None = None, r: float | None = None,
                         handle: str | None = None, diameter: bool = False, angle: float = 45,
                         text: str | None = None, text_height: float | None = None) -> str:
    eng = engine()
    if handle:
        kind, g = eng.geometry(handle)
        if kind not in ("CIRCLE", "ARC"):
            raise ValueError(f"Нужна окружность или дуга, а это {kind}")
        center, radius = g["center"], g["radius"]
    else:
        if cx is None or cy is None or not r:
            raise ValueError("Нужны cx, cy, r или handle")
        center, radius = (cx, cy), r
    h = eng.add_radial_dim(center, radius, angle, diameter, C.L_DIM, text, text_height)
    return f"{'Диаметр' if diameter else 'Радиус'} {radius * (2 if diameter else 1):.0f}, handle {h}"


@tool(description=(
    "Угловой размер: вершина (vx,vy) и точки на сторонах угла (x1,y1), (x2,y2); угол меряется от первой "
    "стороны ко второй против часовой. radius: радиус дуги размера (по умолчанию 0.6 от короткой стороны)."
))
def add_angular_dimension(vx: float, vy: float, x1: float, y1: float, x2: float, y2: float,
                          radius: float | None = None, text: str | None = None,
                          text_height: float | None = None) -> str:
    h = engine().add_angular_dim((vx, vy), (x1, y1), (x2, y2), radius, C.L_DIM, text, text_height)
    return f"Угловой размер в вершине ({vx},{vy}), handle {h}"


@tool(description=(
    "Выноска (мультивыноска) с текстом: points [[x,y] остриё стрелки, ..., [x,y] где текст]. "
    "Текст можно в несколько строк через \\n."
))
def add_leader(points: Pts, text: str, text_height: float | None = None) -> str:
    h = engine().add_leader(_pts(points, 2), text, C.L_DIM, text_height)
    return f"Выноска «{text}», handle {h}"


@tool(description=(
    "Масштаб надписей: высота текста размеров и подписей в мм для всего чертежа. "
    "Для планов зданий задайте крупно (500-1000 мм), для окон 80-120. 0 = авто (≈1/60 габарита, не меньше 80 мм)."
))
def set_annotation_scale(text_height: float) -> str:
    return _ok(engine().set_annotation_scale(text_height or None))


# ------------------------------------------------------------------------ слои

@tool(description="Создать слой или поменять цвет существующего: имя и цвет AutoCAD (ACI 1..255). Рисование на слое цвет не сбрасывает.")
def create_layer(name: str, color: int = 7) -> str:
    engine().ensure_layer(name, color, force=True)
    return f"Слой «{name}» (цвет {color})"


@tool(description=(
    "Настроить слой (создаётся, если нет): on (вкл/выкл), frozen, locked, color (ACI), linetype "
    "(DASHED, CENTER, HIDDEN...: подгружается сам), lineweight (мм, напр. 0.5), current=True: сделать текущим."
))
def set_layer(name: str, on: bool | None = None, frozen: bool | None = None, locked: bool | None = None,
              color: int | None = None, linetype: str | None = None, lineweight: float | None = None,
              current: bool = False) -> str:
    return _ok(engine().set_layer(name, on, frozen, locked, color, linetype, lineweight, current))


@tool(description="Удалить пустой слой (не 0, не Defpoints, не текущий).")
def delete_layer(name: str) -> str:
    return _ok(engine().delete_layer(name))


@tool(description="Список слоёв: цвет, вкл/заморожен/заблокирован, тип линии, текущий.")
def list_layers() -> str:
    return _ok(engine().list_layers())


# ------------------------------------------------- работа с существующими объектами

_FILTER_HELP = ("Фильтр: layer (шаблон, напр. 'WALL*'), entity_type (тип DXF: LINE, LWPOLYLINE, CIRCLE, ARC, "
                "TEXT, MTEXT, DIMENSION, INSERT, HATCH...; через запятую), area [x1,y1,x2,y2] (целиком внутри; "
                "crossing=True: касается).")


@tool(description="Список объектов модели с handle, типом, слоем, цветом и габаритом. " + _FILTER_HELP + " limit: максимум в ответе.")
def list_entities(layer: str | None = None, entity_type: str | None = None, area: list[float] | None = None,
                  crossing: bool = False, limit: int = 100) -> str:
    return _ok(engine().list_entities(layer, entity_type, _area(area), crossing, max(1, int(limit))))


@tool(description="Подробно об объекте по handle: геометрия, текст, атрибуты блока, длина/площадь.")
def get_entity(handle: str) -> str:
    return _ok(engine().get_entity(handle))


@tool(description="Выбрать объекты по фильтру и вернуть их handle (для правки другими инструментами). " + _FILTER_HELP)
def select_entities(layer: str | None = None, entity_type: str | None = None, area: list[float] | None = None,
                    crossing: bool = False) -> str:
    hs = engine().select(layer, entity_type, _area(area), crossing)
    return _ok({"count": len(hs), "handles": hs[:2000]})


@tool(description=(
    "Преобразовать объекты (handles или фильтр layer/entity_type/area). op: move (dx,dy), copy (dx,dy), "
    "rotate (angle° вокруг base_x,base_y), scale (factor от base), mirror (ось axis=[x1,y1,x2,y2]). "
    "copy=True: оставить исходные и изменить копии. Возвращает handle новых объектов."
))
def transform_entities(op: str, handles: list[str] | None = None, layer: str | None = None,
                       entity_type: str | None = None, area: list[float] | None = None,
                       dx: float = 0, dy: float = 0, base_x: float = 0, base_y: float = 0,
                       angle: float = 0, factor: float = 1, axis: list[float] | None = None,
                       copy: bool = False) -> str:
    op = op.lower().strip()
    hs = _targets(handles, layer, entity_type, area)
    if not hs:
        return "Под фильтр не попало ни одного объекта"
    p1 = p2 = None
    if op == "mirror":
        if not axis or len(axis) != 4:
            raise ValueError("Для mirror нужна ось axis=[x1,y1,x2,y2]")
        p1, p2 = (axis[0], axis[1]), (axis[2], axis[3])
    res = engine().transform(hs, op, dx, dy, (base_x, base_y), angle, factor, p1, p2, copy or op == "copy")
    return _ok({"op": op, **res})


@tool(description="Удалить объекты по списку handles или по фильтру (layer/entity_type/area).")
def erase_entities(handles: list[str] | None = None, layer: str | None = None, entity_type: str | None = None,
                   area: list[float] | None = None) -> str:
    hs = _targets(handles, layer, entity_type, area)
    return f"Удалено объектов: {engine().erase(hs)}"


@tool(description=(
    "Сменить свойства объектов (handles или фильтр filter_layer/entity_type/area): layer (перенести на слой), "
    "color (ACI, 256 = по слою), linetype (DASHED, CENTER...), lineweight (мм; -1 по слою), "
    "ltscale: масштаб типа линии (для 1:100 обычно 100, иначе штрихи сливаются)."
))
def set_properties(handles: list[str] | None = None, filter_layer: str | None = None,
                   entity_type: str | None = None, area: list[float] | None = None,
                   layer: str | None = None, color: int | None = None, linetype: str | None = None,
                   lineweight: float | None = None, ltscale: float | None = None) -> str:
    hs = _targets(handles, filter_layer, entity_type, area)
    n = engine().set_props(hs, layer, color, linetype, lineweight, ltscale)
    return f"Изменено объектов: {n}"


@tool(description=(
    "Смещённая копия (OFFSET) отрезка, полилинии, окружности или дуги на distance. Сторону задайте точкой "
    "(side_x, side_y); без неё distance>0: влево от направления / наружу для окружности."
))
def offset_entity(handle: str, distance: float, side_x: float | None = None, side_y: float | None = None) -> str:
    from .common import offset_left
    eng = engine()
    kind, geom = eng.geometry(handle)
    side = (side_x, side_y) if side_x is not None and side_y is not None else None
    d = offset_left(kind, geom, side, distance)
    hs = eng.offset(handle, d)
    return _ok({"new_handles": hs, "distance": d})


@tool(description="Измерить: длина/площадь/периметр объекта по handle, или расстояние между точками (x1,y1)-(x2,y2).")
def measure(handle: str | None = None, x1: float | None = None, y1: float | None = None,
            x2: float | None = None, y2: float | None = None) -> str:
    if handle:
        return _ok(engine().measure(handle))
    if None in (x1, y1, x2, y2):
        raise ValueError("Укажите handle или две точки x1,y1,x2,y2")
    import math
    return _ok({"distance": round(math.dist((x1, y1), (x2, y2)), 3), "dx": x2 - x1, "dy": y2 - y1,
                "angle": round(math.degrees(math.atan2(y2 - y1, x2 - x1)), 3)})


# ----------------------------------------------------------------------- блоки

@tool(description=(
    "Создать блок name из объектов (handles) или из конструкции (kind + params, как в draw_construction). "
    "base_x/base_y: базовая точка. replace=True: исходные объекты заменяются вставкой блока. "
    "attributes: список тегов атрибутов (например ['MARK','NOTE'])."
))
def create_block(name: str, handles: list[str] | None = None, kind: str | None = None,
                 params: dict[str, Any] | None = None, base_x: float = 0, base_y: float = 0,
                 replace: bool = True, attributes: list[str] | None = None) -> str:
    eng = engine()
    with _group(eng):
        if kind:
            handles = eng.render_ops(C.build(kind, params or {}), zoom=False)
        if not handles:
            raise ValueError("Укажите handles или kind+params")
        res = eng.create_block(name, list(handles), (base_x, base_y), replace, attributes)
    return _ok(res)


@tool(description=(
    "Вставить блок: name (или путь к .dwg в COM / .dxf в ezdxf), точка (x,y), scale, rotation°, "
    "attributes: {ТЕГ: значение}."
))
def insert_block(name: str, x: float, y: float, scale: float = 1, rotation: float = 0,
                 attributes: dict[str, str] | None = None) -> str:
    h = engine().insert_block(name, (x, y), scale, rotation, attributes)
    return f"Вставка «{name}» в ({x},{y}), handle {h}"


@tool(description="Список блоков (теги атрибутов, число вставок). include_inserts=True: вставки с handle и значениями атрибутов.")
def list_blocks(include_inserts: bool = False) -> str:
    return _ok(engine().list_blocks(include_inserts))


# ---------------------------------------------------------------------- таблица

@tool(description=(
    "Таблица от левого верхнего угла (x,y): rows [[...],...], header (заголовки колонок), title, "
    "col_widths (мм, иначе по тексту), text_height (иначе авто). В COM родная таблица AutoCAD, в ezdxf линии+текст."
))
def add_table(x: float, y: float, rows: list[list[Any]], header: list[str] | None = None, title: str | None = None,
              col_widths: list[float] | None = None, text_height: float | None = None) -> str:
    data = [[("" if v is None else str(v)) for v in r] for r in rows]
    return _ok(engine().add_table(x, y, header, data, title, col_widths, text_height))


# ---------------------------------------------------------------- листы и вывод

@tool(description=(
    "Лист: рамка формата A4/A3/A2/A1 (landscape) в модели в масштабе 1:scale (0 = подобрать) вокруг "
    "области area [x1,y1,x2,y2] или всего чертежа, с основной надписью ГОСТ 2.104 (форма 1): title "
    "(наименование), number (обозначение), developer, checker, date, organization, sheet/sheets. "
    "После него export_pdf без параметров печатает этот лист."
))
def make_sheet(format: str = "A3", landscape: bool = True, scale: float = 0, area: list[float] | None = None,
               title: str = "", number: str = "", developer: str = "", checker: str = "",
               date: str = "", organization: str = "", sheet: int = 1, sheets: int = 1) -> str:
    eng = engine()
    box = _area(area) or eng.extents()
    if not box:
        raise ValueError("Чертёж пуст: укажите area")
    fields = {"title": title, "number": number, "developer": developer, "checker": checker, "date": date,
              "organization": organization, "sheet": sheet, "sheets": sheets}
    ops, info = S.sheet_ops(format, landscape, scale or None, box, fields)
    eng.render_ops(ops)
    eng.last_sheet = info
    if not info["fits"]:
        info["warning"] = f"Область не помещается в {format} при 1:{info['scale']:g}: возьмите масштаб мельче или формат больше"
    return _ok(info)


@tool(description=(
    "PDF. Без area/format печатает последний лист make_sheet в его масштабе; иначе format (A4..A1), "
    "landscape, area [x1,y1,x2,y2] или весь чертёж, scale 1:N (0 = вписать). monochrome: чёрно-белый. "
    "COM: плоттер DWG To PDF.pc3; ezdxf: matplotlib."
))
def export_pdf(path: str | None = None, format: str | None = None, landscape: bool = True,
               area: list[float] | None = None, scale: float = 0, monochrome: bool = False) -> str:
    eng = engine()
    sheet = eng.last_sheet
    target = _out(path, "drawing", ".pdf")
    if sheet and not area and not format:
        res = eng.export_pdf(target, sheet["frame"], tuple(sheet["paper_mm"]), sheet["scale"], monochrome,
                             paper_name=sheet["format"])
    else:
        fmt = format or "A3"
        res = eng.export_pdf(target, _area(area), S.paper_size(fmt, landscape), scale or None, monochrome,
                             paper_name=fmt.upper())
    return _ok(res)


@tool(description="PNG-превью чертежа (светлый фон) для проверки результата. area [x1,y1,x2,y2] или весь чертёж. Возвращает путь к файлу.")
def render_preview(path: str | None = None, area: list[float] | None = None, width_px: int = 1600) -> str:
    return _ok(engine().render_preview(_out(path, "preview", ".png"), _area(area), int(width_px)))


# ------------------------------------------------------- конструкции (К-Замер)

@tool(description=(
    "Чертёж конструкции по замеру. kind: frame | window | door | vitrazh | balcony | loggia (раздвижное) | partition | generic. "
    "frame (любая схема из конструктора): {width,height,outward,view,frame}, frame = {fill:{t,hinge,dir}} или "
    "{split:{dir:'v'|'h',sizes:[мм],parts:[...],shtulp?}}; t: glass|panel|turn|tiltturn|tilt|tophung|door|slide, "
    "hinge: left|right (петли, вид изнутри), sizes от края рамы до оси импоста. "
    "params (мм): window={width,height,profile,outward,sashes:[{width,type,hinge|handle}]} "
    "(type: Глухое/Поворотное/Поворотно-откидное/Откидное); "
    "door={width,height,side,door_type,outward}; vitrazh={width,height,columns,rows}; "
    "balcony={width,height,door_width,door_side}; loggia={width,height,sections}; прочее={width,height,name}. "
    "view='outside': вид снаружи."
))
def draw_construction(kind: str, params: dict[str, Any]) -> str:
    ops = C.build(kind, params)
    hs = engine().render_ops(ops)
    return _ok({"kind": kind, "entities_created": len(hs), "engine": engine().name, **engine().summary()})


def _draw_orders(orders: list[dict[str, Any]], x: float, y: float, text_height: float | None,
                 spec: bool = True) -> list[dict[str, Any]]:
    eng = engine()
    results = []
    top_y = y  # верх следующего заказа
    first = True
    with _group(eng):
        for o in orders:
            items = O.normalize_items(o["items"])
            if not items:
                continue
            exts = [C.ops_extents(C.build(it["kind"], it["params"])) for it in items]
            size = max(max(e[2] - e[0], e[3] - e[1]) for e in exts)
            max_h = max(e[3] - e[1] for e in exts)
            h = text_height or eng.dim_text or auto_text_height(size)
            y0 = y if first else top_y - (max_h + 180 + 6 * h)
            first = False
            plan = O.plan_order(items, x, y0, h, o.get("customer"))
            created = 0
            for ops in plan["items"]:
                created += len(eng.render_ops(ops, text_height=h, zoom=False))
            created += len(eng.render_ops(plan["labels"], zoom=False))
            res: dict[str, Any] = {"customer": o.get("customer") or {}, "items": len(items),
                                   "entities_created": created, "text_height": h}
            bottom = plan["table"]["y"]
            if spec:
                t = plan["table"]
                tab = eng.add_table(t["x"], t["y"], t["header"], t["rows"], t["title"], None, h)
                res["spec_table"] = tab
                res["spec_rows"] = t["rows"]
                bottom = tab["bbox"][1]
            results.append(res)
            top_y = bottom - 8 * h
    eng.zoom()
    return results


@tool(description=(
    "Заказ из нескольких конструкций одной командой: items [{kind, params, name?, qty?}] (kind/params как в "
    "draw_construction), раскладка в ряд от (x,y), подписи «Поз. N» и таблица-спецификация "
    "(поз., изделие, Ш×В, площадь м², створки/открывание, кол-во). customer {client,address}: в заголовок."
))
def draw_order(items: list[dict[str, Any]], customer: dict[str, Any] | None = None, x: float = 0, y: float = 0,
               text_height: float | None = None, spec: bool = True) -> str:
    res = _draw_orders([{"items": items, "customer": customer or {}}], x, y, text_height, spec)
    return _ok(res[0] if res else "Нет изделий")


@tool(description=(
    "Импорт заказа из «К-Замер»: data = JSON заказа {customer, items:[{templateId, values}]}, файл резервной "
    "копии {app:'k-zamer', orders:[...]} или путь к .json. Строит все изделия и спецификацию. "
    "order_index: какой заказ из бэкапа (по умолчанию все, друг под другом)."
))
def import_order(data: str | dict[str, Any], order_index: int | None = None, x: float = 0, y: float = 0,
                 text_height: float | None = None) -> str:
    orders = O.parse_orders(data)
    if order_index is not None:
        if not 0 <= order_index < len(orders):
            raise ValueError(f"order_index вне диапазона: заказов {len(orders)}")
        orders = [orders[order_index]]
    res = _draw_orders(orders, x, y, text_height)
    return _ok({"orders": len(res), "items": sum(r["items"] for r in res), "details": res})


# ------------------------------------------------------------------ универсальное

@tool(description=(
    "ПРЯМАЯ КОМАНДА AutoCAD (только COM): строка как в командной строке, напр. '_.CHAMFER' или "
    "'_.FILLET _R 50'. Пробел/Enter в конце добавляется сам. Пишите английские имена команд и опций "
    "с префиксом _ (они работают в AutoCAD на любом языке). Выполняется без проверок, используйте, когда "
    "нет подходящего инструмента."
))
def run_command(command: str) -> str:
    return _ok(engine().run_command(command))


@tool(description="Отменить последние действия (только COM): steps шагов UNDO. Одна команда MCP = один шаг.")
def undo(steps: int = 1) -> str:
    return _ok(engine().undo(steps))


@tool(description="Показать весь чертёж (mode='extents') или область (mode='window', area=[x1,y1,x2,y2]).")
def zoom(mode: str = "extents", area: list[float] | None = None) -> str:
    if mode == "window" and not area:
        raise ValueError("Для mode='window' нужна area=[x1,y1,x2,y2]")
    return _ok(engine().zoom(_area(area) if mode == "window" else None))


@tool(description="Очистить чертёж от неиспользуемого (PURGE): пустые слои, неиспользуемые блоки и стили.")
def purge() -> str:
    return _ok(engine().purge())


def main(argv: list[str] | None = None) -> None:
    from . import cli

    cli.run(argv)


if __name__ == "__main__":
    main()
