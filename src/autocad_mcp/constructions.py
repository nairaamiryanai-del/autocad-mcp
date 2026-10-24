"""Геометрия конструкций (окна/двери/витражи) в реальных мм, Y вверх.

Модуль движко-независимый: строит список примитивов (ops), которые
отрисовывает любой движок (ezdxf или COM). Это же используется для
генерации чертежей из данных заказа К-Замер.

Каждый примитив — dict:
  {"op":"line","p1":(x,y),"p2":(x,y),"layer":str,"dashed":bool}
  {"op":"polyline","points":[(x,y)...],"closed":bool,"layer":str}
  {"op":"circle","center":(x,y),"r":float,"layer":str}
  {"op":"text","pos":(x,y),"text":str,"height":float,"layer":str,"align":"center"|"left"}
  {"op":"dim","p1":(x,y),"p2":(x,y),"offset":float,"angle":0|90,"layer":str}
"""

from __future__ import annotations

from typing import Any

# слои
L_FRAME = "FRAME"
L_MULLION = "MULLION"
L_SASH = "SASH"
L_OPEN = "OPEN"
L_HANDLE = "HANDLE"
L_DIM = "DIM"
L_TEXT = "TEXT"

Op = dict[str, Any]
Point = tuple[float, float]


def _line(p1: Point, p2: Point, layer: str, dashed: bool = False) -> Op:
    return {"op": "line", "p1": p1, "p2": p2, "layer": layer, "dashed": dashed}


def _poly(points: list[Point], layer: str, closed: bool = True) -> Op:
    return {"op": "polyline", "points": points, "closed": closed, "layer": layer}


def _text(pos: Point, text: str, height: float, layer: str, align: str = "center") -> Op:
    return {"op": "text", "pos": pos, "text": text, "height": height, "layer": layer, "align": align}


def _dim(p1: Point, p2: Point, offset: float, angle: int, layer: str = L_DIM,
         text: str | None = None) -> Op:
    op: Op = {"op": "dim", "p1": p1, "p2": p2, "offset": offset, "angle": angle, "layer": layer}
    if text is not None:
        op["text"] = text
    return op


def _rect(x: float, y: float, w: float, h: float, layer: str) -> Op:
    return _poly([(x, y), (x + w, y), (x + w, y + h), (x, y + h)], layer, True)


def _handle(x: float, y: float, layer: str = L_HANDLE) -> list[Op]:
    """Метка ручки: короткая вертикальная чёрточка ~120мм."""
    return [_line((x, y - 60), (x, y + 60), layer)]


def _opening_symbol(x: float, y: float, w: float, h: float, typ: str,
                    handle: str, outward: bool) -> list[Op]:
    """Символы открывания по стандарту (вид изнутри).

    Поворот: вершина со стороны ручки. Откид: вершина сверху.
    Наружу: пунктир.
    """
    t = typ.lower()
    turn = "поворот" in t
    tilt = "откид" in t
    ops: list[Op] = []
    hand_right = "прав" in handle.lower()
    if turn:
        if hand_right:
            apex = (x + w, y + h / 2)
            ops.append(_line(apex, (x, y + h), L_OPEN, outward))
            ops.append(_line(apex, (x, y), L_OPEN, outward))
        else:
            apex = (x, y + h / 2)
            ops.append(_line(apex, (x + w, y + h), L_OPEN, outward))
            ops.append(_line(apex, (x + w, y), L_OPEN, outward))
    if tilt:
        apex = (x + w / 2, y + h)  # вершина сверху
        ops.append(_line(apex, (x, y), L_OPEN, outward))
        ops.append(_line(apex, (x + w, y), L_OPEN, outward))
    return ops


