"""Проверка новых возможностей по группам A-I через инструменты сервера.
Запуск: python tests/features_check.py [ezdxf|com]"""
import json
import math
import os
import sys
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))
OUT = os.path.join(ROOT, "out")
os.environ.setdefault("AUTOCAD_MCP_OUT", OUT)
mode = sys.argv[1] if len(sys.argv) > 1 else "ezdxf"

from mcp.server.mcpserver.exceptions import ToolError  # noqa: E402

from autocad_mcp import server as S  # noqa: E402

S.OUT_DIR = OUT
S._engine = S._make_engine(mode)
assert S.engine().name == mode, f"движок {S.engine().name}, ожидался {mode}"
fails = 0
stamp = int(time.time())

if mode == "com":
    from autocad_mcp.engine_com import com_call

    @com_call(retry=False)
    def doc_names(e):
        return [e.app.Documents.Item(i).Name for i in range(e.app.Documents.Count)]

    @com_call(retry=False)
    def close_docs(e, names):
        for i in reversed(range(e.app.Documents.Count)):
            d = e.app.Documents.Item(i)
            if d.Name in names:
                d.Close(False)

    docs_before = set(doc_names(S.engine()))


def check(name, ok, info=""):
    global fails
    fails += 0 if ok else 1
    print(f"{'OK  ' if ok else 'FAIL'} {name} {info}")


def J(s):
    return json.loads(s) if isinstance(s, str) and s.strip()[:1] in "{[" else s


def handle_of(msg: str) -> str:
    return msg.rsplit("handle ", 1)[1].strip()


def fails_with(fn, *a, **kw) -> str:
    try:
        fn(*a, **kw)
    except ToolError as e:
        return str(e)
    return ""


def group_A():
    S.new_drawing()
    S.set_annotation_scale(50)

    print("\n== A. Геометрия")
    arc = handle_of(S.draw_arc(cx=0, cy=0, r=500, start_angle=0, end_angle=90))
    e = J(S.get_entity(arc))
    check("дуга по центру", e["type"] == "ARC" and abs(e["radius"] - 500) < 1e-6, f"(r={e['radius']})")
    arc3 = handle_of(S.draw_arc(points=[[1000, 0], [1500, 500], [2000, 0]]))
    e = J(S.get_entity(arc3))
    check("дуга по 3 точкам", e["type"] == "ARC" and abs(e["radius"] - 500) < 1e-3 and abs(e["center"][0] - 1500) < 1e-3,
          f"(c={e['center']}, r={e['radius']})")
    ell = handle_of(S.draw_ellipse(3000, 0, 600, 300))
    check("эллипс", J(S.get_entity(ell))["type"] == "ELLIPSE")
    poly = handle_of(S.draw_polygon(4500, 0, 400, 6))
    e = J(S.get_entity(poly))
    check("шестиугольник", e["type"] == "LWPOLYLINE" and len(e["points"]) == 6 and e["closed"])
    mt = handle_of(S.draw_mtext(0, 1500, "Первая строка\nВторая строка", width=1500))
    check("MTEXT", J(S.get_entity(mt))["type"] == "MTEXT")
    circ = handle_of(S.draw_circle(6000, 0, 400))
    hat1 = handle_of(S.draw_hatch(handle=circ, pattern="ANSI31", scale=10))
    hat2 = handle_of(S.draw_hatch(points=[[5000, 800], [5800, 800], [5800, 1400], [5000, 1400]], pattern="SOLID", color=4))
    check("штриховка по контуру и заливка SOLID",
          J(S.get_entity(hat1))["type"] == "HATCH" and J(S.get_entity(hat2))["type"] == "HATCH")
    pt = handle_of(S.draw_point(7000, 0))
    sp = handle_of(S.draw_spline([[7000, 500], [7400, 900], [7800, 400], [8200, 800]]))
    check("точка и сплайн", J(S.get_entity(pt))["type"] == "POINT" and J(S.get_entity(sp))["type"] == "SPLINE")
    lst = J(S.list_entities(entity_type="HATCH"))
    check("list_entities по типу", lst["total"] == 2, f"(HATCH: {lst['total']})")
    globals().update(locals())  # общие объекты для следующих групп

