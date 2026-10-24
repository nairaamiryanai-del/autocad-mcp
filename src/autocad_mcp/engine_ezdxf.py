"""Безголовый движок на ezdxf: создаёт и правит DXF без запущенного AutoCAD."""

from __future__ import annotations

import math
import os
from typing import Any

import ezdxf
from ezdxf import bbox as ezbbox
from ezdxf import path as ezpath
from ezdxf.enums import MTextEntityAlignment, TextEntityAlignment
from ezdxf.math import Matrix44, Vec2, offset_vertices_2d

from . import constructions as C
from . import preview
from .common import (DXF_VERSIONS, angular_location as _angular_location, bbox_in_area, lineweight_code,
                     linetype_aliases, match_names, polyline_length_area, rnd, version_key)
from .layout import BBox, DimStacker, aligned_place, auto_text_height, dim_sizes

# слой -> цвет AutoCAD (ACI)
LAYER_COLORS = {
    C.L_FRAME: 7,     # чёрный/белый
    C.L_MULLION: 8,   # тёмно-серый
    C.L_SASH: 8,
    C.L_OPEN: 5,      # синий
    C.L_HANDLE: 1,    # красный
    C.L_DIM: 3,       # зелёный
    C.L_TEXT: 7,
}

FONT = "arial.ttf"  # TrueType с кириллицей (txt.shx кириллицу не показывает)
ONLY_COM = "доступно только в режиме AutoCAD (com)"


