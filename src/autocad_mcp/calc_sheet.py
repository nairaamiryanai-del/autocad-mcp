"""Лист статического расчёта (А4, книжный) из результата calc.rafter_check, по образцу листа 3 примера проекта.

Строится как список ops в мм бумаги со сдвигом (x0, y0) в модели (масштаб 1:1), рамка с основной надписью ГОСТ.
"""

from __future__ import annotations

from typing import Any

from . import calc as K
from . import sheets as S

H = 2.2      # высота основного текста, мм
ROW = 4.0    # шаг строк


def _fmt(v: Any) -> str:
    if isinstance(v, float):
        s = f"{v:.5f}".rstrip("0").rstrip(".") if abs(v) < 1 else f"{v:,.2f}".replace(",", " ").rstrip("0").rstrip(".")
        return s.replace(".", ",")
    return str(v)


def calc_sheet_ops(res: dict[str, Any], x0: float, y0: float, obj: str, fields: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    ops: list[dict[str, Any]] = []
    y = 282.0  # верх поля чертежа А4

    def text(x: float, s: str, h: float = H, align: str = "left") -> None:
        ops.append(S._txt(x0 + x, y0 + y, s, h, align=align))

    def nl(k: float = 1.0) -> None:
        nonlocal y
        y -= ROW * k

    def table(header: list[str], rows: list[list[Any]], widths: list[float] | None = None) -> None:
        nonlocal y
        t_ops, box = S.table_ops(x0 + 30, y0 + y, header, rows, 1.8, widths)
        ops.extend(t_ops)
        y = box[1] - y0 - 2 - H  # следующая строка текста ниже таблицы на свою высоту

    inp, ld, jp = res["inputs"], res["loads"], res["jx_parts"]
    vertical = inp["angle_deg"] >= 89.9
    text(112.5, "СТАТИЧЕСКИЕ РАСЧЁТЫ", 3.5, "center"); nl(1.6)
    text(30, "Расчёт вертикальной стойки (импоста) по условию жёсткости" if vertical
         else "Расчёт наклонной стойки (стропила) по условию жёсткости", 2.8); nl()
    text(30, f"для объекта: {obj}" if obj else "для объекта"); nl(1.4)

    text(30, "Снеговые нагрузки (карта 1 СНиП 2.01.07-85* «Нагрузки и воздействия»)"); nl(0.6)
    table(["Снеговой район", *K.SNOW], [["W0, кгс/м²", *K.SNOW.values()]], [24, *[15.5] * 8])
    text(30, f"Принят район {inp['snow_region']}: Sg = {inp['Sg_kgf_m2']} кгс/м²"); nl(1.3)

    text(30, "Ветровые нагрузки (карта 3 СНиП 2.01.07-85*)"); nl(0.6)
    table(["Ветровой район", *K.WIND], [["W0, кгс/м²", *K.WIND.values()]], [24, *[15.5] * 8])
    text(30, f"Принят район {inp['wind_region']}: W0 = {inp['W0_kgf_m2']} кгс/м²"); nl(1.3)

    text(30, "Коэффициент k изменения ветрового давления по высоте (табл. 6 СНиП 2.01.07-85*)"); nl(0.6)
    table(["Высота, м", "A", "B", "C"], [[f"до {r[0]}" if i == 0 else r[0], *[_fmt(v) for v in r[1:]]] for i, r in enumerate(K.K_TABLE)],
          [24, 20, 20, 20])
    text(30, f"Местность {inp['terrain']} ({K.TERRAIN[inp['terrain']]}), высота {inp['height_m']} м: k = {_fmt(inp['k'])}"); nl(1.5)

    text(30, "Условие: f факт < f доп; f доп = L/200 (одинарное стекло), L/300 (стеклопакет), табл. 42 СНиП 2.03.06-85"); nl()
    text(30, "f = 5/384·(q·B·H⁴)/(E·Jx)  ->  Jx треб = 5/384·(q·B·H⁴)/(E·f доп)"); nl(1.4)

    rows = [
        ("E", f"{_fmt(inp['E_kgf_cm2'])} кгс/см²", "модуль Юнга (алюминий 710000, сталь 2100000)"),
        ("H", f"{_fmt(inp['H_cm'])} см", "длина стойки"),
        ("a", f"{_fmt(inp['angle_deg'])} град", "угол наклона к горизонтали"),
        ("cos a / cos² a", f"{_fmt(inp['cos_a'])} / {_fmt(inp['cos2_a'])}", ""),
        ("B", f"{_fmt(inp['B_cm'])} см", "шаг стоек"),
        ("f доп", f"{inp['f_dop']} = {_fmt(inp['f_dop_cm'])} см", "допустимый прогиб"),
        ("g = b·γ", f"{_fmt(ld['g'])} кгс/см²", f"вес остекления: b = {_fmt(inp['glass_b_cm'])} см, γ = {inp['gamma']} кгс/см³"),
        ("μ", _fmt(ld["mu"]), "1 при a<25°, 0 при a>60°, иначе (60−a)/35"),
        ("S = Sg·μ", f"{_fmt(ld['S'])} кгс/см²", "снеговая нагрузка"),
        ("Wm = W0·k·c", f"{_fmt(ld['Wm'])} кгс/см²", f"ветер, средняя составляющая, c = {inp['c']}"),
        ("q = (Wm + S·cos² a)·0,9 + g·cos a", f"{_fmt(ld['q'])} кгс/см²", "расчётная нагрузка"),
        ("Jx: I = 5/384", _fmt(jp["I"]), ""),
        ("Jx: II = q·B·H⁴", _fmt(jp["II"]), ""),
        ("Jx: III = E·f доп", _fmt(jp["III"]), ""),
    ]
    for a, b, c in rows:
        text(30, a); text(80, b); text(118, c, 1.8); nl()
    nl(0.5)
    text(30, f"Jx треб = {_fmt(res['jx_required_cm4'])} см⁴", 3.0); nl(1.5)
    p = res.get("profile")
    if p:
        text(30, f"Принят профиль {p['article']} ({p.get('name', '')}), Ix = {p.get('ix_cm4')} см⁴", 2.5); nl()
    text(30, ("Удовлетворяет условию прогиба" if res["passed"] else "НЕ удовлетворяет условию прогиба: нужен профиль мощнее или меньший шаг"), 2.8)

    content_bottom = y
    sheet, info = S.sheet_ops("A4", False, 1, [x0 + 30, y0 + 70, x0 + 195, y0 + 282],
                              {**fields, "title": fields.get("title") or "Статический расчёт"})
    info["overflow"] = content_bottom < 72
    return ops + sheet, info
