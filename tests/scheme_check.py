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

# фрамуга на верхних петлях: вершина снизу; петли задают только ось, внутрь/наружу по направлению изделия
def tr_of(values):
    ops = C.build("window", O.kzamer_item("window", {**base, **values})["params"])
    return [o for o in ops if o["op"] == "line" and o["layer"] == C.L_OPEN and o["p1"][1] > 1900]


tr_lines = tr_of({"transomType": C.TILT_TOP})
check("петли сверху: вершина снизу", bool(tr_lines) and all(abs(o["p1"][1] - 1920) < 1 for o in tr_lines), f"{[o['p1'] for o in tr_lines]}")
check("петли сверху, изделие внутрь: сплошная изнутри", not any(o.get("dashed") for o in tr_lines))
check("петли сверху, изделие внутрь: пунктир снаружи", all(o.get("dashed") for o in tr_of({"transomType": C.TILT_TOP, "view": "Снаружи"})))
check("петли сверху, изделие наружу: пунктир изнутри", all(o.get("dashed") for o in tr_of({"transomType": C.TILT_TOP, "direction": "Наружу"})))
check("старое «Откидная наружу» = петли сверху", tr_of({"transomType": "Откидная наружу"}) == tr_lines)
check("символы по петлям", [C.transom_symbol(t) for t in ("Глухая", C.TILT_BOTTOM, C.TILT_TOP)] == ["Глухое", "Откидное", "Подвесное"])
fr_top = {"split": {"dir": "h", "sizes": [500, 1500], "parts": [{"fill": {"t": "tophung"}}, {"fill": {"t": "glass"}}]}}
top_item = O.kzamer_item("construct", {"width": 1800, "height": 2000, "frame": json.dumps(fr_top)})
top_ops = C.build(top_item["kind"], top_item["params"])
def slide_heads(d):
    item = O.kzamer_item("construct", {"width": 1200, "height": 1500, "frame": json.dumps({"fill": {"t": "slide", "dir": d}})})
    return sum(1 for o in C.build(item["kind"], item["params"]) if o["op"] == "line" and o["layer"] == C.L_OPEN)


# импост в створке: одна дверь (один символ, одна ручка), перемычка в слое створки
door_bar = {"split": {"dir": "h", "sizes": [1050, 1050], "parts": [{"fill": {"t": "glass"}}, {"fill": {"t": "panel"}}], "sash": {"t": "door", "hinge": "left"}}}
db_item = O.kzamer_item("construct", {"width": 900, "height": 2100, "frame": json.dumps(door_bar)})
db_ops = C.build(db_item["kind"], db_item["params"])
check("импост в створке: один символ двери", sum(1 for o in db_ops if o["op"] == "line" and o["layer"] == C.L_OPEN) == 2)
check("импост в створке: перемычка в слое створки", any(o["op"] == "line" and o["layer"] == C.L_SASH and abs(o["p1"][1] - 1050) < 1 for o in db_ops))
check("импост в створке: спецификация одним полем", C.frame_short(door_bar, 900, 2100) == "1 пол.: дверь п.лев. с имп.", C.frame_short(door_bar, 900, 2100))
# стена под окном балконного блока: рама обходит стену, площадь без стены
bb = {"split": {"dir": "v", "sizes": [700, 1400], "parts": [
    {"split": {"dir": "h", "sizes": [1450, 700], "parts": [{"fill": {"t": "glass"}}, {"fill": {"t": "panel"}}], "sash": {"t": "tiltturn", "hinge": "left"}}},
    {"split": {"dir": "h", "sizes": [1450, 700], "parts": [{"fill": {"t": "tiltturn", "hinge": "left"}}, {"fill": {"t": "wall"}}]}}]}}
bb_item = O.kzamer_item("construct", {"width": 2100, "height": 2150, "frame": json.dumps(bb)})
bb_ops = C.build(bb_item["kind"], bb_item["params"])
floor = [o for o in bb_ops if o["op"] == "line" and o["layer"] == C.L_FRAME and abs(o["p1"][1]) < 1 and abs(o["p2"][1]) < 1]
check("балконный блок: по полу рама только у двери", bool(floor) and all(max(o["p1"][0], o["p2"][0]) <= 700.5 for o in floor), f"{[(o['p1'], o['p2']) for o in floor]}")
check("балконный блок: площадь без стены", abs(O._area(bb_item) - (2100 * 2150 - 1400 * 700) / 1e6) < 1e-6, f"{O._area(bb_item)}")
check("раздвижная в обе стороны: два наконечника", [slide_heads("left"), slide_heads("both")] == [3, 5], f"{[slide_heads('left'), slide_heads('both')]}")
check("конструктор: петли сверху при изделии внутрь сплошные", not any(o.get("dashed") for o in top_ops if o["op"] == "line" and o["layer"] == C.L_OPEN))
bal = O.kzamer_item("balcony", {"blockWidth": 2100, "blockHeight": 2150, "doorWidth": 700, "doorOpening": "Слева",
                                "transomTop": 600, "transomType": "Глухая"})