class EzdxfEngine:
    name = "ezdxf"

    def __init__(self, out_dir: str):
        self.out_dir = out_dir
        self.doc = None
        self.msp = None
        self.path: str | None = None
        self.dim_text: float | None = None  # None = авто по габаритам
        self.stacker = DimStacker()
        self.bbox = BBox()
        self.last_sheet: dict[str, Any] | None = None
        self.new_drawing()

    # --- служебное ---
    def status(self) -> dict[str, Any]:
        return {
            "engine": self.name,
            "autocad_connected": False,
            "current_file": self.path,
            "entities": self._count(),
            "text_font": self.doc.styles.get("Standard").dxf.font,
            "annotation_text_height": self.dim_text or f"авто ({self._text_h(None):.0f})",
        }

    def _count(self) -> int:
        return len(self.msp) if self.msp is not None else 0

    def _setup(self) -> None:
        # Arial для Standard: в txt.shx нет кириллицы
        std = self.doc.styles.get("Standard")
        std.dxf.font = FONT
        self.stacker.reset()
        self.last_sheet = None

    def new_drawing(self, template: str | None = None) -> str:
        self.doc = ezdxf.new("R2018", setup=True)
        self.msp = self.doc.modelspace()
        self.path = None
        self._setup()
        self.bbox.reset()
        return "Создан новый чертёж (R2018)"

    def open_drawing(self, path: str) -> str:
        self.doc = ezdxf.readfile(path)
        self.msp = self.doc.modelspace()
        self.path = path
        self._setup()
        ext = ezbbox.extents(self.msp, fast=True)
        self.bbox.reset([ext.extmin.x, ext.extmin.y, ext.extmax.x, ext.extmax.y] if ext.has_data else None)
        return f"Открыт {path}: {self._count()} объектов"

    def save_drawing(self, path: str | None = None, version: str | None = None) -> str:
        if path is None:
            path = self.path or os.path.join(self.out_dir, "drawing.dxf")
        if not os.path.splitext(path)[1]:
            path += ".dxf"
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        if version:
            # формат DXF старой версии (ezdxf понижает версию начиная с R2000)
            self.doc.dxfversion = DXF_VERSIONS[version_key(version)]
        self.doc.saveas(path)
        self.path = path
        return path

    def export_dxf(self, path: str) -> str:
        if not path.lower().endswith(".dxf"):
            path += ".dxf"
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        old = self.doc.filename
        self.doc.saveas(path)
        self.doc.filename = old  # копия: текущий файл не меняется
        return os.path.abspath(path)

    # --- слои и типы линий ---
    def ensure_layer(self, name: str, color: int | None = None, force: bool = False) -> str:
        """Создать слой, если его нет. Цвет меняется только у нового слоя или при force=True."""
        if name not in self.doc.layers:
            self.doc.layers.add(name, color=int(color if color is not None else LAYER_COLORS.get(name, 7)))
        elif force and color is not None:
            self.doc.layers.get(name).color = int(color)
        return name

    def list_layers(self) -> list[dict[str, Any]]:
        cur = self.doc.header.get("$CLAYER", "0")
        return [{"name": l.dxf.name, "color": l.color, "on": l.is_on(), "frozen": l.is_frozen(),
                 "locked": l.is_locked(), "linetype": l.dxf.linetype, "current": l.dxf.name == cur}
                for l in self.doc.layers]

    def ensure_linetype(self, name: str) -> str:
        if name.upper() in ("BYLAYER", "BYBLOCK", "CONTINUOUS"):
            return name
        names = linetype_aliases(name)
        for n in names:
            if n in self.doc.linetypes:
                return n
        from ezdxf.tools.standards import linetypes
        for lt in linetypes():
            if lt[0].upper() in (n.upper() for n in names):
                self.doc.linetypes.add(lt[0], pattern=lt[2], description=lt[1])
                return lt[0]
        avail = ", ".join(l.dxf.name for l in self.doc.linetypes)
        raise ValueError(f"Тип линии {name} не найден. Есть: {avail}")

    def set_layer(self, name: str, on: bool | None = None, frozen: bool | None = None,
                  locked: bool | None = None, color: int | None = None, linetype: str | None = None,
                  lineweight: float | None = None, current: bool = False) -> dict[str, Any]:
        self.ensure_layer(name, color)
        lay = self.doc.layers.get(name)
        cur = self.doc.header.get("$CLAYER", "0")
        if frozen and (current or cur == name):
            raise ValueError("Текущий слой нельзя заморозить")
        if on is not None:
            lay.on() if on else lay.off()
        if frozen is not None:
            lay.freeze() if frozen else lay.thaw()
        if locked is not None:
            lay.lock() if locked else lay.unlock()
        if color is not None:
            lay.color = int(color)
        if linetype:
            lay.dxf.linetype = self.ensure_linetype(linetype)
        if lineweight is not None:
            lay.dxf.lineweight = lineweight_code(lineweight)
        if current:
            self.doc.header["$CLAYER"] = name
        return next(l for l in self.list_layers() if l["name"] == name)

    def _layer_used(self, name: str) -> int:
        n = 0
        for layout in [self.msp, *self.doc.layouts_and_blocks()]:
            n += sum(1 for e in layout if e.dxf.get("layer", "0").upper() == name.upper())
        return n

    def delete_layer(self, name: str) -> str:
        if name not in self.doc.layers:
            raise ValueError(f"Слоя «{name}» нет")
        if name in ("0", "Defpoints") or self.doc.header.get("$CLAYER", "0") == name:
            raise ValueError(f"Слой «{name}» удалить нельзя (служебный или текущий)")
        used = self._layer_used(name)
        if used:
            raise ValueError(f"Слой «{name}» не пуст: {used} объектов. Сначала удалите или перенесите их")
        self.doc.layers.remove(name)
        return f"Слой «{name}» удалён"

    # --- оформление ---
    def _text_h(self, explicit: float | None) -> float:
        if explicit:
            return float(explicit)
        if self.dim_text:
            return float(self.dim_text)
        return auto_text_height(self.bbox.extent())

    def set_annotation_scale(self, text_height: float | None) -> str:
        self.dim_text = float(text_height) if text_height else None
        return f"Высота текста размеров и подписей: {self.dim_text:.0f} мм" if self.dim_text else "Высота текста: авто по габаритам"

    @staticmethod
    def _dim_override(h: float) -> dict[str, Any]:
        s = dim_sizes(h)
        return {"dimtxt": s["text"], "dimasz": s["arrow"], "dimexe": s["ext_extend"],
                "dimexo": s["ext_offset"], "dimgap": s["gap"], "dimdec": 0, "dimadec": 0, "dimtad": 1,
                "dimlfac": 1.0, "dimscale": 1.0, "dimtix": 1, "dimtxsty": "Standard"}

    # --- примитивы (возвращают handle) ---
    def _attrs(self, layer: str, dashed: bool = False, lineweight: float | None = None) -> dict[str, Any]:
        self.ensure_layer(layer)
        a: dict[str, Any] = {"layer": layer}
        if dashed:
            if "DASHED" not in self.doc.linetypes:
                self.doc.linetypes.add("DASHED", pattern=[0.6, 0.4, -0.2])
            a["linetype"] = "DASHED"
        if lineweight:
            a["lineweight"] = lineweight_code(lineweight)
        return a

    def add_line(self, p1, p2, layer="0", dashed=False, lineweight=None) -> str:
        e = self.msp.add_line(p1, p2, dxfattribs=self._attrs(layer, dashed, lineweight))
        self.bbox.grow([p1, p2])
        return e.dxf.handle

    def add_polyline(self, points, closed=True, layer="0", lineweight=None) -> str:
        e = self.msp.add_lwpolyline(points, close=closed, dxfattribs=self._attrs(layer, lineweight=lineweight))
        self.bbox.grow(points)
        return e.dxf.handle

    def add_circle(self, center, r, layer="0") -> str:
        e = self.msp.add_circle(center, r, dxfattribs=self._attrs(layer))
        self.bbox.grow([(center[0] - r, center[1] - r), (center[0] + r, center[1] + r)])
        return e.dxf.handle

    def add_arc(self, center, r, start_deg, end_deg, layer="0") -> str:
        e = self.msp.add_arc(center, r, start_deg, end_deg, dxfattribs=self._attrs(layer))
        self.bbox.grow([(center[0] - r, center[1] - r), (center[0] + r, center[1] + r)])
        return e.dxf.handle

    def add_ellipse(self, center, rx, ry, rotation_deg=0.0, layer="0") -> str:
        major, ratio, rot = (rx, ry / rx, rotation_deg) if rx >= ry else (ry, rx / ry, rotation_deg + 90)
        a = math.radians(rot)
        e = self.msp.add_ellipse(center, major_axis=(major * math.cos(a), major * math.sin(a), 0), ratio=ratio,
                                 dxfattribs=self._attrs(layer))
        m = max(rx, ry)
        self.bbox.grow([(center[0] - m, center[1] - m), (center[0] + m, center[1] + m)])
        return e.dxf.handle

    def add_point(self, pos, layer="0") -> str:
        e = self.msp.add_point(pos, dxfattribs=self._attrs(layer))
        self.bbox.grow([pos])
        return e.dxf.handle

    def add_spline(self, points, closed=False, layer="0") -> str:
        pts = [tuple(p) for p in points]
        if closed and pts[0] != pts[-1]:
            pts.append(pts[0])
        e = self.msp.add_spline(pts, dxfattribs=self._attrs(layer))
        self.bbox.grow(points)
        return e.dxf.handle

    def add_text(self, pos, text, height=None, layer=C.L_TEXT, align="center", rotation=0.0) -> str:
        h = self._text_h(height)
        t = self.msp.add_text(text, dxfattribs={"height": h, "style": "Standard", "rotation": rotation,
                                                **self._attrs(layer)})
        anchor = {"center": TextEntityAlignment.MIDDLE_CENTER,
                  "mleft": TextEntityAlignment.MIDDLE_LEFT}.get(align, TextEntityAlignment.LEFT)
        t.set_placement(pos, align=anchor)
        self.bbox.grow([pos])
        return t.dxf.handle

    def add_mtext(self, pos, text, height=None, width=0.0, layer=C.L_TEXT, align="left") -> str:
        h = self._text_h(height)
        attrs = {"char_height": h, "style": "Standard", **self._attrs(layer)}
        if width:
            attrs["width"] = float(width)
        m = self.msp.add_mtext(str(text).replace("\n", "\\P"), dxfattribs=attrs)
        att = MTextEntityAlignment.MIDDLE_CENTER if align == "center" else MTextEntityAlignment.TOP_LEFT
        m.set_location(pos, attachment_point=att)
        self.bbox.grow([pos])
        return m.dxf.handle

    def _boundary(self, handle: str):
        e = self._get(handle)
        t = e.dxftype()
        if t == "LWPOLYLINE":
            if not e.closed:
                raise ValueError("Полилиния контура должна быть замкнутой")
            return ("poly", list(e.get_points("xyb")))
        if t == "CIRCLE":
            return ("circle", (tuple(e.dxf.center)[:2], e.dxf.radius))
        if t == "ELLIPSE":
            return ("ellipse", e)
        raise ValueError(f"Контур штриховки: нужна замкнутая полилиния, окружность или эллипс (получено {t})")

    def add_hatch(self, points=None, handle=None, pattern="ANSI31", scale=1.0, angle=0.0,
                  color=None, layer="HATCH") -> str:
        attrs = self._attrs(layer)
        if color is not None:
            attrs["color"] = int(color)
        h = self.msp.add_hatch(dxfattribs=attrs)
        if handle:
            kind, data = self._boundary(handle)
            if kind == "poly":
                h.paths.add_polyline_path(data, is_closed=True)
            elif kind == "circle":
                ep = h.paths.add_edge_path()
                ep.add_arc(data[0], data[1], 0, 360)
            else:
                ep = h.paths.add_edge_path()
                el = data
                ep.add_ellipse(el.dxf.center, el.dxf.major_axis, el.dxf.ratio, 0, 360)
        else:
            h.paths.add_polyline_path([tuple(p[:2]) for p in points], is_closed=True)
            self.bbox.grow([tuple(p[:2]) for p in points])
        if pattern.upper() == "SOLID":
            h.set_solid_fill(color=int(color) if color is not None else 256)
        else:
            h.set_pattern_fill(pattern.upper(), scale=float(scale), angle=float(angle),
                               color=int(color) if color is not None else 256)
        return h.dxf.handle

    def add_linear_dim(self, p1, p2, offset=0.0, angle=0, layer=C.L_DIM, text=None, text_height=None) -> str:
        self.ensure_layer(layer)
        h = self._text_h(text_height)
        length = abs(p2[1] - p1[1]) if angle == 90 else abs(p2[0] - p1[0])
        label = str(text) if text is not None else f"{length:.0f}"
        if angle == 90:
            mid = (p1[1] + p2[1]) / 2
            base = (self.stacker.place(90, p1[0] + offset, mid, label, h, offset), mid)
        else:
            mid = (p1[0] + p2[0]) / 2
            base = (mid, self.stacker.place(0, p1[1] + offset, mid, label, h, offset))
        dim = self.msp.add_linear_dim(
            base=base, p1=p1, p2=p2, angle=angle,
            text=(str(text) if text is not None else "<>"),
            dimstyle="EZDXF", override=self._dim_override(h), dxfattribs={"layer": layer},
        )
        dim.render()
        self.bbox.grow([p1, p2, base])
        return dim.dimension.dxf.handle

    def add_aligned_dim(self, p1, p2, offset=300.0, layer=C.L_DIM, text=None, text_height=None) -> str:
        self.ensure_layer(layer)
        h = self._text_h(text_height)
        label = str(text) if text is not None else f"{math.dist(p1, p2):.0f}"
        dist, loc = aligned_place(self.stacker, p1, p2, offset, label, h)
        dim = self.msp.add_aligned_dim(p1=p1, p2=p2, distance=dist, text=(str(text) if text is not None else "<>"),
                                       dimstyle="EZDXF", override=self._dim_override(h), dxfattribs={"layer": layer})
        dim.render()
        self.bbox.grow([p1, p2, loc])
        return dim.dimension.dxf.handle

    def add_radial_dim(self, center, r, angle_deg=45.0, diameter=False, layer=C.L_DIM,
                       text=None, text_height=None) -> str:
        self.ensure_layer(layer)
        h = self._text_h(text_height)
        fn = self.msp.add_diameter_dim if diameter else self.msp.add_radius_dim
        dim = fn(center=center, radius=r, angle=angle_deg, text=(str(text) if text is not None else "<>"),
                 dimstyle="EZ_RADIUS", override=self._dim_override(h), dxfattribs={"layer": layer})
        dim.render()
        self.bbox.grow([(center[0] - r, center[1] - r), (center[0] + r, center[1] + r)])
        return dim.dimension.dxf.handle

    def add_angular_dim(self, vertex, p1, p2, radius=None, layer=C.L_DIM, text=None, text_height=None) -> str:
        self.ensure_layer(layer)
        h = self._text_h(text_height)
        loc = _angular_location(vertex, p1, p2, radius)
        dim = self.msp.add_angular_dim_3p(base=loc, center=vertex, p1=p1, p2=p2,
                                          text=(str(text) if text is not None else "<>"),
                                          dimstyle="EZ_CURVED", override=self._dim_override(h),
                                          dxfattribs={"layer": layer})
        dim.render()
        self.bbox.grow([vertex, p1, p2, loc])
        return dim.dimension.dxf.handle

    def add_leader(self, points, text, layer=C.L_DIM, text_height=None) -> str:
        from ezdxf.render import mleader
        self.ensure_layer(layer)
        h = self._text_h(text_height)
        pts = [Vec2(p[:2]) for p in points]
        tip, ins = pts[0], pts[-1]
        right = ins.x >= tip.x
        ml = self.msp.add_multileader_mtext("Standard", dxfattribs={"layer": layer})
        ml.set_content(str(text), char_height=h, style="Standard",
                       alignment=mleader.TextAlignment.left if right else mleader.TextAlignment.right)
        ml.set_arrow_properties(size=h * 0.9)
        ml.set_connection_properties(landing_gap=h * 0.3, dogleg_length=h)
        side = mleader.ConnectionSide.left if right else mleader.ConnectionSide.right
        ml.add_leader_line(side, list(reversed(pts[:-1])) if len(pts) > 2 else [tip])
        ent = ml.build(insert=ins)
        self.bbox.grow([tuple(p) for p in pts])
        return ent.dxf.handle if ent is not None else ml.multileader.dxf.handle

    # --- таблица ---
    def add_table(self, x, y, header, rows, title=None, col_widths=None, text_height=None, layer="TABLE") -> dict[str, Any]:
        from .sheets import table_ops
        h = self._text_h(text_height)
        ops, box = table_ops(x, y, header, rows, h, col_widths, title, layer)
        handles = self.render_ops(ops, zoom=False)
        return {"handles": len(handles), "bbox": rnd(box, 1), "native": False}

    # --- рендер ops из constructions ---
    def render_ops(self, ops: list[dict[str, Any]], text_height: float | None = None, zoom: bool = True) -> list[str]:
        out: list[str] = []
        for op in ops:
            k = op["op"]
            if k == "line":
                out.append(self.add_line(op["p1"], op["p2"], op.get("layer", "0"), op.get("dashed", False),
                                         op.get("lineweight")))
            elif k == "polyline":
                out.append(self.add_polyline(op["points"], op.get("closed", True), op.get("layer", "0"),
                                             op.get("lineweight")))
            elif k == "circle":
                out.append(self.add_circle(op["center"], op["r"], op.get("layer", "0")))
            elif k == "text":
                out.append(self.add_text(op["pos"], op["text"], op.get("height") or text_height,
                                         op.get("layer", C.L_TEXT), op.get("align", "center")))
            elif k == "dim":
                out.append(self.add_linear_dim(op["p1"], op["p2"], op.get("offset", 0), op.get("angle", 0),
                                               op.get("layer", C.L_DIM), op.get("text"),
                                               op.get("text_height") or text_height))
        return out

    # --- существующие объекты ---
    def _get(self, handle: str):
        e = self.doc.entitydb.get(str(handle).upper())
        if e is None or not e.is_alive:
            raise ValueError(f"Объект с handle {handle} не найден")
        return e

    @staticmethod
    def _bbox(e) -> list[float] | None:
        try:
            b = ezbbox.extents([e], fast=True)
        except Exception:  # noqa: BLE001
            return None
        return [b.extmin.x, b.extmin.y, b.extmax.x, b.extmax.y] if b.has_data else None

    def _info(self, e) -> dict[str, Any]:
        return {"handle": e.dxf.handle, "type": e.dxftype(), "layer": e.dxf.get("layer", "0"),
                "color": e.dxf.get("color", 256), "bbox": rnd(self._bbox(e), 1)}

    def list_entities(self, layer=None, etype=None, area=None, crossing=False, limit=200) -> dict[str, Any]:
        res, total = [], 0
        for e in self.msp:
            if not match_names(e.dxftype(), etype) or not match_names(e.dxf.get("layer", "0"), layer):
                continue
            info = self._info(e)
            if not bbox_in_area(info["bbox"], area, crossing):
                continue
            total += 1
            if len(res) < limit:
                res.append(info)
        return {"total": total, "shown": len(res), "entities": res}

    def select(self, layer=None, etype=None, area=None, crossing=False) -> list[str]:
        out = []
        for e in self.msp:
            if not match_names(e.dxftype(), etype) or not match_names(e.dxf.get("layer", "0"), layer):
                continue
            if area and not bbox_in_area(self._bbox(e), area, crossing):
                continue
            out.append(e.dxf.handle)
        return out

    def get_entity(self, handle: str) -> dict[str, Any]:
        e = self._get(handle)
        t = e.dxftype()
        d = self._info(e)
        d.update({"linetype": e.dxf.get("linetype", "BYLAYER"), "lineweight": e.dxf.get("lineweight", -1)})
        if t == "LINE":
            d.update(start=rnd(list(e.dxf.start)[:2]), end=rnd(list(e.dxf.end)[:2]),
                     length=round(e.dxf.start.distance(e.dxf.end), 3))
        elif t in ("CIRCLE", "ARC"):
            d.update(center=rnd(list(e.dxf.center)[:2]), radius=round(e.dxf.radius, 3))
            if t == "ARC":
                d.update(start_angle=round(e.dxf.start_angle, 3), end_angle=round(e.dxf.end_angle, 3))
        elif t == "LWPOLYLINE":
            d.update(points=rnd([list(p) for p in e.get_points("xy")]), closed=e.closed)
        elif t in ("TEXT", "MTEXT"):
            d.update(text=e.dxf.text if t == "TEXT" else e.plain_text(),
                     height=round(e.dxf.height if t == "TEXT" else e.dxf.char_height, 3),
                     insert=rnd(list(e.dxf.insert)[:2]))
        elif t == "INSERT":
            d.update(name=e.dxf.name, insert=rnd(list(e.dxf.insert)[:2]), xscale=e.dxf.xscale,
                     yscale=e.dxf.yscale, rotation=e.dxf.rotation,
                     attributes={a.dxf.tag: a.dxf.text for a in e.attribs})
        elif t == "DIMENSION":
            try:
                d.update(measurement=round(float(e.get_measurement()), 3))
            except Exception:  # noqa: BLE001
                pass
            d.update(text_override=e.dxf.get("text", ""))
        elif t == "HATCH":
            d.update(pattern=e.dxf.pattern_name, solid=bool(e.dxf.solid_fill))
        elif t == "ELLIPSE":
            d.update(center=rnd(list(e.dxf.center)[:2]), major_axis=rnd(list(e.dxf.major_axis)[:2]),
                     ratio=round(e.dxf.ratio, 4))
        elif t == "MULTILEADER":
            try:
                d.update(text=e.context.mtext.default_content)
            except Exception:  # noqa: BLE001
                pass
        if t in ("LINE", "CIRCLE", "ARC", "LWPOLYLINE", "ELLIPSE", "SPLINE", "HATCH"):
            try:
                d.update(self.measure(handle))
            except Exception:  # noqa: BLE001
                pass
        return d

    def measure(self, handle: str) -> dict[str, Any]:
        e = self._get(handle)
        t = e.dxftype()
        if t == "CIRCLE":
            r = e.dxf.radius
            return {"length": round(2 * math.pi * r, 3), "area": round(math.pi * r * r, 3), "closed": True}
        if t == "LWPOLYLINE" and not any(b for *_, b in e.get_points("xyb")):
            length, area = polyline_length_area(list(e.get_points("xy")), e.closed)
            return {"length": round(length, 3), "area": round(area, 3) if e.closed else None, "closed": e.closed}
        if t == "HATCH":
            paths = list(ezpath.from_hatch(e))
            areas = [abs(_path_area(p)) for p in paths]
            return {"area": round(max(areas) - (sum(areas) - max(areas)), 3) if areas else 0.0}
        try:
            p = ezpath.make_path(e)
        except TypeError as ex:
            raise ValueError(f"Измерение для {t} не поддерживается") from ex
        pts = list(p.flattening(0.01))
        length = sum(a.distance(b) for a, b in zip(pts, pts[1:]))
        closed = p.is_closed
        res = {"length": round(length, 3), "closed": closed}
        if closed:
            res["area"] = round(abs(_path_area(p)), 3)
        return res

    # --- правка ---
    def transform(self, handles: list[str], op: str, dx=0.0, dy=0.0, base=(0.0, 0.0), angle=0.0,
                  factor=1.0, p1=None, p2=None, copy=False) -> dict[str, Any]:
        m = _matrix(op, dx, dy, base, angle, factor, p1, p2)
        new: list[str] = []
        for h in handles:
            e = self._get(h)
            target = e
            if copy:
                target = e.copy()
                self.msp.add_entity(target)
                new.append(target.dxf.handle)
            target.transform(m)
            if target.dxftype() == "DIMENSION":
                target.override().render()  # пересобрать блок размера по новым точкам
            b = self._bbox(target)
            if b:
                self.bbox.grow([(b[0], b[1]), (b[2], b[3])])
        return {"count": len(handles), "new_handles": new}

    def erase(self, handles: list[str]) -> int:
        n = 0
        for h in handles:
            e = self.doc.entitydb.get(str(h).upper())
            if e is not None and e.is_alive:
                self.msp.delete_entity(e)
                n += 1
        return n

    def set_props(self, handles: list[str], layer=None, color=None, linetype=None, lineweight=None,
                  ltscale=None) -> int:
        if layer:
            self.ensure_layer(layer)
        if linetype:
            linetype = self.ensure_linetype(linetype)
        for h in handles:
            e = self._get(h)
            if ltscale:
                e.dxf.ltscale = float(ltscale)
            if layer:
                e.dxf.layer = layer
            if color is not None:
                e.dxf.color = int(color)
            if linetype:
                e.dxf.linetype = linetype
            if lineweight is not None:
                e.dxf.lineweight = lineweight_code(lineweight)
        return len(handles)

    def geometry(self, handle: str) -> tuple[str, dict[str, Any]]:
        e = self._get(handle)
        t = e.dxftype()
        if t == "LINE":
            return t, {"start": tuple(e.dxf.start)[:2], "end": tuple(e.dxf.end)[:2]}
        if t in ("CIRCLE", "ARC"):
            return t, {"center": tuple(e.dxf.center)[:2], "radius": e.dxf.radius}
        if t == "LWPOLYLINE":
            return t, {"points": [tuple(p) for p in e.get_points("xy")], "closed": e.closed}
        return t, {}

    def offset(self, handle: str, d_left: float) -> list[str]:
        """d_left > 0: влево от направления (линия/полилиния), наружу (окружность/дуга)."""
        e = self._get(handle)
        t = e.dxftype()
        if t == "LINE":
            s, en = Vec2(e.dxf.start), Vec2(e.dxf.end)
            n = (en - s).orthogonal().normalize() * d_left
            c = e.copy()
            self.msp.add_entity(c)
            c.translate(n.x, n.y, 0)
        elif t in ("CIRCLE", "ARC"):
            if e.dxf.radius + d_left <= 0:
                raise ValueError("Смещение внутрь больше радиуса")
            c = e.copy()
            self.msp.add_entity(c)
            c.dxf.radius = e.dxf.radius + d_left
        elif t == "LWPOLYLINE":
            pts = list(e.get_points("xyb"))
            if any(b for *_, b in pts):
                raise ValueError("Полилинии с дугами смещаются только в режиме AutoCAD (com)")
            new = list(offset_vertices_2d([(x, y) for x, y, _ in pts], offset=d_left, closed=e.closed))
            c = self.msp.add_lwpolyline([(v.x, v.y) for v in new], close=e.closed,
                                        dxfattribs={"layer": e.dxf.layer, "color": e.dxf.get("color", 256)})
        else:
            raise ValueError(f"Смещение для {t} в ezdxf не поддерживается (линия, окружность, дуга, полилиния)")
        b = self._bbox(c)
        if b:
            self.bbox.grow([(b[0], b[1]), (b[2], b[3])])
        return [c.dxf.handle]

    # --- блоки ---
    def create_block(self, name: str, handles: list[str], base=(0.0, 0.0), replace=True,
                     attributes: list[str] | None = None, text_height=None) -> dict[str, Any]:
        if name in self.doc.blocks:
            raise ValueError(f"Блок «{name}» уже существует")
        ents = [self._get(h) for h in handles]
        blk = self.doc.blocks.new(name, base_point=(base[0], base[1]))
        for e in ents:
            if replace:
                self.msp.unlink_entity(e)
                blk.add_entity(e)
            else:
                blk.add_entity(e.copy())
        h = self._text_h(text_height)
        for i, tag in enumerate(attributes or []):
            blk.add_attdef(str(tag).upper(), insert=(base[0], base[1] - (i + 1) * 1.8 * h),
                           dxfattribs={"height": h, "style": "Standard", "prompt": str(tag)})
        res: dict[str, Any] = {"block": name, "entities": len(ents)}
        if replace:
            res["insert_handle"] = self.insert_block(name, base)
        return res

    def insert_block(self, name: str, pos, scale=1.0, rotation=0.0, attributes: dict[str, str] | None = None) -> str:
        if name.lower().endswith(".dxf") and os.path.isfile(name):
            name = self._import_dxf_block(name)
        if name not in self.doc.blocks:
            raise ValueError(f"Блока «{name}» нет. Есть: {', '.join(self._block_names()) or 'нет блоков'}")
        ref = self.msp.add_blockref(name, pos, dxfattribs={"xscale": scale, "yscale": scale, "zscale": scale,
                                                         "rotation": rotation})
        vals = {str(k).upper(): str(v) for k, v in (attributes or {}).items()}
        if any(True for _ in self.doc.blocks.get(name).attdefs()):
            ref.add_auto_attribs(vals)
        b = self._bbox(ref)
        self.bbox.grow([(b[0], b[1]), (b[2], b[3])] if b else [tuple(pos)])
        return ref.dxf.handle

    def _import_dxf_block(self, path: str) -> str:
        from ezdxf.addons import Importer
        name = os.path.splitext(os.path.basename(path))[0]
        if name not in self.doc.blocks:
            src = ezdxf.readfile(path)
            blk = self.doc.blocks.new(name)
            imp = Importer(src, self.doc)
            imp.import_entities(src.modelspace(), blk)
            imp.finalize()
        return name

    def _block_names(self) -> list[str]:
        return [b.name for b in self.doc.blocks if not b.name.startswith("*") and not b.block_record.is_any_layout
                and not b.name.startswith("_")]

    def list_blocks(self, include_inserts: bool = False) -> dict[str, Any]:
        inserts = list(self.msp.query("INSERT"))
        blocks = []
        for n in self._block_names():
            b = self.doc.blocks.get(n)
            blocks.append({"name": n, "entities": len(b), "attributes": [a.dxf.tag for a in b.attdefs()],
                           "inserts": sum(1 for i in inserts if i.dxf.name == n)})
        res: dict[str, Any] = {"blocks": blocks}
        if include_inserts:
            res["inserts"] = [{"handle": i.dxf.handle, "name": i.dxf.name, "insert": rnd(list(i.dxf.insert)[:2], 1),
                               "attributes": {a.dxf.tag: a.dxf.text for a in i.attribs}} for i in inserts]
        return res

    # --- вывод ---
    def extents(self) -> list[float] | None:
        ext = ezbbox.extents(self.msp, fast=True)
        return [ext.extmin.x, ext.extmin.y, ext.extmax.x, ext.extmax.y] if ext.has_data else None

    def render_preview(self, path: str, area=None, width_px: int = 1600) -> dict[str, Any]:
        return preview.render(self.doc, path, area, width_px)

    def export_pdf(self, path: str, area=None, paper_mm=None, scale=None, monochrome=False,
                   paper_name: str = "A3") -> dict[str, Any]:
        return preview.render(self.doc, path, area, paper_mm=paper_mm, scale=scale, monochrome=monochrome)

    # --- прочее ---
    def summary(self) -> dict[str, Any]:
        counts: dict[str, int] = {}
        for e in self.msp:
            counts[e.dxftype()] = counts.get(e.dxftype(), 0) + 1
        return {"file": self.path, "total": self._count(), "by_type": counts}

    def zoom(self, area=None) -> str:
        box = area or self.extents()
        if not box:
            return "Чертёж пуст"
        x1, y1, x2, y2 = box
        self.doc.set_modelspace_vport(max(abs(y2 - y1), abs(x2 - x1) * 0.6) * 1.1, center=((x1 + x2) / 2, (y1 + y2) / 2))
        return "Вид модели сохранён в DXF (покажется при открытии в AutoCAD)"

    def zoom_extents(self) -> str:
        return self.zoom()

    def purge(self) -> dict[str, Any]:
        used_layers = set()
        used_blocks = set()
        for layout in [self.msp, *self.doc.layouts_and_blocks()]:
            for e in layout:
                used_layers.add(e.dxf.get("layer", "0").upper())
                if e.dxftype() == "INSERT":
                    used_blocks.add(e.dxf.name)
        cur = self.doc.header.get("$CLAYER", "0").upper()
        layers = [l.dxf.name for l in self.doc.layers
                  if l.dxf.name.upper() not in used_layers | {"0", "DEFPOINTS", cur}]
        for n in layers:
            self.doc.layers.remove(n)
        blocks = [n for n in self._block_names() if n not in used_blocks]
        for n in blocks:
            self.doc.blocks.delete_block(n, safe=False)
        return {"layers_removed": layers, "blocks_removed": blocks}

    def undo(self, steps: int = 1) -> str:
        return f"undo: {ONLY_COM}. В ezdxf удалите лишнее через erase_entities"

    def run_command(self, command: str) -> str:
        return f"run_command: {ONLY_COM}"


