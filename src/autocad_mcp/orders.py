"""Заказы: несколько конструкций в ряд + спецификация, импорт JSON из «К-Замер».

Формат «К-Замер»:
  заказ  {customer:{client,phone,address,comment}, items:[{templateId, values:{...}}]}
  бэкап  {app:"k-zamer", orders:[заказ, ...]}
"""

from __future__ import annotations

import json
import os
from typing import Any

from . import constructions as C

KIND_NAMES = {
    "window": "Окно", "door": "Дверь", "vitrazh": "Витраж", "balcony": "Балконный блок",
    "loggia": "Раздвижное остекление", "sliding": "Раздвижное остекление",
    "partition": "Перегородка", "generic": "Изделие",
}

SPEC_HEADER = ["Поз.", "Изделие", "Ш×В, мм", "Площадь, м²", "Створки / открывание", "Кол-во"]


def _num(v: Any) -> float:
    try:
        return float(str(v).replace(",", ".").replace(" ", ""))
    except (TypeError, ValueError):
        return 0.0


def _outward(v: Any) -> bool:
    return "наруж" in str(v or "").lower()


def _common(v: dict[str, Any]) -> dict[str, Any]:
    """Вид и горизонтальные импосты из замера «К-Замер»."""
    out: dict[str, Any] = {}
    if str(v.get("view") or "") == "Снаружи":
        out["view"] = "outside"
    if _num(v.get("transomTop")) > 0:
        out["transom_top"] = _num(v.get("transomTop"))
        out["transom_type"] = str(v.get("transomType") or "Глухая")
    if _num(v.get("bottomPart")) > 0:
        out["bottom_part"] = _num(v.get("bottomPart"))
    return out


def kzamer_item(template_id: str, values: dict[str, Any]) -> dict[str, Any]:
    """Изделие «К-Замер» -> {kind, params, name}."""
    v = values or {}
    t = (template_id or "generic").lower()
    if t == "window":
        n = max(1, min(6, int(_num(v.get("sashes")) or 1)))
        sashes = []
        for i in range(1, n + 1):
            s: dict[str, Any] = {"type": str(v.get(f"s{i}_type") or "Поворотно-откидное")}
            if _num(v.get(f"s{i}_w")) > 0:
                s["width"] = _num(v.get(f"s{i}_w"))
            if v.get(f"s{i}_hinge"):  # петли, вид изнутри (с октября 2026)
                s["hinge"] = str(v[f"s{i}_hinge"])
            elif v.get(f"s{i}_handle"):  # старые замеры: сторона ручки
                s["handle"] = str(v[f"s{i}_handle"])
            sashes.append(s)
        params = {"width": _num(v.get("width")), "height": _num(v.get("height")),
                  "sashes": sashes, "outward": _outward(v.get("direction")), **_common(v)}
        return {"kind": "window", "params": params, "name": "Окно"}
    if t == "door":
        params = {"width": _num(v.get("width")), "height": _num(v.get("height")),
                  "side": str(v.get("side") or "Левая"), "door_type": str(v.get("doorType") or "Распашная"),
                  "outward": _outward(v.get("direction")), **_common(v)}
        return {"kind": "door", "params": params, "name": "Дверь"}
    if t == "loggia":
        params = {"width": _num(v.get("length") or v.get("width")), "height": _num(v.get("height")),
                  "sections": int(_num(v.get("sections")) or 3)}
        return {"kind": "loggia", "params": params, "name": "Раздвижное остекление"}
    if t == "vitrazh":
        params = {"width": _num(v.get("width")), "height": _num(v.get("height")),
                  "columns": int(_num(v.get("columns")) or 3), "rows": int(_num(v.get("rows")) or 1)}
        return {"kind": "vitrazh", "params": params, "name": "Витраж"}
    if t == "balcony":
        params = {"width": _num(v.get("blockWidth") or v.get("width")),
                  "height": _num(v.get("blockHeight") or v.get("height")),
                  "door_width": _num(v.get("doorWidth")), "door_side": str(v.get("doorOpening") or "Левое"),
                  "outward": _outward(v.get("direction"))}
        if str(v.get("view") or "") == "Снаружи":
            params["view"] = "outside"
        if _num(v.get("transomTop")) > 0:
            params["transom_top"] = _num(v.get("transomTop"))
        params["transom_type"] = str(v.get("transomType") or "Откидная внутрь")
        return {"kind": "balcony", "params": params, "name": "Балконный блок"}
    if t == "partition":
        name = "Перегородка" + (" с дверью" if v.get("withDoor") in (True, "true", "1", 1) else "")
        params = {"width": _num(v.get("width")), "height": _num(v.get("height")), "name": name, **_common(v)}
        return {"kind": "partition", "params": params, "name": name}
    name = str(v.get("name") or "Изделие")
    params = {"width": _num(v.get("width")), "height": _num(v.get("height")), "name": name, **_common(v)}
    return {"kind": "generic", "params": params, "name": name}