def _transoms(p: dict[str, Any], W: float, H: float, f: float, outward: bool) -> tuple[float, float, list[Op]]:
    """Горизонтальные импосты: transom_top (фрамуга сверху, мм от верха рамы до оси импоста),
    transom_type (Глухая/Откидная), bottom_part (глухая часть снизу). Возвращает низ и верх основной части."""
    top = float(p.get("transom_top") or 0)
    bottom = float(p.get("bottom_part") or 0)
    if top < 0 or bottom < 0 or top + bottom >= H - 2 * f - 100:
        raise ValueError("Фрамуга и нижняя часть занимают всю высоту изделия")
    lo = bottom if bottom else f
    hi = H - top if top else H - f
    ops: list[Op] = []
    if top:
        ops.append(_line((f, hi), (W - f, hi), L_MULLION))
        ops.append(_rect(f + 20, hi + 20, W - 2 * f - 40, H - f - hi - 40, L_SASH))
        if "откид" in str(p.get("transom_type") or "").lower():
            ops += _opening_symbol(f + 20, hi + 20, W - 2 * f - 40, H - f - hi - 40, "Откидное", "", outward)
        ops.append(_dim((W, hi), (W, H), 350, 90, text=str(int(round(top)))))
    if bottom:
        ops.append(_line((f, lo), (W - f, lo), L_MULLION))
        ops.append(_rect(f + 20, f + 20, W - 2 * f - 40, lo - f - 40, L_SASH))
        ops.append(_dim((W, 0), (W, lo), 350, 90, text=str(int(round(bottom)))))
    return lo, hi, ops


def mirror_ops(ops: list[Op], W: float) -> list[Op]:
    """Вид снаружи: зеркало по X относительно середины изделия шириной W."""
    mx = lambda pt: (W - pt[0], pt[1])  # noqa: E731
    out: list[Op] = []
    for op in ops:
        if op["op"] == "dim" and op.get("angle") == 90:
            # вертикальные размеры стоят у краёв рамы и меряют высоты: от вида не зависят
            out.append(op)
            continue
        o = dict(op)
        for k in ("p1", "p2", "pos", "center"):
            if k in o:
                o[k] = mx(o[k])
        if "points" in o:
            o["points"] = [mx(pt) for pt in o["points"]]
        out.append(o)
    return out


def build_window(p: dict[str, Any]) -> list[Op]:
    """Окно с несколькими створками.

    p = {width, height, profile(мм)=60, outward=False,
         sashes=[{width, type, handle}]}  (type: Глухое/Поворотное/Поворотно-откидное; handle: сторона ручки,
         вместо неё можно hinge: сторона петель, вид изнутри),
         transom_top, transom_type, bottom_part: горизонтальные импосты
    """
    W = float(p.get("width") or 0)
    H = float(p.get("height") or 0)
    if W <= 0 or H <= 0:
        raise ValueError("width и height обязательны и > 0")
    f = float(p.get("profile") or 60)
    outward = bool(p.get("outward"))
    sashes = p.get("sashes") or [{"type": "Поворотно-откидное", "handle": "Правая"}]
    n = len(sashes)

    ops: list[Op] = []
    # рама (наружный + внутренний контур)
    ops.append(_rect(0, 0, W, H, L_FRAME))
    ops.append(_rect(f, f, W - 2 * f, H - 2 * f, L_FRAME))
    lo, hi, tops = _transoms(p, W, H, f, outward)
    ops += tops

    # створки стоят между горизонтальными импостами
    ix, iy, iw, ih = f, lo, W - 2 * f, hi - lo

    # ширины створок: если заданы все — по ним, иначе поровну
    widths = [float(s.get("width") or 0) for s in sashes]
    if n > 1 and all(w > 0 for w in widths):
        total = sum(widths)
        cws = [iw * w / total for w in widths]
    else:
        cws = [iw / n] * n
        widths = [W / n] * n  # для подписей, приблизительно

    xcur = ix
    for i, s in enumerate(sashes):
        cw = cws[i]
        typ = str(s.get("type") or "Поворотно-откидное")
        hinge = str(s.get("hinge") or "").lower()
        # ручка напротив петель; без петель и ручки: по умолчанию как раньше
        default = "Правая" if i == 0 else "Левая"
        handle = str(s.get("handle") or (("Правая" if "лев" in hinge else "Левая") if hinge else default))
        # створка (рамка)
        ops.append(_rect(xcur + 20, iy + 20, cw - 40, ih - 40, L_SASH))
        # символ открывания
        if typ.lower() != "глухое":
            ops += _opening_symbol(xcur + 20, iy + 20, cw - 40, ih - 40, typ, handle, outward)
            hx = (xcur + cw - 60) if "прав" in handle.lower() else (xcur + 60)
            ops += _handle(hx, iy + ih / 2)
        else:
            ops.append(_text((xcur + cw / 2, iy + ih / 2), "глухое", min(ih, cw) * 0.1, L_TEXT))
        # импост
        if i > 0:
            ops.append(_line((xcur, iy), (xcur, iy + ih), L_MULLION))
        # размер створки сверху (подпись — реальная ширина створки)
        if n > 1:
            ops.append(_dim((xcur, H), (xcur + cw, H), 180, 0, text=str(int(round(widths[i])))))
        xcur += cw

    # общие размеры
    ops.append(_dim((0, 0), (W, 0), -350, 0))   # ширина снизу
    ops.append(_dim((0, 0), (0, H), -350, 90))  # высота слева
    return ops


