"""Проверка универсальных возможностей: пакетное рисование, массивы, цепочки размеров, стены, оси,
сохранение для старых версий, запуск из командной строки и HTTP с ключом.

Запуск (PYTHONIOENCODING=utf-8):
  .venv\\Scripts\\python tests\\universal_check.py          ezdxf + CLI + HTTP
  .venv\\Scripts\\python tests\\universal_check.py com      инструменты в живом AutoCAD
"""

import asyncio
import json
import os
import socket
import subprocess
import sys
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
OUT = os.path.join(ROOT, "out")
MODE = sys.argv[1] if len(sys.argv) > 1 else "ezdxf"
os.environ["AUTOCAD_MCP_ENGINE"] = MODE
os.environ["AUTOCAD_MCP_OUT"] = OUT
sys.path.insert(0, os.path.join(ROOT, "src"))

from autocad_mcp import server as S  # noqa: E402
from autocad_mcp.common import version_key  # noqa: E402

fails = 0


def check(name, ok, info=""):
    global fails
    print(("OK   " if ok else "FAIL ") + name + (f" {info}" if info else ""))
    if not ok:
        fails += 1


def J(s):
    return json.loads(s)


def tools_part():
    print(f"\n== Инструменты ({MODE})")
    S.new_drawing()
    S.set_annotation_scale(100)

    r = J(S.draw_batch([
        {"type": "line", "x1": 0, "y1": 0, "x2": 1000, "y2": 0},
        {"type": "polyline", "points": [[0, 100], [500, 300], [1000, 100]]},
        {"type": "rect", "x": 0, "y": 400, "w": 600, "h": 300, "layer": "BOX", "color": 1},
        {"type": "circle", "cx": 1500, "cy": 500, "r": 200},
        {"type": "arc", "cx": 1500, "cy": 500, "r": 300, "start_angle": 0, "end_angle": 180},
        {"type": "ellipse", "cx": 2200, "cy": 500, "rx": 300, "ry": 150},
        {"type": "polygon", "cx": 2900, "cy": 500, "r": 200, "sides": 6},
        {"type": "point", "x": 3300, "y": 500},
        {"type": "text", "x": 0, "y": 900, "text": "Пакет: всё одной командой", "height": 80},
        {"type": "mtext", "x": 0, "y": 1200, "text": "Строка 1\nСтрока 2", "height": 60},
        {"type": "dim", "x1": 0, "y1": 0, "x2": 1000, "y2": 0, "offset": -300},
        {"type": "hatch", "points": [[0, 400], [600, 400], [600, 700], [0, 700]], "pattern": "ANSI31", "scale": 20},
        {"type": "spline", "points": [[3500, 0], [3700, 300], [3900, 0]]},
        {"type": "что-то", "x": 1},
        {"type": "circle", "cx": 0},
    ]))
    check("draw_batch: 13 объектов, 2 ошибки с номерами", r["created"] == 13 and [e["index"] for e in r.get("errors", [])] == [13, 14],
          f"({r['created']}, {r.get('errors')})")
    red = J(S.list_entities(layer="BOX"))
    check("draw_batch: цвет и слой элемента", red["total"] == 1 and red["entities"][0]["color"] == 1, f"({red['entities'][:1]})")

    S.new_drawing()
    S.set_annotation_scale(100)
    h = S.draw_rectangle(0, 0, 400, 200).rsplit("handle ", 1)[1].strip()
    r = J(S.array_entities("rect", handles=[h], rows=3, cols=4, dx=600, dy=400))
    check("массив 3×4: 11 копий", r["new_entities"] == 11)
    c = S.draw_circle(5000, 0, 100).rsplit("handle ", 1)[1].strip()
    r = J(S.array_entities("polar", handles=[c], count=8, cx=4000, cy=0, angle=360))
    check("круговой массив: 7 копий", r["new_entities"] == 7)
    last = J(S.get_entity(r["new_handles"][1]))  # вторая копия: 90° от исходной
    check("круговой массив: копия на 90°", abs(last["center"][0] - 4000) < 0.01 and abs(last["center"][1] - 1000) < 0.01,
          f"({last.get('center')})")
    t = S.draw_rectangle(7000, -50, 200, 100).rsplit("handle ", 1)[1].strip()
    r = J(S.array_entities("polar", handles=[t], count=4, cx=6000, cy=0, angle=360, rotate_items=False))
    box = J(S.get_entity(r["new_handles"][0]))["bbox"]
    check("массив без поворота: копия не повёрнута", abs((box[2] - box[0]) - 200) < 0.5 and abs((box[3] - box[1]) - 100) < 0.5,
          f"({box})")

    S.new_drawing()
    S.set_annotation_scale(150)
    r = J(S.add_dimension_chain([[0, 0], [1200, 0], [3000, 0], [3900, 0]], "horizontal", at=-600))
    check("цепочка размеров: 3 + общий", r["dimensions"] == 4)
    dims = J(S.list_entities(entity_type="DIMENSION"))
    check("цепочка в чертеже", dims["total"] == 4)

    S.new_drawing()
    S.set_annotation_scale(250)
    r = J(S.draw_axes([6000, 6000, 4500], [6000, 7200], text_height=350))
    check("оси: 1-4 и А-В", r["axes_x"] == ["1", "2", "3", "4"] and r["axes_y"] == ["А", "Б", "В"], f"({r})")
    lay = {l["name"]: l for l in J(S.list_layers())}
    lt = str(lay.get("AXES", {}).get("linetype", "")).upper()
    check("оси штрихпунктиром", lt in ("CENTER", "ОСЕВАЯ"), f"({lt})")
    ax = J(S.list_entities(layer="AXES"))["entities"][0]["handle"]
    if MODE == "ezdxf":
        check("масштаб штрихпунктира 1:100", abs(S.engine().doc.entitydb[ax].dxf.ltscale - 100) < 0.01)
    # по X: 3 шага + общий, по Y: 2 шага + общий
    check("размеры между осями", J(S.list_entities(entity_type="DIMENSION"))["total"] == 3 + 1 + 2 + 1,
          f"({J(S.list_entities(entity_type='DIMENSION'))['total']})")
    r = J(S.draw_walls([[0, 0], [16500, 0], [16500, 13200], [0, 13200]], 380, closed=True, openings=[
        {"segment": 0, "offset": 2000, "width": 1500, "type": "window"},
        {"segment": 0, "offset": 8000, "width": 1000, "type": "door", "swing": "left", "hinge": "start"},
        {"segment": 1, "offset": 5000, "width": 1800, "type": "window"},
    ]))
    check("стены: 4 участка, 3 проёма", r["walls_segments"] == 4 and r["openings"] == 3, f"({r})")
    arcs = J(S.list_entities(layer="DOORS", entity_type="ARC"))
    check("дверь с дугой открывания", arcs["total"] == 1)
    a = J(S.get_entity(arcs["entities"][0]["handle"]))
    sweep = (a["end_angle"] - a["start_angle"]) % 360
    check("дуга двери 90°", abs(sweep - 90) < 0.01, f"({a['start_angle']}..{a['end_angle']})")
    win = J(S.list_entities(layer="WINDOWS"))
    check("окна: по 3 линии", win["total"] == 6)
    try:
        S.draw_walls([[0, 0], [1000, 0]], 200, openings=[{"segment": 0, "offset": 500, "width": 900}])
        check("проём длиннее стены отклоняется", False)
    except Exception as e:  # noqa: BLE001
        check("проём длиннее стены отклоняется", "не помещается" in str(e), f"({e})")

    S.render_preview(os.path.join(OUT, f"universal_{MODE}_plan.png"))
    ext = "dwg" if MODE == "com" else "dxf"
    p = S.save_drawing(os.path.join(OUT, f"universal_{MODE}_2010.{ext}"), "2010")
    check("сохранение в формате 2010", os.path.exists(p.strip('"')), f"({p})")
    if MODE == "ezdxf":
        import ezdxf

        check("версия файла AC1024 (2010)", ezdxf.readfile(p.strip('"')).dxfversion == "AC1024")