def _path_area(p) -> float:
    pts = [(v.x, v.y) for v in p.flattening(0.01)]
    n = len(pts)
    return sum(pts[i][0] * pts[(i + 1) % n][1] - pts[(i + 1) % n][0] * pts[i][1] for i in range(n)) / 2


def _matrix(op: str, dx, dy, base, angle, factor, p1, p2) -> Matrix44:
    bx, by = base
    if op in ("move", "copy"):
        return Matrix44.translate(dx, dy, 0)
    if op == "rotate":
        return Matrix44.chain(Matrix44.translate(-bx, -by, 0), Matrix44.z_rotate(math.radians(angle)),
                              Matrix44.translate(bx, by, 0))
    if op == "scale":
        if factor <= 0:
            raise ValueError("factor должен быть > 0")
        return Matrix44.chain(Matrix44.translate(-bx, -by, 0), Matrix44.scale(factor, factor, factor),
                              Matrix44.translate(bx, by, 0))
    if op == "mirror":
        if p1 is None or p2 is None:
            raise ValueError("Для mirror нужны две точки оси (x1,y1,x2,y2)")
        a = math.atan2(p2[1] - p1[1], p2[0] - p1[0])
        return Matrix44.chain(Matrix44.translate(-p1[0], -p1[1], 0), Matrix44.z_rotate(-a),
                              Matrix44.scale(1, -1, 1), Matrix44.z_rotate(a), Matrix44.translate(p1[0], p1[1], 0))
    raise ValueError("op: move | copy | rotate | scale | mirror")
