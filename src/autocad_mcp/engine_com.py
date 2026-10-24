"""Живой движок: управляет запущенным AutoCAD через COM (ActiveX).

Только Windows, нужен установленный AutoCAD. Единицы чертежа: мм, углы размеров: радианы.

Важно про потоки. MCP-сервер выполняет инструменты в разных потоках, а COM-объект
AutoCAD можно использовать только в потоке, где он создан. Поэтому все обращения
к AutoCAD идут через ОДИН выделенный поток (COM инициализируется в нём один раз),
а методы движка просто передают туда работу и ждут результат.

Правила (на них держится стабильность):
  - всё общение с AutoCAD только внутри методов с @com_call(); наружу отдаём
    только str/int/float/dict/list, никогда COM-объекты;
  - retry=True только у «атомарных» методов (создают не больше одного объекта);
    составные методы помечены retry=False и вызывают атомарные;
  - свойства нового объекта ставим через _set, мелкие запросы через _retrying,
    обязательные шаги после создания объекта через _call (повтор только этого шага).
"""

from __future__ import annotations

import concurrent.futures
import contextlib
import functools
import math
import os
import threading
import time
from typing import Any

import pythoncom
import pywintypes
import win32com.client
from win32com.client import VARIANT

from . import acad_versions
from . import constructions as C
from .common import (angular_location, bbox_in_area, dxf_type, lineweight_code, linetype_aliases, rnd,
                     version_key)

# AcSaveAsType: формат DWG для «Сохранить как»
DWG_SAVE_TYPES = {"2000": 12, "2004": 24, "2007": 36, "2010": 48, "2013": 60, "2018": 64}
from .layout import BBox, DimStacker, aligned_place, auto_text_height, dim_sizes

LAYER_COLORS = {
    C.L_FRAME: 7, C.L_MULLION: 8, C.L_SASH: 8,
    C.L_OPEN: 5, C.L_HANDLE: 1, C.L_DIM: 3, C.L_TEXT: 7,
}

AC_ALIGN_MIDDLE_CENTER = 10
AC_ALIGN_MIDDLE_LEFT = 9
RUSSIAN_CHARSET = 204
SWISS_VARIABLE_PITCH = 34
AC_SELECTION_SET_ALL = 5
AC_CELL_MIDDLE_CENTER = 5
SEL_NAME = "MCP_SEL"

# AutoCAD занят (модальное окно, идёт команда): повторить через паузу
_HR_RETRY = {-2147418111, -2147417846}  # RPC_E_CALL_REJECTED, RPC_E_SERVERCALL_RETRYLATER
# связь оборвалась (AutoCAD перезапущен): переподключиться
_HR_RECONNECT = {-2147417848, -2147023174, -2147023170}

_PREFIX = "acad-com"
_EXEC = concurrent.futures.ThreadPoolExecutor(
    max_workers=1, thread_name_prefix=_PREFIX, initializer=pythoncom.CoInitialize
)


def com_message(e: Exception) -> str:
    """Понятный текст из COM-ошибки AutoCAD."""
    args = getattr(e, "args", ())
    if len(args) > 2 and isinstance(args[2], tuple) and len(args[2]) > 2 and args[2][2]:
        return str(args[2][2])
    return str(args[1]) if len(args) > 1 else str(e)


def _hresult(e: BaseException):
    """Код COM-ошибки, в том числе спрятанный win32com внутри AttributeError."""
    if isinstance(e, pywintypes.com_error):
        return e.args[0] if e.args else None
    ctx = e.__context__
    if isinstance(ctx, pywintypes.com_error):
        return ctx.args[0] if ctx.args else None
    return None


def _busy(e: BaseException) -> bool:
    """AutoCAD временно занят и отклонил вызов (повторять можно)."""
    hr = _hresult(e)
    if hr in _HR_RETRY:
        return True
    # win32com отдаёт отказ занятого AutoCAD как AttributeError «Объект.Имя» без кода ошибки.
    # Сервер обращается только к существующим именам API AutoCAD, поэтому это временный отказ.
    # Обычные ошибки Python («... object has no attribute ...») сюда не попадают.
    return isinstance(e, AttributeError) and "object has no attribute" not in str(e)


def _set(obj, prop: str, value) -> None:
    """Выставить свойство объекта с повтором (без повтора всей команды и дублей)."""
    _retrying(lambda: setattr(obj, prop, value))


def _retrying(fn, default=None, tries: int = 16):
    """Небольшой запрос с повтором, если AutoCAD занят (например, сразу после сохранения)."""
    for _ in range(tries):
        try:
            return fn()
        except (pywintypes.com_error, AttributeError) as e:
            if _busy(e):
                time.sleep(0.25)
                continue
            return default
    return default


def _call(fn, tries: int = 40):
    """Обязательный шаг: повтор, пока AutoCAD занят; настоящая ошибка уходит наверх.
    Если AutoCAD так и не освободился, ошибка НЕ считается «занятостью», чтобы
    внешний повтор не продублировал уже созданное."""
    for _ in range(tries):
        try:
            return fn()
        except (pywintypes.com_error, AttributeError) as e:
            if _busy(e):
                time.sleep(0.25)
                continue
            raise
    raise RuntimeError("AutoCAD занят и не отвечает, повторите позже")


def _in_com_thread() -> bool:
    return threading.current_thread().name.startswith(_PREFIX)


def _run_guarded(fn, args, kwargs, retry: bool):
    if not retry:
        return fn(*args, **kwargs)
    reconnected = False
    for _ in range(40):  # до ~10 с ожидания занятого AutoCAD
        try:
            return fn(*args, **kwargs)
        except (pywintypes.com_error, AttributeError) as e:
            if _busy(e):
                time.sleep(0.25)
                continue
            hr = _hresult(e)
            if hr in _HR_RECONNECT and not reconnected and args and isinstance(args[0], ComEngine):
                args[0]._connect()
                reconnected = True
                continue
            raise
    return fn(*args, **kwargs)


def com_call(retry: bool = True):
    """Выполнить метод в COM-потоке. retry=True только для «атомарных» вызовов,
    иначе повтор составной операции продублировал бы уже нарисованное."""

    def deco(fn):
        @functools.wraps(fn)
        def run(*args, **kwargs):
            if _in_com_thread():
                return _run_guarded(fn, args, kwargs, retry)
            return _EXEC.submit(_run_guarded, fn, args, kwargs, retry).result()

        return run

    return deco


def _pt(x: float, y: float, z: float = 0.0):
    return VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_R8, (float(x), float(y), float(z)))


def _pt2(x: float, y: float):
    return VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_R8, (float(x), float(y)))