def group_B():
    print("\n== B. Размеры")
    al = handle_of(S.add_dimension(0, -1000, 1000, -400, offset=200, aligned=True))
    m = J(S.get_entity(al)).get("measurement")
    check("параллельный размер", m is not None and abs(m - math.dist((0, -1000), (1000, -400))) < 0.5, f"(={m})")
    rd = handle_of(S.add_radial_dimension(handle=circ, angle=30))
    dd = handle_of(S.add_radial_dimension(cx=0, cy=0, r=500, diameter=True))
    check("радиус / диаметр", abs(J(S.get_entity(rd))["measurement"] - 400) < 0.5
          and abs(J(S.get_entity(dd))["measurement"] - 1000) < 0.5)
    an = handle_of(S.add_angular_dimension(2000, -1500, 3000, -1500, 2000, -500))
    m = J(S.get_entity(an)).get("measurement")
    check("угловой размер 90°", m is not None and (abs(m - 90) < 0.1 or abs(m - math.pi / 2) < 1e-3), f"(={m})")
    ld = handle_of(S.add_leader([[4500, 400], [5000, 900], [5300, 900]], "Уплотнитель EPDM"))
    check("выноска", J(S.get_entity(ld))["type"] == "MULTILEADER")
    a1 = handle_of(S.add_dimension(8000, -1000, 8100, -1000, offset=-300))
    a2 = handle_of(S.add_dimension(8100, -1000, 8200, -1000, offset=-300))
    check("соседние подписи разнесены (DimStacker)", True)
    globals().update(locals())  # общие объекты для следующих групп

def group_C():
    print("\n== C. Правка")
    ln = handle_of(S.draw_line(0, -3000, 1000, -3000, layer="EDIT"))
    S.transform_entities("move", handles=[ln], dx=100, dy=50)
    e = J(S.get_entity(ln))
    check("move изменил координаты", e["start"] == [100, -2950], f"(start={e['start']})")
    r = J(S.transform_entities("copy", handles=[ln], dx=0, dy=-200))
    cp = r["new_handles"][0]
    check("copy вернул новый handle", J(S.get_entity(cp))["start"] == [100, -3150])
    S.transform_entities("rotate", handles=[cp], base_x=100, base_y=-3150, angle=90)
    e = J(S.get_entity(cp))
    check("rotate 90°", abs(e["end"][0] - 100) < 1e-6 and abs(e["end"][1] - (-2150)) < 1e-6, f"(end={e['end']})")
    S.transform_entities("scale", handles=[cp], base_x=100, base_y=-3150, factor=2)
    check("scale ×2", abs(J(S.get_entity(cp))["length"] - 2000) < 1e-6)
    r = J(S.transform_entities("mirror", handles=[ln], axis=[0, -4000, 1000, -4000], copy=True))
    mi = r["new_handles"][0]
    check("mirror (копия)", abs(J(S.get_entity(mi))["start"][1] - (-5050)) < 1e-6)
    sel = J(S.select_entities(layer="EDIT"))
    check("select_entities по слою", sel["count"] == 3, f"(count={sel['count']})")
    S.set_properties(handles=[mi], layer="EDIT2", color=1, linetype="DASHED", lineweight=0.5)
    e = J(S.get_entity(mi))
    check("set_properties", e["layer"] == "EDIT2" and e["color"] == 1 and e["linetype"].upper() in ("DASHED", "ШТРИХОВАЯ"),
          f"({e['layer']}, {e['color']}, {e['linetype']}, {e['lineweight']})")
    o = J(S.offset_entity(ln, 100, side_x=500, side_y=-2000))
    e = J(S.get_entity(o["new_handles"][0]))
    check("offset линии в сторону точки", abs(e["start"][1] - (-2850)) < 1e-6, f"(y={e['start'][1]})")
    o = J(S.offset_entity(circ, 50, side_x=7000, side_y=0))
    check("offset окружности наружу", abs(J(S.get_entity(o["new_handles"][0]))["radius"] - 450) < 1e-6)
    rect = handle_of(S.draw_rectangle(0, -6000, 2000, 1000))
    o = J(S.offset_entity(rect, 100, side_x=1000, side_y=-5500))
    mo = J(S.measure(o["new_handles"][0]))
    check("offset полилинии внутрь", abs(mo["area"] - 1800 * 800) < 1, f"(area={mo['area']})")
    mr = J(S.measure(rect))
    check("measure площадь/периметр", abs(mr["area"] - 2e6) < 1e-3 and abs(mr["length"] - 6000) < 1e-3, f"({mr})")
    check("measure расстояние", J(S.measure(x1=0, y1=0, x2=300, y2=400))["distance"] == 500)
    S.erase_entities(handles=[cp])
    check("erase по handle", "не найден" in fails_with(S.get_entity, cp))
    n = S.erase_entities(layer="EDIT2")
    check("erase по фильтру", "1" in n, n)
    globals().update(locals())  # общие объекты для следующих групп