def unit_part():
    print("\n== Версии и форматы")
    check("2016 -> формат 2013", version_key("2016") == "2013")
    check("2027 -> формат 2018", version_key(2027) == "2018")
    check("R2010 -> 2010", version_key("R2010") == "2010")
    from autocad_mcp.acad_versions import product_name

    check("25.1 -> AutoCAD 2026", product_name("25.1s (LMS Tech)") == "AutoCAD 2026 (R25.1)")
    if sys.platform == "win32":
        from autocad_mcp import acad_versions as A

        progs = A.installed_progids()
        check("установленные версии без «хвостов» удалённых", all(A._exe_of(p) for p in progs), f"({progs})")


def cli_part():
    print("\n== Командная строка")
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    r = subprocess.run([sys.executable, "-m", "autocad_mcp", "--print-config", "--engine", "ezdxf"],
                       capture_output=True, text=True, encoding="utf-8", env=env, cwd=ROOT)
    cfg = json.loads(r.stdout)["mcpServers"]["autocad"]
    check("--print-config: путь к python и режим", cfg["command"] == sys.executable and cfg["env"]["AUTOCAD_MCP_ENGINE"] == "ezdxf")
    r = subprocess.run([sys.executable, "-m", "autocad_mcp", "--check", "--engine", "ezdxf", "--out", OUT],
                       capture_output=True, text=True, encoding="utf-8", env=env, cwd=ROOT)
    check("--check в режиме ezdxf", r.returncode == 0 and "Готово" in r.stdout, f"({r.stdout.strip().splitlines()[-1:]})")
    r = subprocess.run([sys.executable, "-m", "autocad_mcp", "--http", "--host", "0.0.0.0"],
                       capture_output=True, text=True, encoding="utf-8", env=env, cwd=ROOT, timeout=60)
    check("сеть без ключа: отказ запуска", r.returncode != 0 and "без ключа" in (r.stderr + r.stdout))


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


