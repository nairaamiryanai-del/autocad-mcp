"""Воспроизведение бага «после первой команды ошибка»: MCP-сервер вызывает
инструменты из разных потоков. Движок создаётся в одном потоке, команды идут из других."""
import os
import sys
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from autocad_mcp.engine_com import ComEngine  # noqa: E402

eng = ComEngine(os.path.join(os.path.dirname(__file__), "..", "out"))
print("подключено:", eng.status()["version"])
eng.new_drawing()

errors: list[str] = []


def worker(i: int) -> None:
    try:
        eng.add_line((i * 100, 0), (i * 100, 500), "0")
        eng.add_circle((i * 100, 700), 40, "0")
    except Exception as e:  # noqa: BLE001
        errors.append(f"поток {i}: {type(e).__name__}: {e}")


threads = [threading.Thread(target=worker, args=(i,)) for i in range(6)]
for t in threads:
    t.start()
for t in threads:
    t.join()

print("ошибок:", len(errors))
for e in errors[:3]:
    print("  ", e[:160])
print("объектов в чертеже:", eng.status()["entities"])
print("ИТОГ:", "OK" if not errors else "ПАДАЕТ")