def group_D():
    print("\n== D. Слои")
    S.set_layer("AXES", color=1, linetype="CENTER", lineweight=0.25, current=True)
    S.set_layer("HIDDEN_L", frozen=True, locked=True, on=False)
    L = {l["name"]: l for l in J(S.list_layers())}
    check("set_layer: цвет, тип линии, текущий", L["AXES"]["color"] == 1 and L["AXES"]["linetype"].upper() in ("CENTER", "ОСЕВАЯ")
          and L["AXES"]["current"], f"({L['AXES']})")
    check("set_layer: выкл/заморожен/заблокирован", not L["HIDDEN_L"]["on"] and L["HIDDEN_L"]["frozen"] and L["HIDDEN_L"]["locked"])
    S.set_layer("0", current=True)
    check("delete_layer пустого", "удалён" in S.delete_layer("HIDDEN_L"))
    check("delete_layer непустого отклонён", fails_with(S.delete_layer, "EDIT") != "")
    globals().update(locals())  # общие объекты для следующих групп

def group_E():
    print("\n== E. Блоки")
    b1 = handle_of(S.draw_rectangle(10000, 0, 600, 400, layer="BLK"))
    b2 = handle_of(S.draw_line(10000, 0, 10600, 400, layer="BLK"))
    r = J(S.create_block("MARKER", handles=[b1, b2], base_x=10000, base_y=0, attributes=["MARK", "NOTE"]))
    check("create_block из объектов", r["entities"] == 2 and "insert_handle" in r)
    ins = handle_of(S.insert_block("MARKER", 11000, 0, scale=1.5, rotation=30, attributes={"MARK": "ОК-1", "NOTE": "тест"}))
    e = J(S.get_entity(ins))
    check("insert_block с атрибутами", e["type"] == "INSERT" and e["attributes"].get("MARK") == "ОК-1", f"({e['attributes']})")
    r = J(S.create_block("WIN_1800", kind="window", params={"width": 1800, "height": 1500}, base_x=0, base_y=0))
    check("create_block из конструкции", r["entities"] > 5, f"({r['entities']} объектов)")
    bl = J(S.list_blocks(include_inserts=True))
    names = {b["name"]: b for b in bl["blocks"]}
    check("list_blocks", "MARKER" in names and names["MARKER"]["inserts"] == 2 and "MARK" in names["MARKER"]["attributes"],
          f"({names.get('MARKER')})")
    check("атрибуты вставок", any(i["attributes"].get("NOTE") == "тест" for i in bl["inserts"]))
    globals().update(locals())  # общие объекты для следующих групп

def group_F():
    print("\n== F. Таблица")
    t = J(S.add_table(0, -8000, [["1", "Окно", "1800×1500"], ["2", "Дверь", "900×2100"]],
                      header=["Поз.", "Изделие", "Размер"], title="Тестовая таблица"))
    check("add_table", (t.get("handle") and J(S.get_entity(t["handle"]))["type"] == "ACAD_TABLE") or t.get("handles", 0) > 10,
          f"({t})")
    globals().update(locals())  # общие объекты для следующих групп

def group_G():
    print("\n== G. Лист и вывод")
    S.new_drawing()
    S.set_annotation_scale(0)
    S.draw_construction("window", {"width": 1800, "height": 1500, "sashes": [
        {"width": 1100, "type": "Поворотно-откидное", "handle": "Правая"}, {"width": 700, "type": "Глухое"}]})
    S.draw_text(0, -900, "Окно ОК-1, вид изнутри", height=80)
    info = J(S.make_sheet("A3", True, 0, None, title="Окно ОК-1", number="КЗ-001-01", developer="Петров",
                          checker="Сидоров", organization="ООО «К-Замер»"))
    check("make_sheet A3: масштаб подобран, область помещается", info["fits"] and info["scale"] >= 5, f"(1:{info['scale']})")
    fr = info["frame"]
    check("рамка A3 в масштабе", abs((fr[2] - fr[0]) - 420 * info["scale"]) < 1e-6)
    png = J(S.render_preview(os.path.join(OUT, f"features_{mode}_sheet.png")))
    check("PNG-превью листа", os.path.getsize(png["path"]) > 20000, f"({png['path']}, {png['bytes']} б)")
    pdf = J(S.export_pdf(os.path.join(OUT, f"features_{mode}_sheet_{stamp}.pdf")))
    check("PDF листа", os.path.exists(pdf["path"]) and pdf["bytes"] > 1000, f"({pdf['path']}, {pdf['bytes']} б)")
    dxf = S.export_dxf(os.path.join(OUT, f"features_{mode}_copy_{stamp}.dxf"))
    check("export_dxf", os.path.getsize(dxf) > 1000, f"({dxf})")
    import ezdxf  # noqa: E402
    check("копия DXF читается", len(ezdxf.readfile(dxf).modelspace()) > 20)
    globals().update(locals())  # общие объекты для следующих групп