async def _http_session(url, token):
    from mcp import ClientSession
    from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

    headers = {"Authorization": f"Bearer {token}"} if token else None
    async with create_mcp_http_client(headers=headers) as client:
        async with streamable_http_client(url, http_client=client) as streams:
            async with ClientSession(streams[0], streams[1]) as session:
                init = await session.initialize()
                tools = await session.list_tools()
                res = await session.call_tool("draw_batch", {"items": [{"type": "line", "x1": 0, "y1": 0, "x2": 10, "y2": 0}]})
                text = " ".join(getattr(c, "text", "") for c in res.content)
                return init, len(tools.tools), text


def http_part():
    print("\n== HTTP (любой клиент MCP)")
    import urllib.error
    import urllib.request

    port = _free_port()
    token = "test-key-123"
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.Popen([sys.executable, "-m", "autocad_mcp", "--http", "--port", str(port), "--engine", "ezdxf",
                             "--token", token, "--out", OUT], cwd=ROOT, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    url = f"http://127.0.0.1:{port}/mcp"
    try:
        for _ in range(100):
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
                break
            except OSError:
                time.sleep(0.2)
        req = urllib.request.Request(url, data=b"{}", method="POST", headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req, timeout=10)
            code = 200
        except urllib.error.HTTPError as e:
            code = e.code
        check("без ключа: 401", code == 401, f"({code})")
        init, n, text = asyncio.run(_http_session(url, token))
        check("с ключом: подключение и инструкция", "миллиметры" in (init.instructions or ""))
        check("с ключом: инструменты видны", n >= 55, f"({n})")
        check("с ключом: draw_batch работает", '"created": 1' in text, f"({text[:80]})")
    finally:
        proc.terminate()
        proc.wait(10)


if __name__ == "__main__":
    unit_part()
    tools_part()
    if MODE == "ezdxf":
        cli_part()
        http_part()
    print("\n" + ("ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ" if not fails else f"ОШИБОК: {fails}"))
    sys.exit(1 if fails else 0)