def _coords(flat: list[float]):
    return VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_R8, [float(v) for v in flat])


def _objs(items: list):
    return VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_DISPATCH, list(items))


class ComEngine:
    name = "com"

    def __init__(self, out_dir: str):
        self.out_dir = out_dir
        self.app = None
        self.progid = ""
        self.dim_text: float | None = None  # None = авто по габаритам
        self.stacker = DimStacker()
        self.bbox = BBox()
        self.last_sheet: dict[str, Any] | None = None
        self.sheets: list[dict[str, Any]] = []  # все листы make_sheet текущего чертежа
        self._undo_depth = 0
        self._connect()

    # --- подключение (всё ниже выполняется в COM-потоке) ---
    @com_call(retry=False)
    def _connect(self) -> None:
        # любая версия: запущенная или самая новая из установленных (см. acad_versions)
        self.app, self.progid = acad_versions.connect()
        # только что запущенный AutoCAD долго (до минут) отклоняет вызовы: ждём готовности
        _retrying(lambda: setattr(self.app, "Visible", True), tries=720)
        if (_retrying(lambda: int(self.app.Documents.Count), 0, tries=720) or 0) == 0:
            _call(lambda: self.app.Documents.Add(), tries=240)
        self._prepare_doc(read_extents=True)

    @property
    def doc(self):
        # всегда активный документ: команды идут туда, где смотрит пользователь
        return self.app.ActiveDocument

    @property
    def msp(self):
        return self.doc.ModelSpace

    def _prepare_doc(self, read_extents: bool) -> None:
        """Arial с кириллицей для стиля Standard, сброс раскладки размеров.
        Сразу после создания/открытия документа AutoCAD бывает занят: каждый шаг с повтором."""
        _retrying(lambda: self.doc.Name, tries=40)  # дождаться готовности документа

        def set_font() -> bool:
            st = self.doc.TextStyles.Item("Standard")
            st.SetFont("Arial", False, False, RUSSIAN_CHARSET, SWISS_VARIABLE_PITCH)
            self.doc.ActiveTextStyle = st
            return True

        _retrying(set_font, False, tries=40)
        self.stacker.reset()
        self.bbox.reset()
        self.last_sheet = None
        self.sheets = []
        self._undo_depth = 0
        if read_extents:
            self._read_extents()

    def _read_extents(self) -> None:
        if (_retrying(lambda: int(self.msp.Count), 0) or 0) > 0:
            ext = _retrying(lambda: (self.doc.GetVariable("EXTMIN"), self.doc.GetVariable("EXTMAX")))
            if ext:
                mn, mx = ext
                if mx[0] > mn[0] and abs(mx[0]) < 1e15:
                    self.bbox.reset([mn[0], mn[1], mx[0], mx[1]])

    @staticmethod
    @com_call(retry=False)
    def is_available() -> bool:
        """AutoCAD запущен или установлен (тогда он запустится при подключении)."""
        return acad_versions.is_running() or bool(acad_versions.installed_progids())

    @com_call()
    def status(self) -> dict[str, Any]:
        font = _retrying(lambda: self.doc.TextStyles.Item("Standard").GetFont()[0], "?")
        return {
            "engine": self.name,
            "autocad_connected": True,
            "product": acad_versions.product_name(str(self.app.Version)),
            "version": str(self.app.Version),
            "current_file": self.doc.FullName or self.doc.Name,
            "entities": int(self.msp.Count),
            "text_font": font,
            "annotation_text_height": self.dim_text or f"авто ({self._text_h(None):.0f})",
        }

    # --- группы отмены: одна команда MCP = один шаг UNDO ---
    @contextlib.contextmanager
    def _undo_group(self):
        self._undo_depth += 1
        if self._undo_depth == 1:
            _retrying(lambda: self.doc.StartUndoMark())
        try:
            yield
        finally:
            self._undo_depth -= 1
            if self._undo_depth == 0:
                _retrying(lambda: self.doc.EndUndoMark())

    @com_call(retry=False)
    def begin_group(self) -> None:
        self._undo_depth += 1
        if self._undo_depth == 1:
            _retrying(lambda: self.doc.StartUndoMark())

    @com_call(retry=False)
    def end_group(self) -> None:
        if self._undo_depth > 0:
            self._undo_depth -= 1
            if self._undo_depth == 0:
                _retrying(lambda: self.doc.EndUndoMark())

    # --- документы ---
    @com_call(retry=False)
    def new_drawing(self, template: str | None = None) -> str:
        self.app.Documents.Add()
        self._prepare_doc(read_extents=False)
        return "Создан новый чертёж в AutoCAD"

    @com_call(retry=False)
    def open_drawing(self, path: str) -> str:
        self.app.Documents.Open(path)
        self._prepare_doc(read_extents=True)
        return f"Открыт {path}: {int(self.msp.Count)} объектов"

    @com_call(retry=False)
    def save_drawing(self, path: str | None = None, version: str | None = None) -> str:
        if version:
            # формат старой версии: всегда «Сохранить как», чтобы не перезаписать текущий формат молча
            if path is None:
                if not self.doc.FullName:
                    path = os.path.join(self.out_dir, "drawing.dwg")
                else:
                    stem, _ = os.path.splitext(self.doc.FullName)
                    path = f"{stem}_{version}.dwg"
        if path is None:
            if self.doc.FullName:
                self.doc.Save()
                return self.doc.FullName
            path = os.path.join(self.out_dir, "drawing.dwg")
        if not os.path.splitext(path)[1]:
            path += ".dwg"
        target = os.path.abspath(path)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        # поверх файла, открытого в AutoCAD, сохранить нельзя
        for d in self.app.Documents:
            if d.FullName and os.path.abspath(d.FullName).lower() == target.lower():
                if d.Name == self.doc.Name:
                    self.doc.Save()
                    return target
                raise RuntimeError(
                    f"Файл {target} уже открыт в AutoCAD во вкладке «{d.Name}». "
                    "Закройте эту вкладку или сохраните под другим именем."
                )
        try:
            if version:
                self.doc.SaveAs(target, DWG_SAVE_TYPES[version_key(version)])
            else:
                self.doc.SaveAs(target)
        except pywintypes.com_error as e:
            raise RuntimeError(
                f"AutoCAD не смог сохранить {target}: {com_message(e)}. "
                "Проверьте, что папка существует, а файл не только для чтения."
            ) from e
        return target

    @com_call(retry=False)
    def export_dxf(self, path: str) -> str:
        """Копия текущего чертежа в DXF (текущий документ и его имя не меняются)."""
        base = os.path.abspath(path)
        if base.lower().endswith(".dxf"):
            base = base[:-4]
        os.makedirs(os.path.dirname(base), exist_ok=True)
        target = base + ".dxf"
        if os.path.exists(target):
            os.remove(target)  # перезаписываем файл, который просили создать
        _call(lambda: self.doc.Export(base, "DXF", None))
        if not os.path.exists(target):
            raise RuntimeError(f"AutoCAD не создал {target}")
        return target

    # --- слои и типы линий ---
    @com_call()
    def ensure_layer(self, name: str, color: int | None = None, force: bool = False) -> str:
        """Создать слой, если его нет. Цвет меняется только у нового слоя или при force=True."""
        try:
            lay = self.doc.Layers.Item(name)
            created = False
        except pywintypes.com_error as e:
            if _busy(e):
                raise  # AutoCAD занят: повторить проверку, а не считать слой новым
            lay = self.doc.Layers.Add(name)
            created = True
        if created or force:
            c = color if color is not None else LAYER_COLORS.get(name)
            if c:
                _set(lay, "color", int(c))
        return name

    @com_call()
    def list_layers(self) -> list[dict[str, Any]]:
        cur = self.doc.ActiveLayer.Name
        return [{"name": l.Name, "color": int(l.color), "on": bool(l.LayerOn), "frozen": bool(l.Freeze),
                 "locked": bool(l.Lock), "linetype": l.Linetype, "current": l.Name == cur}
                for l in self.doc.Layers]

    @com_call()
    def ensure_linetype(self, name: str) -> str:
        """Тип линии: если не загружен, подгрузить из acadiso.lin / acad.lin.
        В русской AutoCAD типы называются по-русски: DASHED = штриховая, CENTER = осевая и т. д."""
        if name.upper() in ("BYLAYER", "BYBLOCK", "CONTINUOUS"):
            return name
        names = linetype_aliases(name)
        for n in names:
            try:
                return str(self.doc.Linetypes.Item(n).Name)
            except pywintypes.com_error as e:
                if _busy(e):
                    raise
        for n in names:
            for lin in ("acadiso.lin", "acad.lin"):
                try:
                    self.doc.Linetypes.Load(n, lin)
                    return n
                except pywintypes.com_error as e:
                    if _busy(e):
                        raise
        raise ValueError(f"Тип линии {name} не найден в acadiso.lin/acad.lin")

    @com_call()
    def set_layer(self, name: str, on: bool | None = None, frozen: bool | None = None,
                  locked: bool | None = None, color: int | None = None, linetype: str | None = None,
                  lineweight: float | None = None, current: bool = False) -> dict[str, Any]:
        self.ensure_layer(name, color)
        lay = self.doc.Layers.Item(name)
        is_cur = self.doc.ActiveLayer.Name.upper() == name.upper()
        if frozen and (current or is_cur):
            raise ValueError("Текущий слой нельзя заморозить")
        if current:
            _call(lambda: setattr(self.doc, "ActiveLayer", lay))
        if on is not None:
            _call(lambda: setattr(lay, "LayerOn", bool(on)))
        if frozen is not None:
            _call(lambda: setattr(lay, "Freeze", bool(frozen)))
        if locked is not None:
            _call(lambda: setattr(lay, "Lock", bool(locked)))
        if color is not None:
            _call(lambda: setattr(lay, "color", int(color)))
        if linetype:
            lt = self.ensure_linetype(linetype)
            _call(lambda: setattr(lay, "Linetype", lt))
        if lineweight is not None:
            _call(lambda: setattr(lay, "Lineweight", lineweight_code(lineweight)))
        return {"name": lay.Name, "color": int(lay.color), "on": bool(lay.LayerOn), "frozen": bool(lay.Freeze),
                "locked": bool(lay.Lock), "linetype": lay.Linetype,
                "current": self.doc.ActiveLayer.Name == lay.Name}

    @com_call()
    def delete_layer(self, name: str) -> str:
        try:
            lay = self.doc.Layers.Item(name)
        except pywintypes.com_error as e:
            if _busy(e):
                raise
            raise ValueError(f"Слоя «{name}» нет") from e
        if name.upper() in ("0", "DEFPOINTS") or self.doc.ActiveLayer.Name.upper() == name.upper():
            raise ValueError(f"Слой «{name}» удалить нельзя (служебный или текущий)")
        try:
            lay.Delete()
        except pywintypes.com_error as e:
            if _busy(e):
                raise
            raise RuntimeError(f"Слой «{name}» не удалён: на нём есть объекты или он используется ({com_message(e)})") from e
        return f"Слой «{name}» удалён"

    def _finish(self, ent, layer: str, dashed: bool = False, lineweight: float | None = None):
        # вызывается только из COM-потока
        self.ensure_layer(layer)
        _set(ent, "Layer", layer)
        if dashed:
            try:
                lt = self.ensure_linetype("DASHED")
            except ValueError:
                lt = None
            if lt:
                _set(ent, "Linetype", lt)
        if lineweight:
            _set(ent, "Lineweight", lineweight_code(lineweight))
        return ent

    @staticmethod
    def _handle(ent) -> str:
        return str(_retrying(lambda: ent.Handle, ""))

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
    def _style_dim(dim, h: float, text: str | None) -> None:
        if text is not None:
            _set(dim, "TextOverride", str(text))
        s = dim_sizes(h)
        for prop, val in (
            ("TextHeight", s["text"]), ("ArrowheadSize", s["arrow"]),
            ("ExtensionLineExtend", s["ext_extend"]), ("ExtensionLineOffset", s["ext_offset"]),
            ("TextGap", s["gap"]), ("TextInside", True), ("TextStyle", "Standard"),
        ):
            _set(dim, prop, val)

    # --- примитивы (атомарные: один объект, возвращают handle) ---
    @com_call()
    def add_line(self, p1, p2, layer="0", dashed=False, lineweight=None) -> str:
        ent = self.msp.AddLine(_pt(*p1), _pt(*p2))
        self._finish(ent, layer, dashed, lineweight)
        self.bbox.grow([p1, p2])
        return self._handle(ent)

    @com_call()
    def add_polyline(self, points, closed=True, layer="0", lineweight=None) -> str:
        flat: list[float] = []
        for x, y in points:
            flat += [x, y]
        pl = self.msp.AddLightWeightPolyline(_coords(flat))
        if closed:
            _set(pl, "Closed", True)
        self._finish(pl, layer, lineweight=lineweight)
        self.bbox.grow(points)
        return self._handle(pl)

    @com_call()
    def add_circle(self, center, r, layer="0") -> str:
        ent = self.msp.AddCircle(_pt(*center), float(r))
        self._finish(ent, layer)
        self.bbox.grow([(center[0] - r, center[1] - r), (center[0] + r, center[1] + r)])
        return self._handle(ent)

    @com_call()
    def add_arc(self, center, r, start_deg, end_deg, layer="0") -> str:
        ent = self.msp.AddArc(_pt(*center), float(r), math.radians(start_deg), math.radians(end_deg))
        self._finish(ent, layer)
        self.bbox.grow([(center[0] - r, center[1] - r), (center[0] + r, center[1] + r)])
        return self._handle(ent)

    @com_call()
    def add_ellipse(self, center, rx, ry, rotation_deg=0.0, layer="0") -> str:
        major, ratio, rot = (rx, ry / rx, rotation_deg) if rx >= ry else (ry, rx / ry, rotation_deg + 90)
        a = math.radians(rot)
        ent = self.msp.AddEllipse(_pt(*center), _pt(major * math.cos(a), major * math.sin(a)), float(ratio))
        self._finish(ent, layer)
        m = max(rx, ry)
        self.bbox.grow([(center[0] - m, center[1] - m), (center[0] + m, center[1] + m)])
        return self._handle(ent)

    @com_call()
    def add_point(self, pos, layer="0") -> str:
        ent = self.msp.AddPoint(_pt(*pos))
        self._finish(ent, layer)
        self.bbox.grow([pos])
        return self._handle(ent)

    @com_call()
    def add_spline(self, points, closed=False, layer="0") -> str:
        pts = [tuple(p) for p in points]
        if closed and pts[0] != pts[-1]:
            pts.append(pts[0])
        flat: list[float] = []
        for x, y in pts:
            flat += [x, y, 0.0]
        ent = self.msp.AddSpline(_coords(flat), _pt(0, 0), _pt(0, 0))
        self._finish(ent, layer)
        self.bbox.grow(points)
        return self._handle(ent)

    @com_call()
    def add_text(self, pos, text, height=None, layer=C.L_TEXT, align="center", rotation=0.0) -> str:
        h = self._text_h(height)
        t = self.msp.AddText(str(text), _pt(*pos), float(h))
        _set(t, "StyleName", "Standard")
        if align in ("center", "mleft"):
            _set(t, "Alignment", AC_ALIGN_MIDDLE_CENTER if align == "center" else AC_ALIGN_MIDDLE_LEFT)
            _set(t, "TextAlignmentPoint", _pt(*pos))
        if rotation:
            _set(t, "Rotation", math.radians(rotation))
        self._finish(t, layer)
        self.bbox.grow([pos])
        return self._handle(t)

    @com_call()
    def add_mtext(self, pos, text, height=None, width=0.0, layer=C.L_TEXT, align="left") -> str:
        h = self._text_h(height)
        m = self.msp.AddMText(_pt(*pos), float(width or 0.0), str(text).replace("\n", "\\P"))
        _set(m, "StyleName", "Standard")
        _set(m, "Height", float(h))
        if align == "center":
            _set(m, "AttachmentPoint", 5)
            _set(m, "InsertionPoint", _pt(*pos))
        self._finish(m, layer)
        self.bbox.grow([pos])
        return self._handle(m)

    @com_call()
    def _hatch_loop(self, boundary_handle: str, pattern: str, scale: float, angle: float,
                    color, layer: str, associative: bool) -> str:
        """Штриховка по одному замкнутому контуру (создаёт один объект)."""
        boundary = self._obj(boundary_handle)
        solid = pattern.upper() == "SOLID"
        h = self.msp.AddHatch(1, "SOLID" if solid else pattern.upper(), bool(associative))
        if not solid:
            _set(h, "PatternScale", float(scale))
            _set(h, "PatternAngle", math.radians(angle))
        _call(lambda: h.AppendOuterLoop(_objs([boundary])))
        if color is not None:
            _set(h, "color", int(color))
        _retrying(lambda: h.Evaluate())
        self._finish(h, layer)
        return self._handle(h)

    @com_call(retry=False)
    def add_hatch(self, points=None, handle=None, pattern="ANSI31", scale=1.0, angle=0.0,
                  color=None, layer="HATCH") -> str:
        if handle:
            return self._hatch_loop(handle, pattern, scale, angle, color, layer, True)
        # временный контур по точкам: штриховка неассоциативная, контур потом удаляем
        tmp = self.add_polyline([tuple(p[:2]) for p in points], True, layer)
        try:
            return self._hatch_loop(tmp, pattern, scale, angle, color, layer, False)
        finally:
            self._erase_one(tmp)

    # --- размеры и выноски ---
    @com_call()
    def add_linear_dim(self, p1, p2, offset=0.0, angle=0, layer=C.L_DIM, text=None, text_height=None) -> str:
        h = self._text_h(text_height)
        length = abs(p2[1] - p1[1]) if angle == 90 else abs(p2[0] - p1[0])
        label = str(text) if text is not None else f"{length:.0f}"
        mark = len(self.stacker.boxes)
        if angle == 90:
            mid = (p1[1] + p2[1]) / 2
            line = self.stacker.place(90, p1[0] + offset, mid, label, h, offset)
            loc = (line, mid)
            rot = math.radians(90)
        else:
            mid = (p1[0] + p2[0]) / 2
            line = self.stacker.place(0, p1[1] + offset, mid, label, h, offset)
            loc = (mid, line)
            rot = 0.0
        try:
            dim = self.msp.AddDimRotated(_pt(*p1), _pt(*p2), _pt(*loc), rot)
        except BaseException:
            del self.stacker.boxes[mark:]  # размер не создан: при повторе не сдвигать на лишний уровень
            raise
        self._style_dim(dim, h, text)
        self._finish(dim, layer)
        self.bbox.grow([p1, p2, loc])
        return self._handle(dim)

    @com_call()
    def add_aligned_dim(self, p1, p2, offset=300.0, layer=C.L_DIM, text=None, text_height=None) -> str:
        h = self._text_h(text_height)
        label = str(text) if text is not None else f"{math.dist(p1, p2):.0f}"
        mark = len(self.stacker.boxes)
        _, loc = aligned_place(self.stacker, p1, p2, offset, label, h)
        try:
            dim = self.msp.AddDimAligned(_pt(*p1), _pt(*p2), _pt(*loc))
        except BaseException:
            del self.stacker.boxes[mark:]
            raise
        self._style_dim(dim, h, text)
        self._finish(dim, layer)
        self.bbox.grow([p1, p2, loc])
        return self._handle(dim)

    @com_call()
    def add_radial_dim(self, center, r, angle_deg=45.0, diameter=False, layer=C.L_DIM,
                       text=None, text_height=None) -> str:
        h = self._text_h(text_height)
        a = math.radians(angle_deg)
        chord = (center[0] + r * math.cos(a), center[1] + r * math.sin(a))
        if diameter:
            far = (center[0] - r * math.cos(a), center[1] - r * math.sin(a))
            dim = self.msp.AddDimDiametric(_pt(*chord), _pt(*far), float(2 * h))
        else:
            dim = self.msp.AddDimRadial(_pt(*center), _pt(*chord), float(2 * h))
        self._style_dim(dim, h, text)
        self._finish(dim, layer)
        self.bbox.grow([(center[0] - r, center[1] - r), (center[0] + r, center[1] + r)])
        return self._handle(dim)

    @com_call()
    def add_angular_dim(self, vertex, p1, p2, radius=None, layer=C.L_DIM, text=None, text_height=None) -> str:
        h = self._text_h(text_height)
        loc = angular_location(vertex, p1, p2, radius)
        dim = self.msp.AddDimAngular(_pt(*vertex), _pt(*p1), _pt(*p2), _pt(*loc))
        self._style_dim(dim, h, text)
        _set(dim, "TextPrecision", 0)
        self._finish(dim, layer)
        self.bbox.grow([vertex, p1, p2, loc])
        return self._handle(dim)

    @com_call()
    def add_leader(self, points, text, layer=C.L_DIM, text_height=None) -> str:
        h = self._text_h(text_height)
        flat: list[float] = []
        for p in points:
            flat += [float(p[0]), float(p[1]), 0.0]
        res = self.msp.AddMLeader(_coords(flat), 0)
        ml = res[0] if isinstance(res, tuple) else res
        _set(ml, "TextString", str(text))
        _set(ml, "TextStyleName", "Standard")
        _set(ml, "TextHeight", float(h))
        _set(ml, "ArrowheadSize", float(h * 0.9))
        _set(ml, "DoglegLength", float(h))
        _set(ml, "LandingGap", float(h * 0.3))
        self._finish(ml, layer)
        self.bbox.grow([tuple(p[:2]) for p in points])
        return self._handle(ml)

    # --- таблица (родная таблица AutoCAD) ---
    @com_call(retry=False)
    def add_table(self, x, y, header, rows, title=None, col_widths=None, text_height=None, layer="TABLE") -> dict[str, Any]:
        from .sheets import table_layout
        h = self._text_h(text_height)
        lay = table_layout(header, rows, h, col_widths, title)
        widths, rh, ncols = lay["widths"], lay["row_h"], lay["ncols"]
        lines = ([header] if header else []) + [list(r) for r in rows]
        nrows = len(lines) + 1  # строка 0 у стиля Standard: заголовок
        tb = _call(lambda: self.msp.AddTable(_pt(x, y), nrows, ncols, float(rh), float(widths[0])))
        _set(tb, "RegenerateTableSuppressed", True)
        try:
            _call(lambda: tb.SetTextStyle(7, "Standard"))
            _call(lambda: tb.SetTextHeight(1 | 4, float(h)))
            _call(lambda: tb.SetTextHeight(2, float(h * 1.2)))
            _set(tb, "VertCellMargin", float(h * 0.3))
            _set(tb, "HorzCellMargin", float(h * 0.4))
            if title:
                _call(lambda: tb.SetText(0, 0, str(title)))
            else:
                _call(lambda: tb.DeleteRows(0, 1))
            for c, w in enumerate(widths):
                _call(lambda c=c, w=w: tb.SetColumnWidth(c, float(w)))
            first = 1 if title else 0
            for i, row in enumerate(lines):
                r = first + i
                for c in range(ncols):
                    v = row[c] if c < len(row) else ""
                    if v not in (None, ""):
                        _call(lambda r=r, c=c, v=v: tb.SetText(r, c, str(v)))
                    _call(lambda r=r, c=c: tb.SetCellAlignment(r, c, AC_CELL_MIDDLE_CENTER))
            total_rows = int(_retrying(lambda: tb.Rows, nrows))
            for r in range(total_rows):
                _retrying(lambda r=r: tb.SetRowHeight(r, float(rh)))
        finally:
            _set(tb, "RegenerateTableSuppressed", False)
        self._finish(tb, layer)
        W = sum(widths)
        H = _retrying(lambda: float(tb.Height), rh * total_rows)
        self.bbox.grow([(x, y), (x + W, y - H)])
        return {"handle": self._handle(tb), "bbox": rnd([x, y - H, x + W, y], 1), "native": True}

    # --- рендер ops из constructions (составной) ---
    @com_call(retry=False)
    def render_ops(self, ops: list[dict[str, Any]], text_height: float | None = None, zoom: bool = True) -> list[str]:
        out: list[str] = []
        with self._undo_group():
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
                                             op.get("layer", C.L_TEXT), op.get("align", "center"),
                                             op.get("rotation", 0.0)))
                elif k == "dim":
                    out.append(self.add_linear_dim(op["p1"], op["p2"], op.get("offset", 0), op.get("angle", 0),
                                                   op.get("layer", C.L_DIM), op.get("text"),
                                                   op.get("text_height") or text_height))
        if zoom:
            self.zoom_extents()
        return out

    # --- существующие объекты ---
    def _obj(self, handle: str):
        try:
            return self.doc.HandleToObject(str(handle).upper())
        except pywintypes.com_error as e:
            if _busy(e):
                raise
            raise ValueError(f"Объект с handle {handle} не найден") from e

    @staticmethod
    def _ent_bbox(e) -> list[float] | None:
        try:
            mn, mx = e.GetBoundingBox()
            return [mn[0], mn[1], mx[0], mx[1]]
        except pywintypes.com_error as ex:
            if _busy(ex):
                raise
            return None

    def _info(self, e) -> dict[str, Any]:
        return {"handle": e.Handle, "type": dxf_type(e.ObjectName), "layer": e.Layer,
                "color": int(e.color), "bbox": rnd(self._ent_bbox(e), 1)}

    def _selection(self, layer=None, etype=None):
        """Набор объектов модели по фильтру (слой/тип, шаблоны через запятую и *)."""
        try:
            self.doc.SelectionSets.Item(SEL_NAME).Delete()
        except pywintypes.com_error as e:
            if _busy(e):
                raise
        ss = self.doc.SelectionSets.Add(SEL_NAME)
        codes, vals = [410], ["Model"]
        if etype:
            codes.append(0)
            vals.append(etype.upper())
        if layer:
            codes.append(8)
            vals.append(layer)
        ss.Select(AC_SELECTION_SET_ALL, pythoncom.Empty, pythoncom.Empty,
                  VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_I2, codes),
                  VARIANT(pythoncom.VT_ARRAY | pythoncom.VT_VARIANT, vals))
        return ss

    @com_call()
    def list_entities(self, layer=None, etype=None, area=None, crossing=False, limit=200) -> dict[str, Any]:
        ss = self._selection(layer, etype)
        res, total = [], 0
        try:
            for e in ss:
                if area:
                    if not bbox_in_area(self._ent_bbox(e), area, crossing):
                        continue
                total += 1
                if len(res) < limit:
                    res.append(self._info(e))
        finally:
            _retrying(lambda: ss.Delete())
        return {"total": total, "shown": len(res), "entities": res}

    @com_call()
    def select(self, layer=None, etype=None, area=None, crossing=False) -> list[str]:
        ss = self._selection(layer, etype)
        out = []
        try:
            for e in ss:
                if area and not bbox_in_area(self._ent_bbox(e), area, crossing):
                    continue
                out.append(e.Handle)
        finally:
            _retrying(lambda: ss.Delete())
        return out

    @com_call()
    def get_entity(self, handle: str) -> dict[str, Any]:
        e = self._obj(handle)
        d = self._info(e)
        t = d["type"]
        d.update(linetype=e.Linetype, lineweight=int(e.Lineweight))
        g = lambda prop: _retrying(lambda: getattr(e, prop), None, tries=4)  # noqa: E731
        if t == "LINE":
            d.update(start=rnd(list(e.StartPoint)[:2]), end=rnd(list(e.EndPoint)[:2]), length=rnd(e.Length))
        elif t in ("CIRCLE", "ARC"):
            d.update(center=rnd(list(e.Center)[:2]), radius=rnd(e.Radius))
            if t == "ARC":
                d.update(start_angle=rnd(math.degrees(e.StartAngle)), end_angle=rnd(math.degrees(e.EndAngle)))
        elif t == "LWPOLYLINE":
            c = list(e.Coordinates)
            d.update(points=rnd([[c[i], c[i + 1]] for i in range(0, len(c), 2)]), closed=bool(e.Closed))
        elif t in ("TEXT", "MTEXT"):
            d.update(text=e.TextString, height=rnd(e.Height), insert=rnd(list(e.InsertionPoint)[:2]))
        elif t == "INSERT":
            d.update(name=g("EffectiveName") or e.Name, insert=rnd(list(e.InsertionPoint)[:2]),
                     xscale=rnd(e.XScaleFactor), yscale=rnd(e.YScaleFactor),
                     rotation=rnd(math.degrees(e.Rotation)), attributes=self._attrs_of(e))
        elif t == "DIMENSION":
            d.update(measurement=rnd(g("Measurement")), text_override=g("TextOverride") or "")
        elif t == "HATCH":
            d.update(pattern=e.PatternName, area=rnd(g("Area")))
        elif t == "ELLIPSE":
            d.update(center=rnd(list(e.Center)[:2]), major_axis=rnd(list(e.MajorAxis)[:2]), ratio=rnd(e.RadiusRatio, 4))
        elif t == "MULTILEADER":
            d.update(text=g("TextString"))
        elif t == "ACAD_TABLE":
            d.update(rows=int(e.Rows), columns=int(e.Columns))
        if t in ("LINE", "CIRCLE", "ARC", "LWPOLYLINE", "ELLIPSE", "SPLINE"):
            d.update(self._measure_obj(e, t))
        return d

    @staticmethod
    def _attrs_of(ref) -> dict[str, str]:
        if not _retrying(lambda: bool(ref.HasAttributes), False):
            return {}
        atts = _retrying(lambda: ref.GetAttributes(), ()) or ()
        return {a.TagString: a.TextString for a in atts}

    @staticmethod
    def _measure_obj(e, t: str) -> dict[str, Any]:
        g = lambda prop: _retrying(lambda: getattr(e, prop), None, tries=4)  # noqa: E731
        if t == "LINE":
            return {"length": rnd(g("Length"))}
        if t == "LWPOLYLINE":
            closed = bool(g("Closed"))
            return {"length": rnd(g("Length")), "area": rnd(g("Area")) if closed else None, "closed": closed}
        if t == "CIRCLE":
            return {"length": rnd(g("Circumference")), "area": rnd(g("Area")), "closed": True}
        if t == "ARC":
            return {"length": rnd(g("ArcLength")), "closed": False}
        if t in ("ELLIPSE", "SPLINE", "HATCH", "REGION"):
            res = {"area": rnd(g("Area"))}
            if t == "REGION":
                res["length"] = rnd(g("Perimeter"))
            return res
        raise ValueError(f"Измерение для {t} не поддерживается")

    @com_call()
    def measure(self, handle: str) -> dict[str, Any]:
        e = self._obj(handle)
        return self._measure_obj(e, dxf_type(e.ObjectName))

    @com_call()
    def geometry(self, handle: str) -> tuple[str, dict[str, Any]]:
        e = self._obj(handle)
        t = dxf_type(e.ObjectName)
        if t == "LINE":
            return t, {"start": tuple(e.StartPoint)[:2], "end": tuple(e.EndPoint)[:2]}
        if t in ("CIRCLE", "ARC"):
            return t, {"center": tuple(e.Center)[:2], "radius": float(e.Radius)}
        if t == "LWPOLYLINE":
            c = list(e.Coordinates)
            return t, {"points": [(c[i], c[i + 1]) for i in range(0, len(c), 2)], "closed": bool(e.Closed)}
        return t, {}

    # --- правка ---
    @com_call()
    def _xform_one(self, handle, op, dx, dy, base, angle, factor, p1, p2, copy) -> str | None:
        ent = self._obj(handle)
        if op == "mirror":
            new = ent.Mirror(_pt(*p1), _pt(*p2))  # AutoCAD всегда создаёт отражённую копию
            nh = self._handle(new)
            if not copy:
                _call(lambda: ent.Delete())
            return nh
        target = ent.Copy() if copy else ent
        if op in ("move", "copy"):
            _call(lambda: target.Move(_pt(0, 0), _pt(dx, dy)))
        elif op == "rotate":
            _call(lambda: target.Rotate(_pt(*base), math.radians(angle)))
        elif op == "scale":
            _call(lambda: target.ScaleEntity(_pt(*base), float(factor)))
        return self._handle(target) if copy else None

    @com_call(retry=False)
    def transform(self, handles: list[str], op: str, dx=0.0, dy=0.0, base=(0.0, 0.0), angle=0.0,
                  factor=1.0, p1=None, p2=None, copy=False) -> dict[str, Any]:
        if op not in ("move", "copy", "rotate", "scale", "mirror"):
            raise ValueError("op: move | copy | rotate | scale | mirror")
        if op == "scale" and factor <= 0:
            raise ValueError("factor должен быть > 0")
        if op == "mirror" and (p1 is None or p2 is None):
            raise ValueError("Для mirror нужны две точки оси (x1,y1,x2,y2)")
        copy = copy or op == "copy"
        new: list[str] = []
        with self._undo_group():
            for h in handles:
                nh = self._xform_one(h, op, dx, dy, base, angle, factor, p1, p2, copy)
                if nh:
                    new.append(nh)
        res: dict[str, Any] = {"count": len(handles), "new_handles": new}
        if op == "mirror" and not copy:
            res["note"] = "AutoCAD создаёт отражённый объект с новым handle, исходный удалён"
        return res

    @com_call()
    def _erase_one(self, handle: str) -> bool:
        try:
            ent = self._obj(handle)
        except ValueError:
            return False
        _call(lambda: ent.Delete())
        return True

    @com_call(retry=False)
    def erase(self, handles: list[str]) -> int:
        with self._undo_group():
            return sum(1 for h in handles if self._erase_one(h))

    @com_call()
    def _props_one(self, handle, layer, color, linetype, lineweight, ltscale=None) -> None:
        e = self._obj(handle)
        if ltscale:
            _set(e, "LinetypeScale", float(ltscale))
        if layer:
            _set(e, "Layer", layer)
        if color is not None:
            _set(e, "color", int(color))
        if linetype:
            _set(e, "Linetype", linetype)
        if lineweight is not None:
            _set(e, "Lineweight", lineweight_code(lineweight))

    @com_call(retry=False)
    def set_props(self, handles: list[str], layer=None, color=None, linetype=None, lineweight=None,
                  ltscale=None) -> int:
        if layer:
            self.ensure_layer(layer)
        if linetype:
            linetype = self.ensure_linetype(linetype)
        with self._undo_group():
            for h in handles:
                self._props_one(h, layer, color, linetype, lineweight, ltscale)
        return len(handles)

    @com_call()
    def _offset_once(self, handle: str, native: float) -> list[str]:
        e = self._obj(handle)
        res = e.Offset(float(native))
        return [self._handle(o) for o in (res if isinstance(res, tuple) else (res,))]

    @com_call(retry=False)
    def offset(self, handle: str, d_left: float) -> list[str]:
        """d_left > 0: влево от направления (линия, полилиния) или наружу (окружность, дуга)."""
        kind, _ = self.geometry(handle)
        # у полилиний AutoCAD смещает «+» вправо от направления, у линий влево, у окружностей наружу
        native = -d_left if kind == "LWPOLYLINE" else d_left
        return self._offset_once(handle, native)

    # --- блоки ---
    @com_call(retry=False)
    def create_block(self, name: str, handles: list[str], base=(0.0, 0.0), replace=True,
                     attributes: list[str] | None = None, text_height=None) -> dict[str, Any]:
        exists = True
        try:
            _call(lambda: self.doc.Blocks.Item(name))
        except pywintypes.com_error:
            exists = False
        if exists:
            raise ValueError(f"Блок «{name}» уже существует")
        objs = [_call(lambda h=h: self._obj(h)) for h in handles]
        h = self._text_h(text_height)
        with self._undo_group():
            blk = _call(lambda: self.doc.Blocks.Add(_pt(*base), name))
            if objs:
                _call(lambda: self.doc.CopyObjects(_objs(objs), blk))
            for i, tag in enumerate(attributes or []):
                att = _call(lambda i=i, tag=tag: blk.AddAttribute(
                    float(h), 0, str(tag), _pt(base[0], base[1] - (i + 1) * 1.8 * h), str(tag).upper(), ""))
                _set(att, "StyleName", "Standard")
            res: dict[str, Any] = {"block": name, "entities": len(objs)}
            if replace:
                for o in objs:
                    _call(lambda o=o: o.Delete())
                res["insert_handle"] = self.insert_block(name, base)
        return res

    @com_call()
    def insert_block(self, name: str, pos, scale=1.0, rotation=0.0, attributes: dict[str, str] | None = None) -> str:
        is_file = name.lower().endswith(".dwg") and os.path.isfile(name)
        if not is_file:
            try:
                self.doc.Blocks.Item(name)
            except pywintypes.com_error as e:
                if _busy(e):
                    raise
                raise ValueError(f"Блока «{name}» нет. Есть: {', '.join(self._block_names()) or 'нет блоков'}") from e
        ref = self.msp.InsertBlock(_pt(*pos), name, float(scale), float(scale), float(scale), math.radians(rotation))
        vals = {str(k).upper(): str(v) for k, v in (attributes or {}).items()}
        if vals and _retrying(lambda: bool(ref.HasAttributes), False):
            for a in _retrying(lambda: ref.GetAttributes(), ()) or ():
                tag = _retrying(lambda a=a: a.TagString, "")
                if tag.upper() in vals:
                    _set(a, "TextString", vals[tag.upper()])
        self._finish(ref, "0")
        b = _retrying(lambda: self._ent_bbox(ref))
        self.bbox.grow([(b[0], b[1]), (b[2], b[3])] if b else [tuple(pos)])
        return self._handle(ref)

    def _block_names(self) -> list[str]:
        out = []
        for b in self.doc.Blocks:
            n = b.Name
            if n.startswith("*") or n.startswith("_") or b.IsLayout or b.IsXRef:
                continue
            out.append(n)
        return out

    @com_call()
    def list_blocks(self, include_inserts: bool = False) -> dict[str, Any]:
        ss = self._selection(None, "INSERT")
        try:
            refs = []
            for r in ss:
                nm = _retrying(lambda r=r: r.EffectiveName, None) or r.Name
                refs.append((r, nm))
            blocks = []
            for n in self._block_names():
                b = self.doc.Blocks.Item(n)
                tags = [e.TagString for e in b if e.ObjectName == "AcDbAttributeDefinition"]
                blocks.append({"name": n, "entities": int(b.Count), "attributes": tags,
                               "inserts": sum(1 for _, nm in refs if nm == n)})
            res: dict[str, Any] = {"blocks": blocks}
            if include_inserts:
                res["inserts"] = [{"handle": r.Handle, "name": nm, "insert": rnd(list(r.InsertionPoint)[:2], 1),
                                   "attributes": self._attrs_of(r)} for r, nm in refs]
        finally:
            _retrying(lambda: ss.Delete())
        return res

    # --- вывод ---
    @com_call()
    def extents(self) -> list[float] | None:
        n = int(self.msp.Count)
        if n == 0:
            return None
        if n > 3000:  # большой чертёж: системные переменные (могут быть чуть шире)
            mn, mx = self.doc.GetVariable("EXTMIN"), self.doc.GetVariable("EXTMAX")
            return [mn[0], mn[1], mx[0], mx[1]]
        box = None
        for e in self.msp:
            b = self._ent_bbox(e)
            if b is None:
                continue
            box = b if box is None else [min(box[0], b[0]), min(box[1], b[1]), max(box[2], b[2]), max(box[3], b[3])]
        return box

    def render_preview(self, path: str, area=None, width_px: int = 1600) -> dict[str, Any]:
        """PNG: копия чертежа в DXF + рендер через ezdxf (на светлом фоне)."""
        import ezdxf

        from . import preview
        tmp = self.export_dxf(os.path.join(self.out_dir, f"_preview_{os.getpid()}.dxf"))
        try:
            doc = ezdxf.readfile(tmp)
            return preview.render(doc, path, area, width_px)
        finally:
            with contextlib.suppress(OSError):
                os.remove(tmp)

    @com_call(retry=False)
    def export_pdf(self, path: str, area=None, paper_mm=None, scale=None, monochrome=False,
                   paper_name: str = "A3") -> dict[str, Any]:
        """PDF через плоттер «DWG To PDF.pc3» (модель; рамка/границы, масштаб 1:N или вписать)."""
        target = os.path.abspath(path if path.lower().endswith(".pdf") else path + ".pdf")
        os.makedirs(os.path.dirname(target), exist_ok=True)
        lay = _call(lambda: self.doc.ModelSpace.Layout)
        _call(lambda: setattr(lay, "ConfigName", "DWG To PDF.pc3"))
        _retrying(lambda: lay.RefreshPlotDeviceInfo())
        names = list(_call(lambda: lay.GetCanonicalMediaNames()))
        pw, ph = paper_mm or (420.0, 297.0)
        want = f"_{paper_name.upper()}_({pw:.2f}_x_{ph:.2f}_MM)"
        media = next((n for n in names if "full_bleed" in n and n.endswith(want)), None) \
            or next((n for n in names if n.endswith(want)), None)
        if not media:
            raise RuntimeError(f"У плоттера DWG To PDF нет листа {paper_name} {pw:.0f}×{ph:.0f}")
        _call(lambda: setattr(lay, "CanonicalMediaName", media))
        _call(lambda: setattr(lay, "PaperUnits", 1))      # мм
        _call(lambda: setattr(lay, "PlotRotation", 0))
        if area:
            x1, y1, x2, y2 = area
            _call(lambda: lay.SetWindowToPlot(_pt2(min(x1, x2), min(y1, y2)), _pt2(max(x1, x2), max(y1, y2))))
            _call(lambda: setattr(lay, "PlotType", 4))    # acWindow
        else:
            _call(lambda: setattr(lay, "PlotType", 1))    # acExtents
        if scale:
            _call(lambda: setattr(lay, "UseStandardScale", False))
            _call(lambda: lay.SetCustomScale(1.0, float(scale)))
        else:
            _call(lambda: setattr(lay, "UseStandardScale", True))
            _call(lambda: setattr(lay, "StandardScale", 0))  # вписать
        _call(lambda: setattr(lay, "CenterPlot", True))
        _set(lay, "PlotWithLineweights", True)
        if monochrome:
            _set(lay, "StyleSheet", "monochrome.ctb")
            _set(lay, "PlotWithPlotStyles", True)
        old_bg = _retrying(lambda: self.doc.GetVariable("BACKGROUNDPLOT"))
        _retrying(lambda: self.doc.SetVariable("BACKGROUNDPLOT", 0))  # печать без фона: файл готов сразу
        try:
            if os.path.exists(target):
                os.remove(target)
            ok = _call(lambda: self.doc.Plot.PlotToFile(target))
        finally:
            if old_bg is not None:
                _retrying(lambda: self.doc.SetVariable("BACKGROUNDPLOT", old_bg))
        if not ok or not os.path.exists(target):
            raise RuntimeError("AutoCAD не создал PDF (проверьте плоттер DWG To PDF.pc3)")
        return {"path": target, "bytes": os.path.getsize(target), "media": media}

    # --- прочее ---
    @com_call()
    def summary(self) -> dict[str, Any]:
        return {"file": self.doc.FullName or self.doc.Name, "total": int(self.msp.Count)}

    @com_call()
    def zoom_extents(self) -> str:
        self.app.ZoomExtents()
        return "zoom extents"

    @com_call()
    def zoom(self, area=None) -> str:
        if area:
            x1, y1, x2, y2 = area
            self.app.ZoomWindow(_pt(x1, y1), _pt(x2, y2))
            return f"Показана область ({x1},{y1})-({x2},{y2})"
        self.app.ZoomExtents()
        return "Показан весь чертёж"

    @com_call()
    def purge(self) -> dict[str, Any]:
        before_l, before_b = int(self.doc.Layers.Count), int(self.doc.Blocks.Count)
        self.doc.PurgeAll()
        return {"layers_removed": before_l - int(self.doc.Layers.Count),
                "blocks_removed": before_b - int(self.doc.Blocks.Count)}

    def _wait_idle(self, timeout: float = 30.0) -> bool:
        t0 = time.time()
        while time.time() - t0 < timeout:
            if _retrying(lambda: bool(self.app.GetAcadState().IsQuiescent), False, tries=2):
                return True
            time.sleep(0.2)
        return False

    @com_call(retry=False)
    def run_command(self, command: str) -> str:
        cmd = command if command.endswith((" ", "\n")) else command + " "
        before = _retrying(lambda: int(self.msp.Count), 0)
        _call(lambda: self.doc.SendCommand(cmd))
        idle = self._wait_idle()
        after = _retrying(lambda: int(self.msp.Count), 0)
        self.stacker.reset()
        self._read_extents()
        msg = f"Команда отправлена: {cmd.strip()!r}. Объектов: {before} -> {after}"
        if not idle:
            msg += ". AutoCAD всё ещё ждёт ввода: завершите команду в AutoCAD (Esc) или отправьте ответ следующей командой"
        return msg

    @com_call(retry=False)
    def undo(self, steps: int = 1) -> str:
        n = max(1, int(steps))
        before = _retrying(lambda: int(self.msp.Count), 0)
        _call(lambda: self.doc.SendCommand(f"_.UNDO {n} "))
        self._wait_idle()
        after = _retrying(lambda: int(self.msp.Count), 0)
        self.stacker.reset()
        self.bbox.reset()
        self._read_extents()
        return f"Отменено шагов: {n}. Объектов: {before} -> {after}"
