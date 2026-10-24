"""Запуск сервера и настройка клиентов.

  autocad-mcp                         stdio: Claude Desktop, Claude Code, Cursor, VS Code, Windsurf...
  autocad-mcp --http                  HTTP (Streamable HTTP) на http://127.0.0.1:8765/mcp
  autocad-mcp --http --host 0.0.0.0 --token СЕКРЕТ   доступ из сети, только с ключом
  autocad-mcp --check                 проверка: версия AutoCAD, режим, папка вывода
  autocad-mcp --setup cursor          прописать сервер в настройки клиента
"""

from __future__ import annotations

import argparse
import hmac
import json
import os
import secrets
import shutil
import subprocess
import sys

LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")


def _python() -> str:
    return sys.executable


def server_entry(engine: str | None = None, out: str | None = None) -> dict:
    """Запись сервера для конфигов клиентов (stdio)."""
    env = {}
    if engine:
        env["AUTOCAD_MCP_ENGINE"] = engine
    if out:
        env["AUTOCAD_MCP_OUT"] = out
    entry: dict = {"command": _python(), "args": ["-m", "autocad_mcp.server"]}
    if env:
        entry["env"] = env
    return entry


def _appdata(*parts: str) -> str:
    base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, *parts)


def _home(*parts: str) -> str:
    return os.path.join(os.path.expanduser("~"), *parts)


def _claude_desktop_path() -> str:
    if sys.platform == "win32":
        return _appdata("Claude", "claude_desktop_config.json")
    if sys.platform == "darwin":
        return _home("Library", "Application Support", "Claude", "claude_desktop_config.json")
    return _home(".config", "Claude", "claude_desktop_config.json")


def _vscode_path() -> str:
    if sys.platform == "win32":
        return _appdata("Code", "User", "mcp.json")
    if sys.platform == "darwin":
        return _home("Library", "Application Support", "Code", "User", "mcp.json")
    return _home(".config", "Code", "User", "mcp.json")


# клиент -> (путь к конфигу, ключ со списком серверов)
CLIENTS = {
    "claude-desktop": (_claude_desktop_path, "mcpServers"),
    "cursor": (lambda: _home(".cursor", "mcp.json"), "mcpServers"),
    "windsurf": (lambda: _home(".codeium", "windsurf", "mcp_config.json"), "mcpServers"),
    "vscode": (_vscode_path, "servers"),
}


def setup_client(client: str, name: str, engine: str | None, out: str | None) -> str:
    """Добавить сервер в конфиг клиента. Старый конфиг сохраняется рядом (.bak-autocad)."""
    entry = server_entry(engine, out)
    if client == "claude-code":
        cmd = ["claude", "mcp", "add", name, "--scope", "user"]
        for k, v in entry.get("env", {}).items():
            cmd += ["-e", f"{k}={v}"]
        cmd += ["--", entry["command"], *entry["args"]]
        if shutil.which("claude"):
            r = subprocess.run(cmd, capture_output=True, text=True)
            if r.returncode == 0:
                return f"Claude Code: сервер «{name}» добавлен. Проверка: claude mcp list"
            return f"Claude Code ответил ошибкой: {(r.stderr or r.stdout).strip()}\nКоманда: {subprocess.list2cmdline(cmd)}"
        return "Claude Code не найден в PATH. Выполните вручную:\n" + subprocess.list2cmdline(cmd)
    if client not in CLIENTS:
        raise SystemExit(f"Неизвестный клиент {client}. Доступно: claude-code, {', '.join(CLIENTS)}")
    path_fn, key = CLIENTS[client]
    path = path_fn()
    data: dict = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8-sig") as f:
            text = f.read().strip()
        if text:
            try:
                data = json.loads(text)
            except json.JSONDecodeError as e:
                raise SystemExit(f"{path} не читается как JSON ({e}). Исправьте файл или добавьте сервер вручную.")
        shutil.copy2(path, path + ".bak-autocad")
    if client == "vscode":
        entry = {"type": "stdio", **entry}
    data.setdefault(key, {})[name] = entry
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return f"{client}: сервер «{name}» записан в {path}. Перезапустите программу, чтобы она его увидела."


def check(engine: str | None) -> int:
    """Самопроверка для нового компьютера."""
    from . import server as S

    print(f"Python: {sys.version.split()[0]} ({_python()})")
    print(f"Папка для файлов: {S.OUT_DIR}")
    if sys.platform == "win32":
        from . import acad_versions as A

        progs = A.installed_progids()
        print("Установленный AutoCAD:", ", ".join(progs) if progs else "не найден")
        print("AutoCAD запущен:", "да" if A.is_running() else "нет")
    else:
        print("Не Windows: доступен режим ezdxf (DXF без AutoCAD)")
    try:
        st = S.engine().status()
    except Exception as e:  # noqa: BLE001
        print("ОШИБКА подключения:", e)
        return 1
    print("Режим:", st.get("engine"), "|", st.get("product") or "без AutoCAD")
    print("Готово: сервер работает.")
    return 0


