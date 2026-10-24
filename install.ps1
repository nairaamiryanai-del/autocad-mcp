# Установка AutoCAD MCP на Windows одной командой (PowerShell):
#   irm https://raw.githubusercontent.com/nairaamiryanai-del/autocad-mcp/main/install.ps1 | iex
# или из скачанной папки проекта:
#   powershell -ExecutionPolicy Bypass -File install.ps1
#
# Что делает: находит Python 3.10+, ставит сервер в %LOCALAPPDATA%\autocad-mcp (свой venv, систему не трогает),
# проверяет установку и прописывает сервер в найденные программы: Claude Desktop, Claude Code, Cursor,
# VS Code, Windsurf. Старые настройки программ сохраняются рядом с расширением .bak-autocad.

$ErrorActionPreference = "Stop"
$Archive = "https://github.com/nairaamiryanai-del/autocad-mcp/archive/refs/heads/main.zip"
# AUTOCAD_MCP_HOME: своя папка установки; AUTOCAD_MCP_SKIP_SETUP=1: не трогать настройки программ
$Dir = if ($env:AUTOCAD_MCP_HOME) { $env:AUTOCAD_MCP_HOME } else { Join-Path $env:LOCALAPPDATA "autocad-mcp" }

function Find-Python {
    # объект, а не массив: PowerShell разворачивает массив из одного элемента при возврате
    foreach ($c in @(@{Exe = "py"; Extra = @("-3")}, @{Exe = "python"; Extra = @()}, @{Exe = "python3"; Extra = @()})) {
        if (-not (Get-Command $c.Exe -ErrorAction SilentlyContinue)) { continue }
        $extra = $c.Extra
        try {
            $v = & $c.Exe @extra -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
        } catch { continue }
        if ($v -match '^3\.(\d+)$' -and [int]$Matches[1] -ge 10) { return $c }
    }
    return $null
}

Write-Host "AutoCAD MCP: установка" -ForegroundColor Green
$py = Find-Python
if (-not $py) {
    Write-Host "Не найден Python 3.10 или новее." -ForegroundColor Yellow
    Write-Host "Установите его с https://www.python.org/downloads/ (отметьте «Add python.exe to PATH»)"
    Write-Host "или командой:  winget install Python.Python.3.12"
    Write-Host "и запустите установку ещё раз."
    exit 1
}
$pyArgs = $py.Extra

# источник: папка проекта, если скрипт запущен из неё, иначе архив с GitHub
$Source = $Archive
if ($PSScriptRoot -and (Test-Path (Join-Path $PSScriptRoot "pyproject.toml"))) { $Source = $PSScriptRoot }

Write-Host "1/4 Окружение Python в $Dir"
New-Item -ItemType Directory -Force $Dir | Out-Null
$venvPy = Join-Path $Dir ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) { & $py.Exe @pyArgs -m venv (Join-Path $Dir ".venv") }

Write-Host "2/4 Установка сервера (из $Source)"
& $venvPy -m pip install --quiet --upgrade pip
& $venvPy -m pip install --quiet --upgrade $Source
if ($LASTEXITCODE -ne 0) { Write-Host "Не удалось установить пакет." -ForegroundColor Red; exit 1 }

Write-Host "3/4 Проверка"
& $venvPy -m autocad_mcp --check

Write-Host "4/4 Подключение к AI-программам"
$clients = @()
if (Test-Path (Join-Path $env:APPDATA "Claude")) { $clients += "claude-desktop" }
if (Get-Command claude -ErrorAction SilentlyContinue) { $clients += "claude-code" }
if (Test-Path (Join-Path $HOME ".cursor")) { $clients += "cursor" }
if (Test-Path (Join-Path $env:APPDATA "Code\User")) { $clients += "vscode" }
if (Test-Path (Join-Path $HOME ".codeium\windsurf")) { $clients += "windsurf" }
if ($env:AUTOCAD_MCP_SKIP_SETUP) {
    Write-Host "Пропущено (AUTOCAD_MCP_SKIP_SETUP). Найдены программы: $($clients -join ', ')"
} elseif ($clients.Count -eq 0) {
    Write-Host "Программы не найдены. Настройки для ручного подключения:"
    & $venvPy -m autocad_mcp --print-config
} else {
    & $venvPy -m autocad_mcp --setup ($clients -join ",")
}

Write-Host ""
Write-Host "Готово. Перезапустите AI-программу и попросите, например: «Нарисуй в AutoCAD окно 1500 на 1400»." -ForegroundColor Green
Write-Host "Файлы сохраняются в Документы\AutoCAD-MCP. Обновление: запустите эту установку ещё раз."