def _short_type(t: str) -> str:
    t = t.lower()
    # «поворотное» тоже содержит «поворотно», поэтому смотрим на оба корня
    if "поворот" in t and "откид" in t:
        return "П/О"
    if "поворот" in t:
        return "пов."
    if "откид" in t:
        return "отк."
    return "глух."


def describe(kind: str, p: dict[str, Any]) -> str:
    """Краткое описание створок/открывания для спецификации."""
    base = _describe(kind, p)
    extra = []
    if p.get("transom_top"):
        extra.append(f"фрамуга {_num(p['transom_top']):.0f}, {str(p.get('transom_type') or 'Глухая').lower()}")
    if p.get("bottom_part"):
        extra.append(f"низ {_num(p['bottom_part']):.0f}, глух.")
    if C.is_outside_view(p):
        extra.append("вид снаружи")
    return "; ".join([base, *extra]) if base else "; ".join(extra)


def _describe(kind: str, p: dict[str, Any]) -> str:
    out = " наружу" if p.get("outward") else ""
    if kind == "window":
        sashes = p.get("sashes") or [{"type": "Поворотно-откидное", "handle": "Правая"}]
        parts = []
        for s in sashes:
            st = _short_type(str(s.get("type") or ""))
            hinge = str(s.get("hinge") or "").lower()
            h = str(s.get("handle") or "").lower()
            if st != "глух." and hinge:
                st += " петли прав." if "прав" in hinge else " петли лев."
            elif st != "глух." and h:
                st += " прав." if "прав" in h else " лев."
            parts.append(st)
        return f"{len(sashes)} ств.: " + ", ".join(parts) + out
    if kind == "door":
        side = "прав." if "прав" in str(p.get("side") or "").lower() else "лев."
        return f"{p.get('door_type') or 'Распашная'}, петли {side}" + (out or " внутрь")
    if kind == "balcony":
        side = "прав." if "прав" in str(p.get("door_side") or "").lower() else "лев."
        dw = _num(p.get("door_width"))
        return (f"дверь П/О {dw:.0f} мм" if dw else "дверь П/О") + f", петли {side}" + out
    if kind in ("loggia", "sliding"):
        return f"{p.get('sections') or 3} секц., раздвижное"
    if kind == "vitrazh":
        return f"{p.get('columns') or 3}×{p.get('rows') or 1} полей"
    return str(p.get("name") or "")


