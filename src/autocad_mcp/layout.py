"""Оформление размеров: высота текста по масштабу чертежа и разнос пересекающихся подписей."""

from __future__ import annotations

from typing import Iterable

Point = tuple[float, float]


def auto_text_height(extent: float) -> float:
    """Высота текста по габариту чертежа: ~1/60 от большей стороны, не меньше 80 мм.

    Окно 1800 мм → 80 мм; склад 60 000 мм → 1000 мм (10 мм на листе в 1:100).
    """
    if extent <= 0:
        return 100.0
    return round(min(max(extent / 60.0, 80.0), 5000.0))


def dim_sizes(h: float) -> dict[str, float]:
    """Пропорциональные размеры элементов размера от высоты текста."""
    return {"text": h, "arrow": h * 0.9, "ext_extend": h * 0.6, "ext_offset": h * 0.6, "gap": h * 0.35}


class BBox:
    """Габариты того, что нарисовано (для автоматической высоты текста)."""

    def __init__(self) -> None:
        self.box: list[float] | None = None

    def reset(self, box: list[float] | None = None) -> None:
        self.box = box

    def grow(self, points: Iterable[Point]) -> None:
        for x, y in points:
            if self.box is None:
                self.box = [x, y, x, y]
            else:
                b = self.box
                b[0], b[1], b[2], b[3] = min(b[0], x), min(b[1], y), max(b[2], x), max(b[3], y)

    def extent(self) -> float:
        if self.box is None:
            return 0.0
        return max(self.box[2] - self.box[0], self.box[3] - self.box[1])


class DimStacker:
    """Не даёт подписям соседних размеров наезжать друг на друга.

    Если подпись нового размера пересекается с уже поставленной на той же линии,
    размерная линия сдвигается на следующий «этаж» (дальше от объекта).
    """

    def __init__(self) -> None:
        self.boxes: list[tuple[int, float, float, float]] = []  # (угол, линия, от, до)

    def reset(self) -> None:
        self.boxes.clear()

    def place(self, angle: int, line: float, center: float, label: str, h: float, offset: float) -> float:
        half = (len(label) * 0.7 * h + h) / 2
        step = h * 2.2
        direction = 1.0 if offset > 0 else -1.0
        for _ in range(12):
            clash = any(
                a == angle and abs(c - line) < step * 0.9 and lo < center + half and hi > center - half
                for a, c, lo, hi in self.boxes
            )
            if not clash:
                break
            line += step * direction
        self.boxes.append((angle, line, center - half, center + half))
        return line


def aligned_place(stacker: DimStacker, p1: Point, p2: Point, offset: float, label: str,
                  h: float) -> tuple[float, Point]:
    """Параллельный размер через DimStacker.

    offset > 0: размерная линия слева от направления p1->p2. Возвращает итоговое смещение
    (с учётом разноса подписей) и точку на размерной линии. Горизонтальные и вертикальные
    размеры делят «этажи» с линейными размерами.
    """
    import math

    dx, dy = p2[0] - p1[0], p2[1] - p1[1]
    L = math.hypot(dx, dy)
    if L < 1e-9:
        raise ValueError("Точки размера совпадают")
    ux, uy = dx / L, dy / L
    nx, ny = -uy, ux  # левая нормаль
    deg = round(math.degrees(math.atan2(uy, ux))) % 180
    if deg == 90:
        key, n0, u0 = 90, (1.0, 0.0), (0.0, 1.0)  # как у вертикальных линейных: «линия» = x
    else:
        a = math.radians(deg)
        key = 0 if deg == 0 else ("al", deg)
        u0, n0 = (math.cos(a), math.sin(a)), (-math.sin(a), math.cos(a))
    sgn = nx * n0[0] + ny * n0[1]  # +1 или -1
    base = p1[0] * n0[0] + p1[1] * n0[1]
    mid = ((p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2)
    center = mid[0] * u0[0] + mid[1] * u0[1]
    want = offset * sgn
    line = stacker.place(key, base + want, center, label, h, want)
    dist = (line - base) * sgn
    loc = (mid[0] + nx * dist, mid[1] + ny * dist)
    return dist, loc
