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
    if "подвес" in t:
        apex = (x + w / 2, y)  # откидная наружу вокруг верхней оси: ручка и вершина снизу
        ops.append(_line(apex, (x, y + h), L_OPEN, outward))
        ops.append(_line(apex, (x + w, y + h), L_OPEN, outward))
    return ops


TILT_BOTTOM = "Откидная, петли снизу"  # открывается внутрь, вокруг нижней оси
TILT_TOP = "Откидная, петли сверху"  # открывается наружу, вокруг верхней оси


def is_top_hinged(transom_type: str) -> bool:
    """Откидная на верхних петлях (наружу)? Понимает и старые названия «Откидная наружу»."""
    t = str(transom_type or "").lower()
    return "сверху" in t or "наруж" in t


def transom_symbol(transom_type: str) -> str:
    """Глухая / Откидная, петли снизу (внутрь) / Откидная, петли сверху (наружу)."""
    if "откид" not in str(transom_type or "").lower():
        return "Глухое"
    return "Подвесное" if is_top_hinged(transom_type) else "Откидное"


def _transom_dashed(p: dict[str, Any]) -> bool:
    """Пунктир фрамуги как у створок: по направлению изделия (петли снизу или сверху задают только ось).
    build() для вида снаружи уже перевернул outward, поэтому это и есть «открывается от смотрящего»."""
    return bool(p.get("outward"))


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
        sym = transom_symbol(str(p.get("transom_type") or ""))
        if sym != "Глухое":
            ops += _opening_symbol(f + 20, hi + 20, W - 2 * f - 40, H - f - hi - 40, sym, "", _transom_dashed(p))
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
    p = {width,height,door_width,door_side(петли, вид изнутри),outward,profile,
         transom_top (фрамуга окна, мм до оси импоста), transom_type (Откидная, петли снизу/сверху; Глухая)}."""
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
    # фрамуга окна: transom_top мм от верха рамы до оси импоста, без замера условно верхние 32 %
    top = float(p.get("transom_top") or 0)
    if top and not 100 < top < H - 300:
        raise ValueError("Фрамуга балконного блока не помещается по высоте")
    ty = H - top if top else iy + ih * 0.68
    ttype = str(p.get("transom_type") or TILT_BOTTOM)
    ops.append(_line((wx, ty), (wx + ww, ty), L_MULLION))
    ops.append(_rect(wx + 20, ty + 20, ww - 40, iy + ih - ty - 40, L_SASH))
    sym = transom_symbol(ttype)
    if sym != "Глухое":
        ops += _opening_symbol(wx + 20, ty + 20, ww - 40, iy + ih - ty - 40, sym, "", _transom_dashed({**p, "transom_type": ttype}))
    if top:
        ops.append(_dim((W, ty), (W, H), 350, 90, text=str(int(round(top)))))
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


# ------------------------------------------------------------ конструктор «К-Замер»
# Схема: {"fill": {"t": ..., "hinge": "left"|"right", "dir": ...}} или
#        {"split": {"dir": "v"|"h", "sizes": [мм...], "parts": [узлы...], "shtulp": bool}}
# Размеры кусков от края рамы до оси импоста; сумма = размер родителя (последний кусок забирает остаток).

_FILL_TYPE = {"turn": "Поворотное", "door": "Поворотное", "tiltturn": "Поворотно-откидное",
              "tilt": "Откидное", "tophung": "Подвесное"}
_FILL_SHORT = {"glass": "глух.", "panel": "панель", "turn": "пов.", "tiltturn": "П/О", "tilt": "отк. п.низ",
               "tophung": "отк. п.верх", "door": "дверь", "slide": "разд.", "wall": "стена"}
# split.sash = тип створки: деление внутри одной створки (импост в створке), части глухие или панель;
# fill.t = "wall": стена, изделия нет (под окном балконного блока), рама обходит её контуром.


def _fit_sizes(sizes: list[float], total: float) -> list[float]:
    head = [round(float(s)) for s in sizes[:-1]]
    rest = round(total) - sum(head)
    if rest >= 50:
        return [*head, rest]
    tot = sum(sizes) or 1
    out = [max(1, round(s * total / tot)) for s in sizes]
    out[-1] = round(total) - sum(out[:-1])
    return out


def _layout(node: dict[str, Any], W: float, H: float) -> dict[str, list[dict]]:
    """Куски, створки с импостом в створке, импосты и деления; мм, y от ВЕРХА рамы (как в приложении).
    Кусок внутри такой створки имеет "sash": номер в списке sashes; импост внутри неё "bar": True."""
    out: dict[str, list[dict]] = {"leaves": [], "sashes": [], "imposts": [], "dims": []}

    def walk(n: dict, x: float, y: float, w: float, h: float, depth: int, sash: int | None) -> None:
        s = n.get("split")
        if not s:
            leaf = {"x": x, "y": y, "w": w, "h": h, "fill": n.get("fill") or {"t": "glass"}}
            if sash is not None:
                leaf["sash"] = sash
            out["leaves"].append(leaf)
            return
        if s.get("sash") and sash is None:
            out["sashes"].append({"x": x, "y": y, "w": w, "h": h, "fill": s["sash"]})
            sash = len(out["sashes"]) - 1
        v = s["dir"] == "v"
        sizes = _fit_sizes(s["sizes"], w if v else h)
        out["dims"].append({"dir": s["dir"], "depth": depth, "start": x if v else y, "sizes": sizes, "box": (x, y, w, h)})
        cur = x if v else y
        for i, sz in enumerate(sizes):
            if i:
                im = {"p1": (cur, y), "p2": (cur, y + h)} if v else {"p1": (x, cur), "p2": (x + w, cur)}
                im["shtulp"] = bool(s.get("shtulp"))
                im["bar"] = sash is not None
                out["imposts"].append(im)
            if v:
                walk(s["parts"][i], cur, y, sz, h, depth + 1, sash)
            else:
                walk(s["parts"][i], x, cur, w, sz, depth + 1, sash)
            cur += sz

    walk(node, 0.0, 0.0, W, H, 0, None)
    return out


def frame_layout(node: dict[str, Any], W: float, H: float) -> tuple[list[dict], list[dict], list[dict]]:
    """Куски, импосты и деления схемы; координаты в мм, y от ВЕРХА рамы (как в приложении)."""
    L = _layout(node, W, H)
    return L["leaves"], L["imposts"], L["dims"]


def frame_fields(node: dict[str, Any], W: float, H: float) -> list[dict]:
    """Поля в порядке чтения: створка с импостом в створке одним полем (с ключом parts)."""
    L = _layout(node, W, H)
    fields = [l for l in L["leaves"] if "sash" not in l]
    for i, s in enumerate(L["sashes"]):
        fields.append({**s, "parts": [l for l in L["leaves"] if l.get("sash") == i]})
    return sorted(fields, key=lambda l: (round(l["y"]), l["x"]))


def frame_area(node: dict[str, Any], W: float, H: float) -> float:
    """Площадь изделия без стен, м²."""
    return sum(l["w"] * l["h"] for l in _layout(node, W, H)["leaves"] if l["fill"].get("t") != "wall") / 1e6


def frame_outline(node: dict[str, Any], W: float, H: float) -> list[tuple[float, float, float, float]]:
    """Отрезки внешнего контура рамы (мм, y от верха): внешний край, кроме участков у стены, и края стены
    со стороны изделия. Без стен это прямоугольник W × H."""
    leaves = _layout(node, W, H)["leaves"]
    walls = [l for l in leaves if l["fill"].get("t") == "wall"]

    def near(a: float, b: float) -> bool:
        return abs(a - b) < 0.5

    out: list[tuple[float, float, float, float]] = []
    for l in leaves:
        if l["fill"].get("t") == "wall":
            continue
        x, y, w, h = l["x"], l["y"], l["w"], l["h"]
        for x1, y1, x2, y2, d in ((x, y, x + w, y, "h"), (x, y + h, x + w, y + h, "h"), (x, y, x, y + h, "v"), (x + w, y, x + w, y + h, "v")):
            if (d == "h" and (near(y1, 0) or near(y1, H))) or (d == "v" and (near(x1, 0) or near(x1, W))):
                out.append((x1, y1, x2, y2))
                continue
            for wl in walls:
                if d == "h" and (near(wl["y"], y1) or near(wl["y"] + wl["h"], y1)):
                    a, b = max(x1, wl["x"]), min(x2, wl["x"] + wl["w"])
                    if b - a > 0.5:
                        out.append((a, y1, b, y1))
                if d == "v" and (near(wl["x"], x1) or near(wl["x"] + wl["w"], x1)):
                    a, b = max(y1, wl["y"]), min(y2, wl["y"] + wl["h"])
                    if b - a > 0.5:
                        out.append((x1, a, x1, b))
    return out


def frame_short(node: dict[str, Any], W: float, H: float) -> str:
    fields = frame_fields(node, W, H)
    parts = []
    for l in fields:
        f = l["fill"]
        s = _FILL_SHORT.get(f.get("t"), "?")
        if f.get("hinge"):
            s += " п.прав." if f["hinge"] == "right" else " п.лев."
        if f.get("passive") is not None:
            s += " пасс." if f["passive"] else " раб."
        if l.get("parts"):
            s += " с имп."
        parts.append(s)
    return f"{len(fields)} пол.: " + ", ".join(parts)


def _frame_with_walls(node: dict[str, Any], W: float, H: float, f: float) -> list[Op]:
    """Рама со стеной: внешний контур и внутренний на f внутрь изделия; углы по пересечению смещённых соседей."""
    segs = frame_outline(node, W, H)
    solid = [l for l in _layout(node, W, H)["leaves"] if l["fill"].get("t") != "wall"]

    def near(a: float, b: float) -> bool:
        return abs(a - b) < 0.5

    def inside(px: float, py: float) -> bool:
        return any(l["x"] < px < l["x"] + l["w"] and l["y"] < py < l["y"] + l["h"] for l in solid)

    def shift(sg: tuple[float, float, float, float]) -> float:
        x1, y1, x2, y2 = sg
        if y1 == y2:
            return y1 + f if inside((x1 + x2) / 2, y1 + 1) else y1 - f
        return x1 + f if inside(x1 + 1, (y1 + y2) / 2) else x1 - f

    ops: list[Op] = []
    for sg in segs:
        x1, y1, x2, y2 = sg
        horiz = y1 == y2

        def end(px: float, py: float, fallback: float) -> float:
            for s in segs:
                if s is not sg and (s[1] == s[3]) != horiz and ((near(s[0], px) and near(s[1], py)) or (near(s[2], px) and near(s[3], py))):
                    return shift(s)
            return fallback

        o = shift(sg)
        ops.append(_line((x1, H - y1), (x2, H - y2), L_FRAME))
        if horiz:
            ops.append(_line((end(x1, y1, x1), H - o), (end(x2, y2, x2), H - o), L_FRAME))
        else:
            ops.append(_line((o, H - end(x1, y1, y1)), (o, H - end(x2, y2, y2)), L_FRAME))
    return ops


def build_frame(p: dict[str, Any]) -> list[Op]:
    """Изделие из конструктора «К-Замер». p = {width, height, frame (схема или JSON), outward, view, profile}."""
    import json as _json

    W = float(p.get("width") or 0)
    H = float(p.get("height") or 0)
    if W <= 0 or H <= 0:
        raise ValueError("width и height обязательны и > 0")
    node = p.get("frame")
    if isinstance(node, str):
        node = _json.loads(node)
    if not isinstance(node, dict):
        raise ValueError("frame: схема изделия из конструктора")
    f = float(p.get("profile") or 60)
    outside = is_outside_view(p)
    # build() для вида снаружи переворачивает outward; здесь нужно реальное направление
    item_out = bool(p.get("outward")) != outside
    L = _layout(node, W, H)
    leaves, imposts, dims = L["leaves"], L["imposts"], L["dims"]
    has_wall = any(l["fill"].get("t") == "wall" for l in leaves)
    ops: list[Op] = _frame_with_walls(node, W, H, f) if has_wall else [_rect(0, 0, W, H, L_FRAME), _rect(f, f, W - 2 * f, H - 2 * f, L_FRAME)]
    cx = lambda x: max(f, min(W - f, x))  # noqa: E731
    cy = lambda y: max(f, min(H - f, y))  # noqa: E731
    for im in imposts:
        (x1, y1), (x2, y2) = im["p1"], im["p2"]
        ops.append(_line((cx(x1), H - cy(y1)), (cx(x2), H - cy(y2)), L_SASH if im["shtulp"] or im["bar"] else L_MULLION, im["shtulp"]))

    def sash_symbol(x1: float, x2: float, y1: float, y2: float, fill: dict) -> None:
        """Символ открывания, ручка и подпись двери на всю створку (вид изнутри)."""
        t = fill.get("t")
        sx, sy, sw, sh = x1 + 20, H - y2 + 20, x2 - x1 - 40, y2 - y1 - 40
        dashed = item_out != outside  # все открывающиеся по направлению изделия, петли задают только ось
        hinge = fill.get("hinge") or "left"
        handle = "Левая" if hinge == "right" else "Правая"
        nonlocal ops
        ops += _opening_symbol(sx, sy, sw, sh, _FILL_TYPE[t], handle, dashed)
        passive = fill.get("passive")  # створка штульпа: True пассивная (без ручки), False рабочая
        if t in ("turn", "tiltturn", "door") and not passive:
            hx = (x2 - 45) if handle == "Правая" else (x1 + 45)
            ops += _handle(hx, sy + sh / 2)
        if t == "door":
            word = "дверь" if passive is None else ("пассивная" if passive else "рабочая")
            ops.append(_text((sx + sw / 2, sy + min(sh, sw) * 0.12), word, min(sw, sh) * 0.08, L_TEXT))

    # створки с импостом в створке: контур и символ на всю створку, части рисуются ниже как глухие/панели
    for s in L["sashes"]:
        x1, x2, y1, y2 = cx(s["x"]), cx(s["x"] + s["w"]), cy(s["y"]), cy(s["y"] + s["h"])
        if x2 - x1 > 40 and y2 - y1 > 40:
            ops.append(_rect(x1 + 20, H - y2 + 20, x2 - x1 - 40, y2 - y1 - 40, L_SASH))
            sash_symbol(x1, x2, y1, y2, s["fill"])
    for l in leaves:
        x1, x2 = cx(l["x"]), cx(l["x"] + l["w"])
        y1, y2 = cy(l["y"]), cy(l["y"] + l["h"])
        sx, sy, sw, sh = x1 + 20, H - y2 + 20, x2 - x1 - 40, y2 - y1 - 40
        if sw <= 0 or sh <= 0:
            continue
        fill = l["fill"]
        t = fill.get("t", "glass")
        if t == "wall":
            ops.append(_text((sx + sw / 2, sy + sh / 2), "стена", min(sw, sh) * 0.1, L_TEXT))
            continue
        ops.append(_rect(sx, sy, sw, sh, L_SASH))
        if t == "glass" and "sash" in l:
            continue  # стекло внутри открывающейся створки не «глухое»
        if t in ("glass", "panel"):
            ops.append(_text((sx + sw / 2, sy + sh / 2), "глухое" if t == "glass" else "панель", min(sw, sh) * 0.1, L_TEXT))
            continue
        if t == "slide":
            a = min(sw, sh) * 0.08
            y = sy + sh / 2
            fr_x, to_x = (sx + sw * 0.25, sx + sw * 0.75) if fill.get("dir") == "right" else (sx + sw * 0.75, sx + sw * 0.25)
            ops += _arrow(fr_x, to_x, y, a)
            if fill.get("dir") == "both":  # в обе стороны: второй наконечник без повтора линии
                ops += _arrow(to_x, fr_x, y, a)[1:]
            continue
        sash_symbol(x1, x2, y1, y2, fill)
    # размеры кусков с той стороны, к которой прилегает деление
    def near(a: float, b: float) -> bool:
        return abs(a - b) < 0.5

    for d in dims:
        x, y, w, h = d["box"]
        cur = d["start"]
        for sz in d["sizes"]:
            a, b = cur, cur + sz
            cur = b
            label = str(int(round(sz)))
            if d["dir"] == "v" and near(y, 0):
                ops.append(_dim((a, H), (b, H), 180 + d["depth"] * 250, 0, text=label))
            elif d["dir"] == "v" and near(y + h, H):
                ops.append(_dim((a, 0), (b, 0), -180 - d["depth"] * 250, 0, text=label))
            elif d["dir"] == "h" and near(x + w, W):
                ops.append(_dim((W, H - b), (W, H - a), 180 + d["depth"] * 250, 90, text=label))
            elif d["dir"] == "h" and near(x, 0):
                ops.append(_dim((0, H - b), (0, H - a), -180 - d["depth"] * 250, 90, text=label))
    ops.append(_dim((0, 0), (W, 0), -650, 0))
    ops.append(_dim((0, 0), (0, H), -650, 90))
    return ops


BUILDERS = {
    "frame": build_frame,
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
