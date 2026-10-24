"""Дымовой тест: строим конструкции через ezdxf-движок, сохраняем, перечитываем."""
import os, sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import ezdxf
from autocad_mcp import constructions as C
from autocad_mcp.engine_ezdxf import EzdxfEngine

OUT = os.path.join(os.path.dirname(__file__), "..", "out")
os.makedirs(OUT, exist_ok=True)

eng = EzdxfEngine(OUT)

# окно: 1800x1500, две створки 1100 (пов.-откид., ручка правая) + 700 (глухое)
win = C.build("window", {
    "width": 1800, "height": 1500, "profile": 60, "outward": False,
    "sashes": [
        {"width": 1100, "type": "Поворотно-откидное", "handle": "Правая"},
        {"width": 700, "type": "Глухое"},
    ],
})
eng.render_ops(win)
p1 = eng.save_drawing(os.path.join(OUT, "window.dxf"))
print("window ops:", len(win), "->", p1)

# дверь штульповая наружу
eng.new_drawing()
door = C.build("door", {"width": 1400, "height": 2100, "door_type": "Штульповая", "outward": True})
eng.render_ops(door)
p2 = eng.save_drawing(os.path.join(OUT, "door.dxf"))
print("door ops:", len(door), "->", p2)

# витраж 4x2
eng.new_drawing()
vit = C.build("vitrazh", {"width": 3000, "height": 2700, "columns": 4, "rows": 2})
eng.render_ops(vit)
p3 = eng.save_drawing(os.path.join(OUT, "vitrazh.dxf"))
print("vitrazh ops:", len(vit), "->", p3)

# общее черчение
eng.new_drawing()
eng.add_line((0, 0), (1000, 0), "0")
eng.add_circle((500, 500), 250, "0")
eng.add_text((500, 900), "TEST", 120, C.L_TEXT)
eng.add_linear_dim((0, 0), (1000, 0), -300, 0)
p4 = eng.save_drawing(os.path.join(OUT, "general.dxf"))
print("general summary:", eng.summary())

# верификация: перечитываем каждый файл
for p in (p1, p2, p3, p4):
    doc = ezdxf.readfile(p)
    n = len(list(doc.modelspace()))
    assert n > 0, f"пусто: {p}"
    print("  reread OK:", os.path.basename(p), "entities:", n)

print("\nALL OK")