def parse_orders(data: Any) -> list[dict[str, Any]]:
    """JSON-строка, путь к файлу или объект -> список заказов {customer, items:[{kind,params,name,qty}]}."""
    if isinstance(data, str):
        s = data.strip()
        if s.startswith("{") or s.startswith("["):
            data = json.loads(s)
        elif os.path.isfile(s):
            with open(s, encoding="utf-8-sig") as f:
                data = json.load(f)
        else:
            raise ValueError("Ожидается JSON заказа «К-Замер» или путь к .json файлу")
    if isinstance(data, list):
        raw_orders = data
    elif isinstance(data, dict) and isinstance(data.get("orders"), list):
        raw_orders = data["orders"]
    elif isinstance(data, dict) and isinstance(data.get("items"), list):
        raw_orders = [data]
    else:
        raise ValueError("Не похоже на заказ «К-Замер»: нужен объект с items или бэкап с orders")
    orders = []
    for o in raw_orders:
        items = []
        for it in o.get("items") or []:
            if "kind" in it:  # уже в нашем формате
                items.append({"kind": it["kind"], "params": it.get("params") or {},
                              "name": it.get("name") or KIND_NAMES.get(it["kind"], "Изделие"),
                              "qty": int(_num(it.get("qty")) or 1)})
                continue
            m = kzamer_item(str(it.get("templateId") or ""), it.get("values") or {})
            m["qty"] = int(_num((it.get("values") or {}).get("qty")) or 1)
            items.append(m)
        orders.append({"customer": o.get("customer") or {}, "items": items, "id": o.get("id")})
    return orders


def normalize_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for it in items:
        kind = str(it.get("kind") or "generic")
        if kind not in C.BUILDERS:
            raise ValueError(f"Неизвестный тип конструкции: {kind}. Доступно: {', '.join(C.BUILDERS)}")
        out.append({"kind": kind, "params": dict(it.get("params") or {}),
                    "name": it.get("name") or KIND_NAMES.get(kind, "Изделие"),
                    "qty": int(_num(it.get("qty")) or 1)})
    return out


def order_title(customer: dict[str, Any] | None) -> str:
    c = customer or {}
    who = ", ".join(str(x) for x in (c.get("client"), c.get("address")) if x)
    return f"Спецификация: {who}" if who else "Спецификация"


def spec_row(pos: int, it: dict[str, Any]) -> list[str]:
    p = it["params"]
    w, h = _num(p.get("width") or p.get("length")), _num(p.get("height"))
    area = w * h / 1e6
    return [str(pos), str(it["name"]), f"{w:.0f}×{h:.0f}", f"{area:.2f}", describe(it["kind"], p), str(it["qty"])]


def plan_order(items: list[dict[str, Any]], x0: float, y0: float, h: float,
               customer: dict[str, Any] | None = None) -> dict[str, Any]:
    """Раскладка заказа: изделия в ряд по низу y0, над каждым «Поз. N», под рядом спецификация.

    Возвращает {"items": [ops, ...], "labels": ops, "table": {...}, "bottom": y, "right": x}.
    """
    built = [C.build(it["kind"], it["params"]) for it in items]
    exts = [C.ops_extents(ops) for ops in built]
    max_h = max(e[3] - e[1] for e in exts)
    gap = 600 + 5 * h  # место под вертикальный размер следующего изделия
    x = x0
    placed, labels = [], []
    for i, (ops, e) in enumerate(zip(built, exts)):
        dx, dy = x - e[0], y0 - e[1]
        placed.append(C.shift_ops(ops, dx, dy))
        w = e[2] - e[0]
        labels.append({"op": "text", "pos": (x + w / 2, y0 + max_h + 180 + 5 * h), "text": f"Поз. {i + 1}",
                       "height": 1.5 * h, "layer": C.L_TEXT, "align": "center"})
        x += w + gap
    rows = [spec_row(i + 1, it) for i, it in enumerate(items)]
    if len(items) > 1:
        rows.append(spec_total(items))
    table = {"x": x0, "y": y0 - 350 - 7 * h, "header": SPEC_HEADER, "rows": rows,
             "title": order_title(customer)}
    return {"items": placed, "labels": labels, "table": table, "right": x - gap, "top": y0 + max_h + 180 + 6 * h}


def spec_total(items: list[dict[str, Any]]) -> list[str]:
    qty = sum(it["qty"] for it in items)
    area = sum(_num(it["params"].get("width") or it["params"].get("length")) * _num(it["params"].get("height"))
               * it["qty"] for it in items) / 1e6
    return ["", "Итого", "", f"{area:.2f}", "", str(qty)]
