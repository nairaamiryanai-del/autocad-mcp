"""Статический расчёт стойки или стропила на прогиб (условие жёсткости), как в примере проекта, лист 3.

Нагрузки по СНиП 2.01.07-85* «Нагрузки и воздействия», допустимый прогиб по табл. 42 СНиП 2.03.06-85.
Единицы как в примере: кгс, см. Стойка рассматривается как однопролётная балка со свободными опорами:
    f = 5/384 * q*B*H^4 / (E*Jx)   ->   Jx треб = 5/384 * q*B*H^4 / (E*f доп)
    q = (Wm + S*cos²a)*0,9 + g*cos a
    g  = b*γ (вес остекления), S = Sg*μ (снег), Wm = W0*k*c (ветер, средняя составляющая)
Для вертикальной стойки (a = 90°) снег и вес стекла не дают составляющей по нормали: остаётся ветер.
"""

from __future__ import annotations

import math
from typing import Any

# снеговые районы: W0 (Sg), кгс/м² (карта 1)
SNOW = {"I": 80, "II": 120, "III": 180, "IV": 240, "V": 320, "VI": 400, "VII": 480, "VIII": 560}
# ветровые районы: W0, кгс/м² (карта 3)
WIND = {"Iа": 17, "I": 23, "II": 30, "III": 38, "IV": 48, "V": 60, "VI": 73, "VII": 85}
# коэффициент k изменения ветрового давления по высоте (табл. 6): высота м -> (A, B, C)
K_TABLE = [(5, 0.75, 0.5, 0.4), (10, 1.0, 0.65, 0.4), (20, 1.25, 0.85, 0.55), (40, 1.5, 1.1, 0.8),
           (60, 1.7, 1.3, 1.0), (80, 1.85, 1.45, 1.15), (100, 2.0, 1.6, 1.25), (150, 2.25, 1.9, 1.55),
           (200, 2.45, 2.1, 1.8), (250, 2.65, 2.3, 2.0), (300, 2.75, 2.5, 2.2), (350, 2.75, 2.75, 2.35)]
TERRAIN = {"A": "открытые побережья морей, озёр и водохранилищ, степи",
           "B": "городские территории, лесные массивы с препятствиями выше 10 м",
           "C": "городские районы с застройкой зданиями выше 25 м"}

E_ALU = 710000.0    # кгс/см², модуль Юнга алюминия
E_STEEL = 2100000.0
GLASS_GAMMA = 0.0025  # кгс/см³, удельный вес стекла
C_AERO = 0.8          # аэродинамический коэффициент
PHI2 = 0.9            # коэффициент сочетаний


_ARAB = {"1": "I", "2": "II", "3": "III", "4": "IV", "5": "V", "6": "VI", "7": "VII", "8": "VIII", "1A": "Iа", "1А": "Iа"}


def _norm_region(r: str, table: dict[str, int], what: str) -> str:
    """'II', '2', 'Ia' или 'Iа' (кириллицей) -> ключ таблицы."""
    s = str(r).strip().upper()
    s = _ARAB.get(s, s)
    if s in ("IA", "IА"):  # латинская или кириллическая «а»
        s = "Iа"
    if s not in table:
        raise ValueError(f"{what} район: один из {', '.join(table)}")
    return s


def k_height(height_m: float, terrain: str) -> float:
    col = {"A": 1, "B": 2, "C": 3}.get(str(terrain).upper().replace("А", "A").replace("В", "B").replace("С", "C"))
    if not col:
        raise ValueError("Тип местности: A, B или C")
    for row in K_TABLE:
        if height_m <= row[0]:
            return row[col]
    return K_TABLE[-1][col]


def mu_snow(angle: float) -> float:
    """Коэффициент перехода от веса снега на земле к нагрузке на покрытие."""
    if angle <= 25:
        return 1.0
    if angle >= 60:
        return 0.0
    return (60 - angle) / 35


def rafter_check(*, length_cm: float, angle_deg: float, step_cm: float, glass_mm: float = 12,
                 snow_region: str = "II", wind_region: str = "V", terrain: str = "B", height_m: float = 5,
                 igu: bool = True, steel: bool = False, profiles: list[dict[str, Any]] | None = None,
                 article: str | None = None) -> dict[str, Any]:
    """Требуемый момент инерции и подбор профиля. glass_mm: общая толщина стекла в стеклопакете (b)."""
    H = float(length_cm)
    a = float(angle_deg)
    B = float(step_cm)
    if H <= 0 or B <= 0 or not 0 <= a <= 90:
        raise ValueError("Длина и шаг > 0, угол от 0 до 90°")
    sr = _norm_region(snow_region, SNOW, "Снеговой")
    wr = _norm_region(wind_region, WIND, "Ветровой")
    E = E_STEEL if steel else E_ALU
    cos_a = math.cos(math.radians(a))
    b = glass_mm / 10.0                       # см
    g = b * GLASS_GAMMA                       # кгс/см²
    Sg = SNOW[sr]                             # кгс/м²
    mu = mu_snow(a)
    S = Sg * mu / 10000.0                     # кгс/см²
    W0 = WIND[wr]
    k = k_height(height_m, terrain)
    Wm = W0 * k * C_AERO / 10000.0            # кгс/см²
    q = (Wm + S * cos_a ** 2) * PHI2 + g * cos_a
    f_lim_div = 300 if igu else 200
    f_dop = H / f_lim_div
    part1 = 5 / 384
    part2 = q * B * H ** 4
    part3 = E * f_dop
    jx = part1 * part2 / part3

    cands = [p for p in (profiles or []) if p.get("ix_cm4") and p.get("role") in ("стойка", "стропило", "импост", "ригель")]
    if article:
        chosen = next((p for p in profiles or [] if p["article"] == article), None)
        if not chosen:
            raise ValueError(f"Профиля {article} нет в каталоге")
    else:
        ok = sorted((p for p in cands if p["ix_cm4"] >= jx), key=lambda p: p["ix_cm4"])
        chosen = ok[0] if ok else None
    passed = bool(chosen and chosen.get("ix_cm4") and chosen["ix_cm4"] >= jx)
    r = lambda x, n=4: round(x, n)  # noqa: E731
    return {
        "inputs": {"E_kgf_cm2": E, "H_cm": H, "angle_deg": a, "cos_a": r(cos_a), "cos2_a": r(cos_a ** 2), "B_cm": B,
                   "f_dop": f"H/{f_lim_div}", "f_dop_cm": r(f_dop), "glass_b_cm": b, "gamma": GLASS_GAMMA,
                   "snow_region": sr, "Sg_kgf_m2": Sg, "wind_region": wr, "W0_kgf_m2": W0,
                   "terrain": terrain.upper(), "height_m": height_m, "k": k, "c": C_AERO, "phi2": PHI2},
        "loads": {"g": r(g, 5), "mu": r(mu, 2), "S": r(S, 5), "Wm": r(Wm, 5), "q": r(q, 5)},
        "jx_parts": {"I": r(part1, 5), "II": round(part2, 2), "III": round(part3, 1)},
        "jx_required_cm4": round(jx, 2),
        "profile": chosen,
        "passed": passed,
        "verdict": (f"Jx треб = {jx:.2f} см4 < {chosen['ix_cm4']} см4 ({chosen['article']}): удовлетворяет условию прогиба"
                    if passed else
                    (f"Jx треб = {jx:.2f} см4: профиль {chosen['article']} с Ix = {chosen.get('ix_cm4')} см4 НЕ проходит"
                     if chosen else f"Jx треб = {jx:.2f} см4: в каталоге нет профиля с таким моментом инерции")),
    }