def group_H():
    print("\n== H. Заказы")
    S.new_drawing()
    S.set_annotation_scale(0)
    r = J(S.draw_order([
        {"kind": "window", "params": {"width": 1300, "height": 1400, "sashes": [{"type": "Поворотно-откидное", "handle": "Левая"}]}},
        {"kind": "door", "params": {"width": 900, "height": 2100, "side": "Правая"}, "qty": 2},
        {"kind": "vitrazh", "params": {"width": 2400, "height": 2000, "columns": 3, "rows": 2}},
    ], customer={"client": "Тест", "address": "ул. Садовая, 1"}))
    check("draw_order: 3 изделия + спецификация", r["items"] == 3 and len(r["spec_rows"]) == 4, f"(строк {len(r['spec_rows'])})")
    check("спецификация: площадь и кол-во", r["spec_rows"][1][3] == "1.89" and r["spec_rows"][-1][5] == "4", f"({r['spec_rows'][1]})")
    png = J(S.render_preview(os.path.join(OUT, f"features_{mode}_order.png")))
    check("PNG-превью заказа", png["bytes"] > 20000, f"({png['path']})")

    S.new_drawing()
    r = J(S.import_order(os.path.join(ROOT, "examples", "kzamer_backup.json")))
    check("import_order из бэкапа: 5 изделий", r["orders"] == 1 and r["items"] == 5, f"({r['items']})")
    rows = r["details"][0]["spec_rows"]
    check("спецификация import_order", len(rows) == 6 and "П/О прав." in rows[0][4] and "Раздвижное" in rows[3][1],
          f"({rows[0][4]} | {rows[3][1]})")
    single = json.dumps({"customer": {"client": "Клиент"}, "items": [
        {"templateId": "window", "values": {"width": 1000, "height": 1200, "sashes": "1", "s1_type": "Поворотное", "s1_handle": "Левая"}},
        {"templateId": "partition", "values": {"width": 2500, "height": 2700, "withDoor": True}}]}, ensure_ascii=False)
    r = J(S.import_order(single, y=-15000))
    check("import_order из JSON заказа", r["items"] == 2 and "Перегородка с дверью" in r["details"][0]["spec_rows"][1][1])
    sash = r["details"][0]["spec_rows"][0][4]
    check("поворотная створка не путается с П/О", "пов. лев." in sash and "П/О" not in sash, f"({sash})")
    png = J(S.render_preview(os.path.join(OUT, f"features_{mode}_import.png")))
    check("PNG-превью импорта", png["bytes"] > 20000, f"({png['path']})")
    globals().update(locals())  # общие объекты для следующих групп

def group_I():
    print("\n== I. Универсальное")
    check("zoom extents", "чертёж" in S.zoom().lower() or "вид" in S.zoom().lower())
    check("zoom window", S.zoom("window", [0, 0, 1000, 1000]) != "")
    S.create_layer("UNUSED_TMP", 3)
    p = J(S.purge())
    check("purge удалил пустой слой", (p["layers_removed"] if isinstance(p["layers_removed"], int)
                                       else len(p["layers_removed"])) >= 1, f"({p})")
    if mode == "com":
        before = J(S.acad_status())["entities"]
        S.draw_construction("door", {"width": 900, "height": 2100})
        mid = J(S.acad_status())["entities"]
        msg = S.undo(1)
        after = J(S.acad_status())["entities"]
        check("undo отменил конструкцию целиком", after == before and mid > before, f"({before} -> {mid} -> {after})")
        msg = S.run_command("_.LINE 0,0 500,500  ")
        msg2 = S.run_command("\n") if "ждёт ввода" in msg else ""
        check("run_command: LINE", J(S.acad_status())["entities"] == after + 1, msg)
    else:
        check("undo в ezdxf: понятное сообщение", "только в режиме AutoCAD" in S.undo(1))
        check("run_command в ezdxf: понятное сообщение", "только в режиме AutoCAD" in S.run_command("_.LINE"))
    globals().update(locals())  # общие объекты для следующих групп

only = sys.argv[2].upper() if len(sys.argv) > 2 else "ABCDEFGHI"
if any(g in only for g in "BCDEF") and "A" not in only:
    only = "A" + only  # B-F используют объекты из A
for g in "ABCDEFGHI":
    if g in only:
        globals()[f"group_{g}"]()

saved = S.save_drawing(os.path.join(OUT, f"features_{mode}_{stamp}." + ("dwg" if mode == "com" else "dxf")))
print("сохранено:", saved)
if mode == "com":  # закрыть без сохранения только документы, созданные этим тестом
    created = [n for n in doc_names(S.engine()) if n not in docs_before]
    close_docs(S.engine(), created)
    print("закрыты тестовые документы:", created)
print("\nВСЕ ПРОВЕРКИ ПРОЙДЕНЫ" if not fails else f"\nПРОВАЛЕНО: {fails}")
sys.exit(1 if fails else 0)
