"""Проверка исправлений по отчёту: цвет слоя, шрифт, высота текста, разнос подписей.
Запуск: python tests/fixes_check.py [ezdxf|com]"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
OUT = os.path.join(os.path.dirname(__file__), "..", "out")
mode = sys.argv[1] if len(sys.argv) > 1 else "ezdxf"
fails = 0


def check(name, ok, info=""):
    global fails
    fails += 0 if ok else 1
    print(f"{'OK  ' if ok else 'FAIL'} {name} {info}")


if mode == "com":
    from autocad_mcp.engine_com import ComEngine, com_call

    eng = ComEngine(OUT)

    @com_call()
    def last_dims(e, n):
        cnt = e.msp.Count
        res = []
        for i in range(cnt - n, cnt):
            ent = e.msp.Item(i)
            if "Dimension" in ent.ObjectName:
                res.append({"h": round(ent.TextHeight), "y": round(ent.TextPosition[1]), "style": ent.TextStyle})
        return res
else:
    from autocad_mcp.engine_ezdxf import EzdxfEngine

    eng = EzdxfEngine(OUT)

    def last_dims(e, n):
        dims = [d for d in e.msp if d.dxftype() == "DIMENSION"][-n:]
        return [{"h": round(d.override().get("dimtxt")), "y": round(d.dxf.defpoint.y),
                 "style": d.override().get("dimtxsty")} for d in dims]

eng.new_drawing()

# 1. Цвет слоя не сбрасывается при рисовании
eng.ensure_layer("WALLS", 1, force=True)
for i in range(5):
    eng.add_line((0, i * 1000), (60000, i * 1000), "WALLS")
color = {l["name"]: l["color"] for l in eng.list_layers()}.get("WALLS")
check("цвет слоя WALLS остался красным (1) после 5 линий", color == 1, f"(цвет={color})")

# 2. Шрифт с кириллицей
font = eng.status()["text_font"]
check("шрифт Standard = Arial", "arial" in str(font).lower(), f"({font})")

# 3. Высота текста по масштабу: склад 60 x 30 м
eng.add_polyline([(0, 0), (60000, 0), (60000, 30000), (0, 30000)], True, "WALLS")
eng.add_linear_dim((0, 0), (60000, 0), -2000, 0)
d = last_dims(eng, 1)[0]
check("высота текста размера на складе ≈ 1000 мм (было 90)", 900 <= d["h"] <= 1100, f"(h={d['h']})")
check("стиль текста размера = Standard (Arial)", str(d["style"]).lower() == "standard", f"({d['style']})")

# 4. Две соседние подписи «100» не наезжают
eng.add_linear_dim((10000, 30000), (10100, 30000), 2000, 0)
eng.add_linear_dim((10100, 30000), (10200, 30000), 2000, 0)
a, b = last_dims(eng, 2)
check("соседние размеры «100» разнесены по высоте", abs(a["y"] - b["y"]) >= 1500, f"(y1={a['y']}, y2={b['y']})")

# 5. Явная высота текста
eng.set_annotation_scale(500)
eng.add_text((30000, 15000), "Склад №1 (тест кириллицы)")
eng.add_linear_dim((0, 0), (0, 30000), -3000, 90)
check("set_annotation_scale(500) применён", last_dims(eng, 1)[0]["h"] == 500, f"(h={last_dims(eng, 1)[0]['h']})")

path = eng.save_drawing(os.path.join(OUT, f"fixes_{mode}_{int(time.time())}." + ("dwg" if mode == "com" else "dxf")))
check("статус сразу после сохранения отвечает", "entities" in eng.status())
print("сохранено:", path)
print("\nВСЕ ПРОВЕРКИ ПРОЙДЕНЫ" if not fails else f"\nПРОВАЛЕНО: {fails}")
sys.exit(1 if fails else 0)
