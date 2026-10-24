"""Рендер DXF-документа в PNG/PDF через ezdxf.addons.drawing + matplotlib.

Фон всегда светлый: цвет 7 (белый/чёрный) рисуется чёрным, иначе белый текст пропал бы на белом.
"""

from __future__ import annotations

import os
import threading
from typing import Any

import matplotlib

matplotlib.use("Agg")
from matplotlib.figure import Figure  # noqa: E402

from ezdxf import bbox as ezbbox  # noqa: E402
from ezdxf.addons.drawing import Frontend, RenderContext  # noqa: E402
from ezdxf.addons.drawing.config import BackgroundPolicy, ColorPolicy, Configuration  # noqa: E402
from ezdxf.addons.drawing.matplotlib import MatplotlibBackend  # noqa: E402

_LOCK = threading.Lock()  # matplotlib не любит параллельный рендер


def _area(msp, area: list[float] | None) -> tuple[float, float, float, float]:
    if area:
        x1, y1, x2, y2 = area
        return min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)
    ext = ezbbox.extents(msp, fast=True)
    if not ext.has_data:
        return 0.0, 0.0, 1000.0, 1000.0
    return ext.extmin.x, ext.extmin.y, ext.extmax.x, ext.extmax.y


def render(doc, path: str, area: list[float] | None = None, width_px: int = 1600,
           paper_mm: tuple[float, float] | None = None, scale: float | None = None,
           monochrome: bool = False) -> dict[str, Any]:
    """Отрисовать пространство модели в файл (формат по расширению: .png или .pdf).

    paper_mm: размер листа (для PDF). scale: 1:N (иначе вписать область в лист).
    """
    msp = doc.modelspace()
    x1, y1, x2, y2 = _area(msp, area)
    w, h = max(x2 - x1, 1e-6), max(y2 - y1, 1e-6)
    cfg = Configuration(
        background_policy=BackgroundPolicy.WHITE,
        color_policy=ColorPolicy.BLACK if monochrome else ColorPolicy.COLOR,
        lineweight_scaling=0.6,
    )
    pdf = path.lower().endswith(".pdf")
    if paper_mm:
        fw, fh = paper_mm[0] / 25.4, paper_mm[1] / 25.4
    else:
        margin = 0.03
        w, h = w * (1 + 2 * margin), h * (1 + 2 * margin)
        x1, x2 = x1 - (x2 - x1) * margin, x2 + (x2 - x1) * margin
        y1, y2 = y1 - (y2 - y1) * margin, y2 + (y2 - y1) * margin
        fw = 16.0
        fh = max(2.0, min(fw * h / w, 40.0))
    dpi = 72 if pdf else max(50, int(width_px / fw))
    with _LOCK:
        fig = Figure(figsize=(fw, fh), facecolor="white")
        ax = fig.add_axes((0, 0, 1, 1))
        ax.set_facecolor("white")
        Frontend(RenderContext(doc), MatplotlibBackend(ax, adjust_figure=False), config=cfg).draw_layout(msp, finalize=True)
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        if paper_mm:
            if scale:
                hw, hh = paper_mm[0] * scale / 2, paper_mm[1] * scale / 2
            else:  # вписать область в лист с сохранением пропорций
                k = max(w / paper_mm[0], h / paper_mm[1]) * 1.02
                hw, hh = paper_mm[0] * k / 2, paper_mm[1] * k / 2
        else:
            hw, hh = w / 2, h / 2
            if fh * w / fw < h:  # высоту ограничили, расширяем по X
                hw = h * fw / fh / 2
            else:
                hh = w * fh / fw / 2
        ax.set_xlim(cx - hw, cx + hw)
        ax.set_ylim(cy - hh, cy + hh)
        ax.set_aspect("auto")
        ax.axis("off")
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        fig.savefig(path, dpi=dpi, facecolor="white")
    return {"path": os.path.abspath(path), "bytes": os.path.getsize(path), "area": [x1, y1, x2, y2]}
