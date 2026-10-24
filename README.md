# AutoCAD MCP

Сервер [MCP](https://modelcontextprotocol.io), через который AI-помощник чертит в AutoCAD: геометрия, размеры, слои, блоки, таблицы, листы с рамкой по ГОСТ, планы с осями и стенами, окна и двери по замерам.

- **Любая версия AutoCAD** с COM (проверено на AutoCAD 2026 и 2027, рус.). Сервер сам находит запущенный или установленный AutoCAD.
- **Без AutoCAD тоже работает:** режим ezdxf создаёт DXF на Windows, macOS и Linux.
- **Любой AI-помощник:** Claude Desktop, Claude Code, Cursor, VS Code (Copilot), Windsurf, LM Studio и другие программы с поддержкой MCP. Подключение локально (stdio) или по сети (HTTP).

*English: MCP server for AutoCAD (any version via COM on Windows) and DXF without AutoCAD (ezdxf, any OS). 55 tools: drawing, dimensions, layers, blocks, tables, title-block sheets, building plans, windows/doors from measurements. Works with any MCP client over stdio or HTTP. Install on Windows: `irm https://raw.githubusercontent.com/nairaamiryanai-del/autocad-mcp/main/install.ps1 | iex`.*

## Установка

### Windows: одной командой

Откройте PowerShell и выполните:

```powershell
irm https://raw.githubusercontent.com/nairaamiryanai-del/autocad-mcp/main/install.ps1 | iex
```

Установщик:
1. находит Python 3.10 или новее (если его нет, подскажет, как поставить);
2. ставит сервер в `%LOCALAPPDATA%\autocad-mcp`, отдельно от системы;
3. проверяет, видит ли он AutoCAD;
4. прописывает сервер в найденные программы: Claude Desktop, Claude Code, Cursor, VS Code, Windsurf. Старые настройки сохраняются рядом с расширением `.bak-autocad`.

После установки перезапустите AI-программу. Обновление: та же команда.

Из скачанной папки проекта (без интернета к GitHub):

```powershell
powershell -ExecutionPolicy Bypass -Command "iex (Get-Content -Raw -Encoding UTF8 .\install.ps1)"
```

### Вручную (любая ОС)

```bash
python -m venv .venv
.venv/bin/pip install https://github.com/nairaamiryanai-del/autocad-mcp/archive/refs/heads/main.zip
.venv/bin/autocad-mcp --check
.venv/bin/autocad-mcp --setup claude-desktop,cursor
```

На Windows пути `.venv\Scripts\...`. Через [uv](https://docs.astral.sh/uv/) можно без установки: в настройках клиента команда `uvx`, аргументы `--from https://github.com/nairaamiryanai-del/autocad-mcp/archive/refs/heads/main.zip autocad-mcp`.

## Подключение к AI-помощникам

Обычно этим занимается установщик. Если нужно вручную:

| Программа | Как подключить |
|-----------|----------------|
| Claude Desktop, Cursor, Windsurf, VS Code | `autocad-mcp --setup claude-desktop` (или `cursor`, `windsurf`, `vscode`; можно через запятую) |
| Claude Code | `autocad-mcp --setup claude-code` |
| Любая другая (stdio) | `autocad-mcp --print-config` выдаёт JSON: команда = python из установки, аргументы `-m autocad_mcp.server` |
| Любая другая (HTTP) | запустите `autocad-mcp --http` и укажите адрес `http://127.0.0.1:8765/mcp` |

### HTTP и доступ по сети

```bash
autocad-mcp --http                                   # только этот компьютер: http://127.0.0.1:8765/mcp
autocad-mcp --http --host 0.0.0.0 --token СЕКРЕТ     # из локальной сети, только с ключом
```

- Без `--token` сервер в сеть не откроется: иначе любой в сети мог бы управлять вашим AutoCAD.
- Клиент передаёт ключ заголовком `Authorization: Bearer СЕКРЕТ`. Ключ можно задать переменной `AUTOCAD_MCP_TOKEN`.
- ChatGPT и другие облачные помощники подключаются только по публичному адресу `https://`. Для этого нужен туннель (например, Cloudflare Tunnel) и обязательно ключ.

## Настройки

| Параметр | Переменная | По умолчанию |
|----------|------------|--------------|
| `--engine auto\|com\|ezdxf` | `AUTOCAD_MCP_ENGINE` | `auto`: AutoCAD, если установлен, иначе DXF |
| `--out ПАПКА` | `AUTOCAD_MCP_OUT` | `Документы/AutoCAD-MCP` |
| | `AUTOCAD_MCP_PROGID` | выбрать версию AutoCAD вручную, например `AutoCAD.Application.25.1` |
| `--token` | `AUTOCAD_MCP_TOKEN` | ключ для HTTP |

### Версии AutoCAD

Порядок подключения: уже запущенный AutoCAD любой версии, иначе запуск самой новой установленной. Записи в реестре от удалённых версий пропускаются. `acad_status` показывает версию по-человечески, например «AutoCAD 2026 (R25.1)».

Чтобы файл открылся в старой версии, сохраните в её формате: `save_drawing(version="2013")`. Годы форматов: 2018, 2013, 2010, 2007, 2004, 2000; можно указать год своей версии (2016 сохранится в формате 2013).

## Инструменты (55)

| Группа | Инструменты |
|--------|-------------|
| Режим и файлы | `acad_status`, `set_engine`, `drawing_summary`, `new_drawing`, `open_drawing`, `save_drawing`, `export_dxf` |
| Геометрия | `draw_line`, `draw_polyline`, `draw_rectangle`, `draw_circle`, `draw_arc`, `draw_ellipse`, `draw_polygon`, `draw_point`, `draw_spline`, `draw_text`, `draw_mtext`, `draw_hatch` |
| Пакетом и массивом | `draw_batch` (до 5000 объектов одной командой, один шаг отмены), `array_entities` (прямоугольный и круговой массив) |
| Планы зданий | `draw_axes` (оси с марками по ГОСТ 21.101 и размерами), `draw_walls` (стены по осевой с проёмами, двери с дугой, окна), `add_dimension_chain` (цепочки размеров) |
| Размеры | `add_dimension`, `add_radial_dimension`, `add_angular_dimension`, `add_leader`, `set_annotation_scale` |
| Слои | `create_layer`, `set_layer`, `delete_layer`, `list_layers` |
| Объекты | `list_entities`, `select_entities`, `get_entity`, `transform_entities`, `erase_entities`, `set_properties`, `offset_entity`, `measure` |
| Блоки и таблицы | `create_block`, `insert_block`, `list_blocks`, `add_table` |
| Листы и вывод | `make_sheet` (рамка A4-A1, основная надпись ГОСТ 2.104), `export_pdf`, `render_preview` (PNG для проверки) |
| Окна и двери | `draw_construction`, `draw_order`, `import_order` (заказы из приложения «К-Замер») |
| Прочее | `zoom`, `purge`, `undo`, `run_command` (прямая команда AutoCAD) |

Фильтр объектов везде одинаковый: `layer` (шаблон, например `WALL*`), `entity_type` (`LINE,LWPOLYLINE,CIRCLE,TEXT,DIMENSION,INSERT,HATCH`...), `area` `[x1,y1,x2,y2]`.

### Что в каком режиме

| Возможность | com (AutoCAD) | ezdxf (без AutoCAD) |
|-------------|---------------|---------------------|
| Файл | DWG | DXF |
| `undo`, `run_command` | да | нет |
| `add_table` | родная таблица AutoCAD | сетка линиями и текст |
| `export_pdf` | плоттер «DWG To PDF.pc3» | matplotlib |
| `offset_entity` | все типы | отрезок, окружность, дуга, полилиния без дуг |

### Примеры запросов

- «Нарисуй план 18 на 12 метров: оси через 6 метров, стены 380 мм, два окна по 1500 и дверь 1000 по фасаду».
- «Окно 1800 на 1500, левая створка поворотно-откидная 1100 с ручкой справа, правая глухая. Поставь размеры».
- «Сделай лист A3 в масштабе 1:100 с основной надписью и сохрани PDF».
- «Сохрани чертёж для AutoCAD 2013».

## Оформление

- Стиль Standard переводится на **Arial**: в стандартном txt.shx нет кириллицы.
- Высота надписей по умолчанию около 1/60 габарита (не меньше 80 мм), задаётся `set_annotation_scale`.
- Пересекающиеся подписи линейных размеров разносятся по уровням.
- Типы линий подгружаются сами; русские имена (`штриховая`, `осевая`) и английские (`DASHED`, `CENTER`) равнозначны.
- Штрихпунктир осей масштабируется под масштаб листа, иначе на печати он сливается в сплошную.

## Надёжность режима COM

Все обращения к AutoCAD идут через один выделенный поток (объект AutoCAD живёт только в своём потоке). Если AutoCAD занят, вызов повторяется; если связь оборвалась, сервер переподключается. Составные операции не повторяются целиком, чтобы не задвоить нарисованное, и отменяются одним шагом `undo`. При работе по HTTP одновременные запросы выполняются по очереди.

**Перед работой сохраните свои открытые чертежи:** сервер рисует в активном документе AutoCAD.

## Проверка

Запускать с `PYTHONIOENCODING=utf-8`:

```bash
python tests/smoke.py                      # окно, дверь, витраж (ezdxf)
python tests/fixes_check.py ezdxf          # цвет слоя, шрифт, высота текста, разнос подписей (или com)
python tests/features_check.py ezdxf       # все группы инструментов (или com)
python tests/universal_check.py            # пакет, массивы, оси, стены, версии форматов, CLI, HTTP с ключом
python tests/com_threads_test.py           # COM из разных потоков
TEST_ENGINE=ezdxf python tests/stdio_client_test.py   # через протокол MCP (или com)
```

## Ограничения

- Радиальные и угловые размеры не участвуют в автоматическом разносе подписей.
- `export_pdf` в режиме COM меняет настройки печати листа «Модель» текущего чертежа.
- В ezdxf `measure` для полилиний с дугами считает по аппроксимации, `offset_entity` такие полилинии не смещает.
- `draw_walls` рисует стены линиями; штриховку стен добавьте `draw_hatch`.

## Структура

```
src/autocad_mcp/
  server.py         # инструменты MCP
  cli.py            # запуск: stdio, HTTP с ключом, --check, --setup
  acad_versions.py  # поиск AutoCAD любой версии
  engine_com.py     # режим AutoCAD (COM)
  engine_ezdxf.py   # режим DXF (ezdxf)
  drafting.py       # стены, оси
  constructions.py  # окна, двери, витражи
  orders.py         # заказы и спецификация
  sheets.py         # рамка листа, основная надпись
  preview.py        # PNG и PDF через matplotlib
  layout.py         # высота текста, разнос подписей
  common.py         # фильтры, типы линий, версии форматов
install.ps1         # установка на Windows
tests/
examples/
```

## Лицензия

MIT, см. [LICENSE](LICENSE).