def build_door(p: dict[str, Any]) -> list[Op]:
    """Дверь. p = {width,height,profile,outward, side (петли, вид изнутри: Слева/Справа), door_type,
    transom_top, transom_type, bottom_part}."""
    W = float(p.get("width") or 0)
    H = float(p.get("height") or 0)
    if W <= 0 or H <= 0:
        raise ValueError("width и height обязательны и > 0")
    f = float(p.get("profile") or 70)
    outward = bool(p.get("outward"))
    side = str(p.get("side") or "Левая")
    dtype = str(p.get("door_type") or "Распашная")
    ops: list[Op] = []
    ops.append(_rect(0, 0, W, H, L_FRAME))
    ops.append(_rect(f, f, W - 2 * f, H - 2 * f, L_FRAME))
    lo, hi, tops = _transoms(p, W, H, f, outward)
    ops += tops
    ix, iy, iw, ih = f, lo, W - 2 * f, hi - lo

    if "штульп" in dtype.lower():
        # двупольная: две створки, ручки к центру, штульп — пунктир по центру
        half = iw / 2
        for j, (hx0, hand) in enumerate([(ix, "Правая"), (ix + half, "Левая")]):
            ops.append(_rect(hx0 + 20, iy + 20, half - 40, ih - 40, L_SASH))
            ops += _opening_symbol(hx0 + 20, iy + 20, half - 40, ih - 40, "Поворотное", hand, outward)
        ops.append(_line((ix + half, iy), (ix + half, iy + ih), L_OPEN, True))
        ops += _handle(ix + half - 50, iy + ih / 2)
        ops += _handle(ix + half + 50, iy + ih / 2)
    else:
        handle = "Правая" if "лев" in side.lower() else "Левая"
        ops.append(_rect(ix + 20, iy + 20, iw - 40, ih - 40, L_SASH))
        ops += _opening_symbol(ix + 20, iy + 20, iw - 40, ih - 40, "Поворотное", handle, outward)
        hx = (ix + iw - 60) if "прав" in handle.lower() else (ix + 60)
        ops += _handle(hx, iy + ih / 2)

    ops.append(_dim((0, 0), (W, 0), -350, 0))
    ops.append(_dim((0, 0), (0, H), -350, 90))
    return ops


def build_vitrazh(p: dict[str, Any]) -> list[Op]:
    """Витраж/фасад: сетка стоек x рядов. p = {width,height,columns,rows,profile}."""
    W = float(p.get("width") or 0)
    H = float(p.get("height") or 0)
    if W <= 0 or H <= 0:
        raise ValueError("width и height обязательны и > 0")
    f = float(p.get("profile") or 50)
    cols = max(1, int(p.get("columns") or 3))
    rows = max(1, int(p.get("rows") or 1))
    ops: list[Op] = []
    ops.append(_rect(0, 0, W, H, L_FRAME))
    ops.append(_rect(f, f, W - 2 * f, H - 2 * f, L_FRAME))
    ix, iy, iw, ih = f, f, W - 2 * f, H - 2 * f
    cw = iw / cols
    rh = ih / rows
    for c in range(1, cols):
        ops.append(_line((ix + cw * c, iy), (ix + cw * c, iy + ih), L_MULLION))
    for r in range(1, rows):
        ops.append(_line((ix, iy + rh * r), (ix + iw, iy + rh * r), L_MULLION))
    ops.append(_dim((0, 0), (W, 0), -350, 0))
    ops.append(_dim((0, 0), (0, H), -350, 90))
    return ops


