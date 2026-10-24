"""MCP: импорт замеров «К-Замер» с петлями, фрамугами и видом снаружи (ezdxf).
Запуск: .venv\\Scripts\\python tests\\scheme_check.py"""
import json
import os
import sys

OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "out")
sys.argv[1:] = [OUT]
os.environ["AUTOCAD_MCP_ENGINE"] = "ezdxf"
os.environ["AUTOCAD_MCP_OUT"] = OUT
from autocad_mcp import constructions as C  # noqa: E402
from autocad_mcp import orders as O  # noqa: E402
from autocad_mcp import server as S  # noqa: E402

fails = 0


def check(name, ok, info=""):
    global fails
    print(("OK   " if ok else "FAIL ") + name + (f" {info}" if info else ""))
    fails += 0 if ok else 1


base = {"width": 1800, "height": 2400, "sashes": "2", "s1_type": "Поворотно-откидное", "s1_w": 1100, "s1_hinge": "Слева",
        "s2_type": "Глухое", "s2_w": 700, "transomTop": 500, "transomType": "Откидная", "bottomPart": 400}
inside = O.kzamer_item("window", base)
check("петли слева -> ручка справа", inside["params"]["sashes"][0].get("hinge") == "Слева")
check("импосты переданы", inside["params"]["transom_top"] == 500 and inside["params"]["bottom_part"] == 400)
ops = C.build("window", inside["params"])
mull = [o for o in ops if o["op"] == "line" and o["layer"] == C.L_MULLION]
check("горизонтальные импосты на 1900 и 400", {round(o["p1"][1]) for o in mull if o["p1"][1] == o["p2"][1]} == {1900, 400})
opens = [o for o in ops if o["op"] == "line" and o["layer"] == C.L_OPEN]
# вершина поворота: общая точка двух линий створки на середине высоты, справа (ручка справа)
turn = [o for o in opens if abs(o["p1"][1] - (400 + 1900) / 2) < 1]
check("вершина поворота справа (у ручки)", bool(turn) and all(o["p1"][0] > 900 for o in turn), f"{[o['p1'] for o in turn]}")
check("внутрь, вид изнутри: сплошные", not any(o.get("dashed") for o in opens))

outside = O.kzamer_item("window", {**base, "view": "Снаружи"})
ops_o = C.build("window", outside["params"])
turn_o = [o for o in ops_o if o["op"] == "line" and o["layer"] == C.L_OPEN and abs(o["p1"][1] - 1150) < 1]
check("вид снаружи: вершина слева (зеркало)", bool(turn_o) and all(o["p1"][0] < 900 for o in turn_o), f"{[o['p1'] for o in turn_o]}")
check("вид снаружи: открывание внутрь пунктиром", all(o.get("dashed") for o in ops_o if o["op"] == "line" and o["layer"] == C.L_OPEN))
vd = [o for o in ops_o if o["op"] == "dim" and o.get("angle") == 90]
check("вертикальные размеры не зеркалятся", sorted(o["p1"][0] for o in vd) == [0, 1800, 1800])

legacy = O.kzamer_item("window", {**{k: v for k, v in base.items() if k != "s1_hinge"}, "s1_handle": "Правая"})
check("старый замер с ручкой читается", legacy["params"]["sashes"][0].get("handle") == "Правая")
desc = O.describe("window", inside["params"])
check("спецификация: петли и фрамуга", "П/О петли лев." in desc and "фрамуга 500, откидная" in desc and "низ 400" in desc, f"({desc})")
check("спецификация: вид снаружи", "вид снаружи" in O.describe("window", outside["params"]))
door = O.kzamer_item("door", {"width": 1000, "height": 2700, "side": "Справа", "transomTop": 500})
check("дверь: петли справа", "петли прав." in O.describe("door", door["params"]))
C.build("door", door["params"])

# фрамуга наружу (вокруг верхней оси): вершина снизу, пунктир изнутри и сплошная снаружи
out_in = C.build("window", O.kzamer_item("window", {**base, "transomType": "Откидная наружу"})["params"])
tr_lines = [o for o in out_in if o["op"] == "line" and o["layer"] == C.L_OPEN and o["p1"][1] > 1900]
check("фрамуга наружу: вершина снизу", bool(tr_lines) and all(abs(o["p1"][1] - 1920) < 1 for o in tr_lines), f"{[o['p1'] for o in tr_lines]}")
check("фрамуга наружу: пунктир при виде изнутри", all(o.get("dashed") for o in tr_lines))
out_out = C.build("window", O.kzamer_item("window", {**base, "transomType": "Откидная наружу", "view": "Снаружи"})["params"])
tr_out = [o for o in out_out if o["op"] == "line" and o["layer"] == C.L_OPEN and o["p1"][1] > 1900]
check("фрамуга наружу: сплошная при виде снаружи", bool(tr_out) and not any(o.get("dashed") for o in tr_out))
bal = O.kzamer_item("balcony", {"blockWidth": 2100, "blockHeight": 2150, "doorWidth": 700, "doorOpening": "Слева",
                                "transomTop": 600, "transomType": "Глухая"})
bops = C.build("balcony", bal["params"])
check("балкон: фрамуга по замеру (импост на 1550)", any(o["op"] == "line" and o["layer"] == C.L_MULLION and o["p1"][1] == o["p2"][1] == 1550 for o in bops))
check("балкон: спецификация с фрамугой", "фрамуга 600, глухая" in O.describe("balcony", bal["params"]), O.describe("balcony", bal["params"]))

S.new_drawing()
r = json.loads(S.import_order(json.dumps({"customer": {"client": "Тест"}, "items": [
    {"templateId": "window", "values": base}, {"templateId": "window", "values": {**base, "view": "Снаружи"}},
    {"templateId": "door", "values": {"width": 1000, "height": 2700, "side": "Справа", "transomTop": 500}}]}, ensure_ascii=False)))
check("import_order: 3 изделия", r["items"] == 3)
png = json.loads(S.render_preview(os.path.join(sys.argv[1], "scheme_import.png")))
print("png:", png["path"])
print("\n" + ("ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ" if not fails else f"ОШИБОК: {fails}"))
sys.exit(1 if fails else 0)