def _auth_app(app, token: str | None):
    """ASGI-прослойка: запросы без ключа (Authorization: Bearer ...) отклоняются."""
    if not token:
        return app
    expected = ("Bearer " + token).encode()

    async def guarded(scope, receive, send):
        if scope["type"] == "http":
            got = dict(scope.get("headers") or []).get(b"authorization", b"")
            if not hmac.compare_digest(got, expected):
                await send({"type": "http.response.start", "status": 401,
                            "headers": [(b"content-type", b"text/plain; charset=utf-8"),
                                        (b"www-authenticate", b"Bearer")]})
                await send({"type": "http.response.body", "body": "Нужен ключ: Authorization: Bearer <token>".encode()})
                return
        await app(scope, receive, send)

    return guarded


def serve_http(host: str, port: int, token: str | None, path: str) -> None:
    import uvicorn
    from mcp.server.transport_security import TransportSecuritySettings

    from .server import mcp

    local = host in LOCAL_HOSTS
    if not local and not token:
        suggestion = secrets.token_urlsafe(24)
        raise SystemExit(
            f"Доступ из сети ({host}) без ключа открыл бы ваш AutoCAD всем в сети.\n"
            f"Запустите с ключом, например:\n  autocad-mcp --http --host {host} --port {port} --token {suggestion}\n"
            "и укажите этот ключ в клиенте: заголовок Authorization: Bearer <ключ>."
        )
    security = (
        TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=["127.0.0.1:*", "localhost:*", "[::1]:*"],
            allowed_origins=["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"],
        )
        if local
        else TransportSecuritySettings(enable_dns_rebinding_protection=False)
    )
    app = mcp.streamable_http_app(streamable_http_path=path, transport_security=security, host=host)
    shown = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    print(f"AutoCAD MCP: http://{shown}:{port}{path}" + ("  (нужен ключ)" if token else ""), file=sys.stderr)
    uvicorn.run(_auth_app(app, token), host=host, port=port, log_level="warning")


def run(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(
        prog="autocad-mcp",
        description="MCP-сервер для AutoCAD (любая версия с COM) и DXF без AutoCAD.",
    )
    p.add_argument("--engine", choices=["auto", "com", "ezdxf"], help="режим (по умолчанию auto)")
    p.add_argument("--out", help="папка для файлов (по умолчанию Документы/AutoCAD-MCP)")
    p.add_argument("--http", action="store_true", help="HTTP вместо stdio (Streamable HTTP)")
    p.add_argument("--host", default="127.0.0.1", help="адрес для --http (0.0.0.0: доступ из сети, нужен --token)")
    p.add_argument("--port", type=int, default=8765, help="порт для --http (по умолчанию 8765)")
    p.add_argument("--path", default="/mcp", help="путь для --http (по умолчанию /mcp)")
    p.add_argument("--token", default=os.environ.get("AUTOCAD_MCP_TOKEN"),
                   help="ключ для --http (или AUTOCAD_MCP_TOKEN); клиент шлёт Authorization: Bearer <ключ>")
    p.add_argument("--check", action="store_true", help="проверить установку и выйти")
    p.add_argument("--setup", metavar="CLIENT",
                   help="прописать сервер в клиент: claude-desktop, claude-code, cursor, vscode, windsurf")
    p.add_argument("--name", default="autocad", help="имя сервера в клиенте (для --setup)")
    p.add_argument("--print-config", action="store_true", help="показать JSON для ручной настройки клиента")
    a = p.parse_args(argv)

    # настройки до создания движка
    if a.engine:
        os.environ["AUTOCAD_MCP_ENGINE"] = a.engine
    if a.out:
        os.environ["AUTOCAD_MCP_OUT"] = os.path.abspath(a.out)
    from . import server as S

    S.ENGINE_PREF = os.environ.get("AUTOCAD_MCP_ENGINE", S.ENGINE_PREF)
    S.OUT_DIR = os.environ.get("AUTOCAD_MCP_OUT", S.OUT_DIR)

    if a.print_config:
        print(json.dumps({"mcpServers": {a.name: server_entry(a.engine, a.out and os.path.abspath(a.out))}},
                         ensure_ascii=False, indent=2))
        return
    if a.setup:
        for client in [c.strip() for c in a.setup.split(",") if c.strip()]:
            print(setup_client(client, a.name, a.engine, a.out and os.path.abspath(a.out)))
        return
    if a.check:
        raise SystemExit(check(a.engine))
    if a.http:
        serve_http(a.host, a.port, a.token, a.path)
        return
    S.mcp.run("stdio")


if __name__ == "__main__":
    run()
