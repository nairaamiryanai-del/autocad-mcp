"""Сквозной тест по протоколу MCP: клиент запускает сервер по stdio,
как это делают Claude Desktop / Claude Code, и вызывает инструменты."""
import asyncio
import os
import sys
import time

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ENGINE = os.environ.get("TEST_ENGINE", "ezdxf")


async def main() -> None:
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "autocad_mcp.server"],
        env={**os.environ, "AUTOCAD_MCP_ENGINE": ENGINE, "AUTOCAD_MCP_OUT": os.path.join(ROOT, "out")},
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            print("tools:", len(tools.tools))
            bad = [t.name for t in tools.tools
                   if not t.description or t.input_schema.get("type") != "object" or "properties" not in t.input_schema]
            print("схемы:", "OK" if not bad else f"ПРОБЛЕМЫ {bad}")
            desc_len = sum(len(t.description or "") for t in tools.tools)
            print("суммарная длина описаний:", desc_len, "символов")

            async def call(name, args=None):
                res = await session.call_tool(name, args or {})
                text = " ".join(getattr(c, "text", "") for c in res.content)
                print(f"\n> {name}{'  [ОШИБКА]' if getattr(res, 'is_error', False) else ''}\n{text}")
                return res

            await call("acad_status")
            await call("new_drawing")
            await call("draw_construction", {
                "kind": "window",
                "params": {
                    "width": 1800, "height": 1500,
                    "sashes": [
                        {"width": 1100, "type": "Поворотно-откидное", "handle": "Правая"},
                        {"width": 700, "type": "Глухое"},
                    ],
                },
            })
            await call("draw_text", {"x": 0, "y": -700, "text": "К-Замер: окно 1800x1500", "height": 80})
            ext = "dwg" if ENGINE == "com" else "dxf"
            if ENGINE == "com":
                # файл открыт в другой вкладке AutoCAD: ждём понятное сообщение, а не падение
                await call("save_drawing", {"path": os.path.join(ROOT, "out", "fixes_com.dwg")})
            path = os.path.join(ROOT, "out", f"mcp_{ENGINE}_{int(time.time())}.{ext}")
            await call("save_drawing", {"path": path})
            await call("acad_status")
            # новые инструменты через протокол
            await call("list_entities", {"entity_type": "DIMENSION", "limit": 3})
            await call("render_preview", {"path": os.path.join(ROOT, "out", f"mcp_{ENGINE}_preview.png")})
            await call("run_command" if ENGINE == "ezdxf" else "measure", {"command": "_.LINE"} if ENGINE == "ezdxf"
                       else {"x1": 0, "y1": 0, "x2": 3, "y2": 4})


asyncio.run(main())