bops = C.build("balcony", bal["params"])
check("балкон: фрамуга по замеру (импост на 1550)", any(o["op"] == "line" and o["layer"] == C.L_MULLION and o["p1"][1] == o["p2"][1] == 1550 for o in bops))
check("балкон: спецификация с фрамугой", "фрамуга 600, глухая" in O.describe("balcony", bal["params"]), O.describe("balcony", bal["params"]))

# конструктор «К-Замер»: окно с фрамугой 1800 x 2000, П/О 1100 петли слева + глухое 700
frame = {"split": {"dir": "h", "sizes": [500, 1500], "parts": [
    {"fill": {"t": "tilt"}},
    {"split": {"dir": "v", "sizes": [1100, 700], "parts": [{"fill": {"t": "tiltturn", "hinge": "left"}}, {"fill": {"t": "glass"}}]}}]}}
cons = O.kzamer_item("construct", {"width": 1800, "height": 2000, "frame": json.dumps(frame), "frameName": "Окно с фрамугой"})
check("конструктор: вид frame и название", cons["kind"] == "frame" and cons["name"] == "Окно с фрамугой")
fops = C.build("frame", cons["params"])
mull = [o for o in fops if o["op"] == "line" and o["layer"] == C.L_MULLION]
check("конструктор: импосты на 1500 (гориз.) и x=1100", any(o["p1"][1] == o["p2"][1] == 1500 for o in mull) and any(o["p1"][0] == o["p2"][0] == 1100 for o in mull))
turn = [o for o in fops if o["op"] == "line" and o["layer"] == C.L_OPEN and abs(o["p1"][1] - 780) < 1]  # середина створки: рама 60 + отступ 20
check("конструктор: вершина П/О у ручки справа", bool(turn) and all(o["p1"][0] > 1000 for o in turn), f"{[o['p1'] for o in turn]}")
check("конструктор: спецификация", O.describe("frame", cons["params"]) == "3 пол.: отк. п.низ, П/О п.лев., глух.", O.describe("frame", cons["params"]))
cons_out = O.kzamer_item("construct", {"width": 1800, "height": 2000, "frame": json.dumps(frame), "view": "Снаружи"})
turn_o = [o for o in C.build("frame", cons_out["params"]) if o["op"] == "line" and o["layer"] == C.L_OPEN and abs(o["p1"][1] - 780) < 1]  # середина створки: рама 60 + отступ 20
check("конструктор, вид снаружи: зеркало и пунктир", bool(turn_o) and all(o["p1"][0] < 800 and o.get("dashed") for o in turn_o))
door = {"split": {"dir": "v", "sizes": [700, 700], "shtulp": True, "parts": [{"fill": {"t": "door", "hinge": "left"}}, {"fill": {"t": "door", "hinge": "right"}}]}}
dops = C.build("frame", O.kzamer_item("construct", {"width": 1400, "height": 2100, "frame": json.dumps(door), "direction": "Наружу"})["params"])
check("штульп: пунктир по центру, двери наружу пунктиром", any(o["op"] == "line" and o.get("dashed") and o["layer"] == C.L_SASH for o in dops)
      and all(o.get("dashed") for o in dops if o["op"] == "line" and o["layer"] == C.L_OPEN))

S.new_drawing()
r = json.loads(S.import_order(json.dumps({"customer": {"client": "Тест"}, "items": [
    {"templateId": "window", "values": base}, {"templateId": "window", "values": {**base, "view": "Снаружи"}},
    {"templateId": "door", "values": {"width": 1000, "height": 2700, "side": "Справа", "transomTop": 500}},
    {"templateId": "construct", "values": {"width": 1800, "height": 2000, "frame": json.dumps(frame), "frameName": "Окно с фрамугой"}},
    {"templateId": "construct", "values": {"width": 1400, "height": 2100, "frame": json.dumps(door), "frameName": "Дверь штульповая"}}]}, ensure_ascii=False)))
check("import_order: 5 изделий, из них 2 из конструктора", r["items"] == 5)
png = json.loads(S.render_preview(os.path.join(sys.argv[1], "scheme_import.png")))
print("png:", png["path"])
print("\n" + ("ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ" if not fails else f"ОШИБОК: {fails}"))
sys.exit(1 if fails else 0)
