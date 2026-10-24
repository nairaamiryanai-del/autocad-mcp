"""Поиск AutoCAD любой версии на Windows.

Порядок подключения:
  1. AUTOCAD_MCP_PROGID, если задан (например AutoCAD.Application.25.1);
  2. уже запущенный AutoCAD (общий ProgID, затем каждая установленная версия);
  3. запуск самой новой установленной версии.

В реестре после удаления AutoCAD часто остаются записи ProgID без программы на диске.
Такие версии пропускаются: проверяется, что exe из LocalServer32 существует.
"""

from __future__ import annotations

import os
import re
import sys

# номер выпуска (Version у Application) -> название продукта
RELEASES = {
    "20.0": 2015, "20.1": 2016, "21.0": 2017, "22.0": 2018, "23.0": 2019, "23.1": 2020,
    "24.0": 2021, "24.1": 2022, "24.2": 2023, "24.3": 2024, "25.0": 2025, "25.1": 2026,
    "26.0": 2027, "26.1": 2028,
}


def product_name(version: str) -> str:
    """'25.1s (LMS Tech)' -> 'AutoCAD 2026 (R25.1)'."""
    m = re.match(r"(\d+)\.(\d+)", str(version))
    if not m:
        return f"AutoCAD {version}"
    key = f"{int(m.group(1))}.{int(m.group(2))}"
    year = RELEASES.get(key)
    return f"AutoCAD {year} (R{key})" if year else f"AutoCAD R{key}"


def _exe_of(progid: str) -> str | None:
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, progid + r"\CLSID") as k:
            clsid = winreg.QueryValue(k, None)
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, rf"CLSID\{clsid}\LocalServer32") as k:
            cmd = winreg.QueryValue(k, None)
    except OSError:
        return None
    # путь бывает без кавычек и с пробелами: C:\Program Files\...\acad.exe /Automation
    m = re.match(r'\s*"([^"]+)"|\s*(.+?\.exe)\b', cmd or "", re.IGNORECASE)
    path = (m.group(1) or m.group(2)) if m else ""
    return path if path and os.path.isfile(path) else None


def installed_progids() -> list[str]:
    """ProgID установленных версий AutoCAD (есть на диске), от новой к старой."""
    if sys.platform != "win32":
        return []
    import winreg

    found: list[tuple[tuple[int, ...], str]] = []
    i = 0
    while True:
        try:
            name = winreg.EnumKey(winreg.HKEY_CLASSES_ROOT, i)
        except OSError:
            break
        i += 1
        m = re.fullmatch(r"AutoCAD\.Application\.(\d+)(?:\.(\d+))?", name)
        if m and _exe_of(name):
            found.append(((int(m.group(1)), int(m.group(2) or 0)), name))
    # у одной версии бывают оба ProgID (.25 и .25.1): точный идёт первым
    found.sort(key=lambda x: (x[0], x[1].count(".")), reverse=True)
    return [p for _, p in found]


def candidates() -> list[str]:
    forced = os.environ.get("AUTOCAD_MCP_PROGID", "").strip()
    if forced:
        return [forced]
    return ["AutoCAD.Application", *installed_progids()]


def connect():
    """Подключиться к запущенному AutoCAD или запустить установленный. Возвращает (app, progid)."""
    import pywintypes
    import win32com.client

    progids = candidates()
    for pid in progids:
        try:
            return win32com.client.GetActiveObject(pid), pid
        except pywintypes.com_error:
            continue
    errors = []
    # общий ProgID может указывать на удалённую версию: запускаем только то, что реально установлено
    launch = progids if os.environ.get("AUTOCAD_MCP_PROGID") else installed_progids()
    for pid in launch:
        try:
            return win32com.client.Dispatch(pid), pid
        except pywintypes.com_error as e:
            errors.append(f"{pid}: {e}")
    if not launch:
        raise RuntimeError("AutoCAD не найден: не запущен и не установлен. Работайте в режиме ezdxf (DXF без AutoCAD).")
    raise RuntimeError("Не удалось запустить AutoCAD. " + "; ".join(errors))


def is_running() -> bool:
    import pywintypes
    import win32com.client

    for pid in candidates():
        try:
            win32com.client.GetActiveObject(pid)
            return True
        except pywintypes.com_error:
            continue
    return False
