"""Комплект документации, этап 1: рамка с боковыми графами, многостраничный PDF, каталог профилей, статрасчёт.
Эталон расчёта: пример проекта (лист 3): стропило 430 см, 45°, шаг 115 см, снег II, ветер V, местность B, 20 м -> Jx 407,85.

Запуск: .venv\\Scripts\\python tests\\project_check.py [ezdxf|com]
"""

import json
import os
import sys
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
MODE = sys.argv[1] if len(sys.argv) > 1 else "ezdxf"
OUT = tempfile.mkdtemp(prefix="acad_project_")  # свой каталог профилей не портим
os.environ["AUTOCAD_MCP_ENGINE"] = MODE
os.environ["AUTOCAD_MCP_OUT"] = OUT
sys.path.insert(0, os.path.join(ROOT, "src"))

from autocad_mcp import calc as K  # noqa: E402
from autocad_mcp import server as S  # noqa: E402
from autocad_mcp import sheets as SH  # noqa: E402

fails = 0


def check(name, ok, info=""):
    global fails
    print(("OK   " if ok else "FAIL ") + name + (f" {info}" if info else ""))
    fails += 0 if ok else 1


J = json.loads

# рамка: боковые графы и подписи под рамкой
ops, info = SH.sheet_ops("A3", True, 1, [0, 0, 100, 100], {})
texts = [o["text"] for o in ops if o["op"] == "text"]
check("боковые графы ГОСТ 2.104", all(t in texts for t in ("Инв. № подл.", "Взам. инв. №", "Инв. № дубл.", "Перв. примен.", "Справ. №")))
check("подписи графы вертикальные", all(o.get("rotation") == 90 for o in ops if o["op"] == "text" and o["text"] == "Инв. № подл."))
check("«Копировал» и «Формат A3»", "Копировал" in texts and "Формат A3" in texts)
ops2, _ = SH.sheet_ops("A3", True, 1, [0, 0, 100, 100], {}, side_stamp=False)
check("без боковых граф по запросу", not any(o.get("text") == "Инв. № подл." for o in ops2 if o["op"] == "text"))

# расчёт: точно как в примере
r = K.rafter_check(length_cm=430, angle_deg=45, step_cm=115, glass_mm=12, snow_region="II", wind_region="V",
                   terrain="B", height_m=20, profiles=[{"article": "ALM150175", "role": "стойка", "ix_cm4": 467}])
check("пример: часть II = 31 876 035 452,89", r["jx_parts"]["II"] == 31876035452.89, str(r["jx_parts"]["II"]))
check("пример: часть III = 1 017 666,7", r["jx_parts"]["III"] == 1017666.7)
check("пример: Jx треб = 407,85 см4", r["jx_required_cm4"] == 407.85)
check("пример: ALM150175 проходит", r["passed"] and r["profile"]["article"] == "ALM150175")
v = K.rafter_check(length_cm=270, angle_deg=90, step_cm=100, snow_region="2", wind_region="Ia", terrain="b", height_m=10)
check("вертикальная стойка: снег и вес стекла не грузят", v["loads"]["S"] == 0 and abs(v["loads"]["q"] - v["loads"]["Wm"] * 0.9) < 1e-5)  # значения округлены до 5 знаков
check("районы арабскими цифрами и «Ia» латиницей", v["inputs"]["snow_region"] == "II" and v["inputs"]["wind_region"] == "Iа")
weak = K.rafter_check(length_cm=430, angle_deg=45, step_cm=115, snow_region="II", wind_region="V", height_m=20,
                      profiles=[{"article": "X", "role": "стойка", "ix_cm4": 300}])
check("нет подходящего профиля: так и сказано", not weak["passed"] and "нет профиля" in weak["verdict"])

# каталог профилей
S.set_profile("TST100", "Стойка тест", "стойка", 100, 520)
cat = {p["article"]: p for p in J(S.list_profiles())}
check("свой профиль в каталоге", cat.get("TST100", {}).get("ix_cm4") == 520 and "ALM150175" in cat)
r2 = J(S.static_calc(4300, 45, 1150, 12, "II", "V", "B", 20, draw=False))
check("подбор: самый лёгкий подходящий (ALM150175 467 < TST100 520)", r2["profile"]["article"] == "ALM150175")

# листы и многостраничный PDF
S.new_drawing()
S.draw_construction("window", {"width": 1800, "height": 1500})
S.make_sheet("A3", True, 0, [-700, -700, 2300, 2000], title="Окно", sheet=1, sheets=2)
r3 = J(S.static_calc(4300, 45, 1150, 12, "II", "V", "B", 20, object_name="Тест", x=5000, y=0, sheet=2, sheets=2))
check("лист расчёта помещается в А4", not r3["sheet"]["overflow"])
pdf = J(S.export_pdf(os.path.join(OUT, "set.pdf"), all_sheets=True))
check("PDF из двух листов", pdf["pages"] == 2 and os.path.getsize(pdf["path"]) > 10000, f"({pdf['pages']} стр.)")
try:
    from pypdf import PdfReader

    sizes = [(round(float(p.mediabox.width) / 72 * 25.4), round(float(p.mediabox.height) / 72 * 25.4)) for p in PdfReader(pdf["path"]).pages]
    check("страницы А3 альбомная и А4 книжная", sizes == [(420, 297), (210, 297)], str(sizes))
except Exception as e:  # noqa: BLE001
    check("чтение PDF", False, str(e))

print("\n" + ("ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ" if not fails else f"ОШИБОК: {fails}"))
sys.exit(1 if fails else 0)