def _arrow(x1: float, x2: float, y: float, size: float, layer: str = L_OPEN) -> list[Op]:
    """Горизонтальная стрелка с наконечником в x2."""
    d = size if x2 > x1 else -size
    return [_line((x1, y), (x2, y), layer), _line((x2, y), (x2 - d, y + size * 0.5), layer),
            _line((x2, y), (x2 - d, y - size * 0.5), layer)]


def build_sliding(p: dict[str, Any]) -> list[Op]:
    """Раздвижное остекление (лоджия). p = {width,height,sections,profile}.
    Секции со стрелками сдвига (чётные влево, нечётные вправо)."""
    W = float(p.get("width") or p.get("length") or 0)
    H = float(p.get("height") or 0)
    if W <= 0 or H <= 0:
        raise ValueError("width (length) и height обязательны и > 0")
    f = float(p.get("profile") or 50)
    n = max(2, min(8, int(p.get("sections") or 3)))
    ops: list[Op] = [_rect(0, 0, W, H, L_FRAME), _rect(f, f, W - 2 * f, H - 2 * f, L_FRAME)]
    ix, iy, iw, ih = f, f, W - 2 * f, H - 2 * f
    cw = iw / n
    for i in range(n):
        x = ix + cw * i
        ops.append(_rect(x + 15, iy + 15, cw - 30, ih - 30, L_SASH))
        if i > 0:
            ops.append(_line((x, iy), (x, iy + ih), L_MULLION))
        a, b = (x + cw * 0.7, x + cw * 0.3) if i % 2 == 0 else (x + cw * 0.3, x + cw * 0.7)
        ops += _arrow(a, b, iy + ih / 2, min(cw, ih) * 0.08)
    ops.append(_dim((0, 0), (W, 0), -350, 0))
    ops.append(_dim((0, 0), (0, H), -350, 90))
    return ops


def build_balcony(p: dict[str, Any]) -> list[Op]:
    """Балконный блок: дверь слева + окно (откидная фрамуга сверху, глухое снизу).
    p = {width,height,door_width,door_side(петли Левое/Правое),outward,profile}."""
    W = float(p.get("width") or 0)
    H = float(p.get("height") or 0)
    if W <= 0 or H <= 0:
        raise ValueError("width и height обязательны и > 0")
    f = float(p.get("profile") or 60)
    outward = bool(p.get("outward"))
    dw = float(p.get("door_width") or W * 0.4)
    dw = max(W * 0.05, min(W * 0.95, dw))
    hinge_right = "прав" in str(p.get("door_side") or "").lower()
    ops: list[Op] = [_rect(0, 0, W, H, L_FRAME), _rect(f, f, W - 2 * f, H - 2 * f, L_FRAME)]
    ix, iy, iw, ih = f, f, W - 2 * f, H - 2 * f
    door_w = dw - f  # внутренняя ширина двери до импоста
    handle = "Левая" if hinge_right else "Правая"
    ops.append(_rect(ix + 20, iy + 20, door_w - 40, ih - 40, L_SASH))
    ops += _opening_symbol(ix + 20, iy + 20, door_w - 40, ih - 40, "Поворотно-откидное", handle, outward)
    ops += _handle((ix + door_w - 60) if handle == "Правая" else (ix + 60), iy + ih / 2)
    wx = ix + door_w
    ops.append(_line((wx, iy), (wx, iy + ih), L_MULLION))
    ww = ix + iw - wx
    ty = iy + ih * 0.68  # фрамуга: верхние 32 %
    ops.append(_line((wx, ty), (wx + ww, ty), L_MULLION))
    ops.append(_rect(wx + 20, ty + 20, ww - 40, iy + ih - ty - 40, L_SASH))
    ops += _opening_symbol(wx + 20, ty + 20, ww - 40, iy + ih - ty - 40, "Откидное", "", outward)
    ops.append(_text((wx + ww / 2, iy + (ty - iy) / 2), "глухое", min(ww, ty - iy) * 0.1, L_TEXT))
    ops.append(_dim((0, H), (dw, H), 180, 0))
    ops.append(_dim((dw, H), (W, H), 180, 0))
    ops.append(_dim((0, 0), (W, 0), -350, 0))
    ops.append(_dim((0, 0), (0, H), -350, 90))
    return ops


def build_rectangle(p: dict[str, Any]) -> list[Op]:
    """Простое изделие рамкой + размеры. p = {width,height,profile,name}."""
    W = float(p.get("width") or 0)
    H = float(p.get("height") or 0)
    if W <= 0 or H <= 0:
        raise ValueError("width и height обязательны и > 0")
    f = float(p.get("profile") or 50)
    ops = [_rect(0, 0, W, H, L_FRAME), _rect(f, f, W - 2 * f, H - 2 * f, L_FRAME)]
    ops += _transoms(p, W, H, f, bool(p.get("outward")))[2]
    name = p.get("name")
    if name:
        th = min(H * 0.08, (W - 2 * f) * 0.85 / (len(str(name)) * 0.72))  # влезть в ширину
        ops.append(_text((W / 2, H / 2), str(name), th, L_TEXT))
    ops.append(_dim((0, 0), (W, 0), -350, 0))
    ops.append(_dim((0, 0), (0, H), -350, 90))
    return ops


BUILDERS = {
    "window": build_window,
    "door": build_door,
    "vitrazh": build_vitrazh,
    "balcony": build_balcony,
    "loggia": build_sliding,
    "sliding": build_sliding,
    "partition": build_rectangle,
    "generic": build_rectangle,
}


def is_outside_view(params: dict[str, Any]) -> bool:
    return str(params.get("view") or "").lower() in ("outside", "снаружи", "вид снаружи")


def build(kind: str, params: dict[str, Any]) -> list[Op]:
    """params.view = 'outside' / 'снаружи': вид с улицы. Геометрия строится в виде изнутри и отражается;
    пунктиром всегда створка, открывающаяся от смотрящего (с улицы это открывание внутрь)."""
    builder = BUILDERS.get(kind)
    if not builder:
        raise ValueError(f"Неизвестный тип конструкции: {kind}. Доступно: {', '.join(BUILDERS)}")
    if not is_outside_view(params):
        return builder(params)
    p = {**params, "outward": not bool(params.get("outward"))}
    return mirror_ops(builder(p), float(p.get("width") or p.get("length") or 0))


def _shift_pt(p, dx: float, dy: float):
    return (p[0] + dx, p[1] + dy)


def shift_ops(ops: list[Op], dx: float, dy: float) -> list[Op]:
    """Сдвинуть примитивы (для раскладки нескольких изделий)."""
    out: list[Op] = []
    for op in ops:
        o = dict(op)
        for k in ("p1", "p2", "pos", "center"):
            if k in o:
                o[k] = _shift_pt(o[k], dx, dy)
        if "points" in o:
            o["points"] = [_shift_pt(p, dx, dy) for p in o["points"]]
        out.append(o)
    return out


def ops_extents(ops: list[Op]) -> tuple[float, float, float, float]:
    """Габарит геометрии (без размеров и текста)."""
    xs: list[float] = []
    ys: list[float] = []
    for op in ops:
        pts = op.get("points") or [op[k] for k in ("p1", "p2") if k in op and op["op"] == "line"]
        for x, y in pts:
            xs.append(x)
            ys.append(y)
    return (min(xs), min(ys), max(xs), max(ys)) if xs else (0.0, 0.0, 0.0, 0.0)
