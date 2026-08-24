# ==============================================================================
# Grailed Liquidity Analyzer - Interactive Launcher & Process Supervisor
# ==============================================================================
[CmdletBinding()]
param(
    [string]$Mode = "menu"
)

$ErrorActionPreference = "Continue"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RootDir = Split-Path -Parent $ScriptDir
$ToolsDir = Join-Path $RootDir ".tools"

# Add portable node or npm if installed locally
if (Test-Path (Join-Path $ToolsDir "node")) {
    $env:Path = "$ToolsDir\node;$env:Path"
}
if (Test-Path "$env:APPDATA\npm") {
    $env:Path = "$env:APPDATA\npm;$env:Path"
}

$VenvPython = Join-Path $RootDir "backend\.venv\Scripts\python.exe"
$VenvDir = Join-Path $RootDir "backend\.venv"

# --- Output Helpers ---
function Write-Color([string]$text, [ConsoleColor]$color = [ConsoleColor]::White) {
    Write-Host $text -ForegroundColor $color
}
function Write-Success([string]$msg) {
    Write-Host "[OK] " -ForegroundColor Green -NoNewline
    Write-Host $msg -ForegroundColor White
}
function Write-Warn([string]$msg) {
    Write-Host "[WARN] " -ForegroundColor Yellow -NoNewline
    Write-Host $msg -ForegroundColor Yellow
}
function Write-Err([string]$msg) {
    Write-Host "[ERROR] " -ForegroundColor Red -NoNewline
    Write-Host $msg -ForegroundColor Red
}

function Show-Banner {
    Clear-Host
    Write-Color "====================================================================" Cyan
    Write-Color "         GRAILED LIQUIDITY ANALYZER - CONTROL CENTER                " Cyan
    Write-Color "====================================================================" Cyan
    Write-Host ""
}

function Test-Prerequisites {
    if (-not (Test-Path $VenvPython)) {
        Write-Warn "Виртуальное окружение не обнаружено!"
        Write-Host "Запуск автоматического установщика..."
        & (Join-Path $ScriptDir "install.ps1")
        if (-not (Test-Path $VenvPython)) {
            Write-Err "Установка не завершена. Завершение работы."
            pause
            exit 1
        }
    }
}

function Stop-PortProcesses([int]$Port) {
    try {
        $pids = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique
        foreach ($p in $pids) {
            if ($p -and $p -ne 0 -and $p -ne $PID) {
                Write-Warn "Освобождение порта $Port (PID: $p)..."
                Stop-Process -Id $p -Force -ErrorAction SilentlyContinue
            }
        }
    } catch {}
}

function Start-DesktopApp {
    Show-Banner
    Write-Color ">> Запуск нативного Десктопного приложения..." Green
    Write-Host ""

    Stop-PortProcesses 8000

    # Check if compiled frontend exists, if not build it
    if (-not (Test-Path (Join-Path $RootDir "frontend\out\index.html"))) {
        Write-Host "Сборка встроенного интерфейса Next.js..." -ForegroundColor Cyan
        Push-Location (Join-Path $RootDir "frontend")
        try {
            $pnpmCmd = if (Get-Command "pnpm.cmd" -ErrorAction SilentlyContinue) { "pnpm.cmd" } else { "pnpm" }
            & $pnpmCmd run build
        } finally {
            Pop-Location
        }
    }

    Write-Host "Открытие нативного окна программы (Microsoft Edge WebView2)..." -ForegroundColor Yellow
    Push-Location (Join-Path $RootDir "backend")
    try {
        & $VenvPython -m app.desktop
    } finally {
        Pop-Location
        Stop-PortProcesses 8000
    }
}

function Start-ProductionApp {
    Show-Banner
    Write-Color ">> Запуск веб-версии приложения в браузере..." Green
    Write-Host ""
    
    Stop-PortProcesses 8000
    Stop-PortProcesses 3000

    # Start Backend
    Write-Host "1. Запуск Backend (FastAPI / Uvicorn на порту 8000)..." -ForegroundColor Cyan
    $backendProcess = Start-Process -FilePath $VenvPython `
        -ArgumentList "-m", "uvicorn", "app.main:app", "--port", "8000", "--host", "127.0.0.1" `
        -WorkingDirectory (Join-Path $RootDir "backend") `
        -PassThru -NoNewWindow

    # Start Frontend
    Write-Host "2. Запуск Frontend (Next.js на порту 3000)..." -ForegroundColor Cyan
    $pnpmCmd = if (Get-Command "pnpm.cmd" -ErrorAction SilentlyContinue) { "pnpm.cmd" } else { "pnpm" }
    $frontendProcess = Start-Process -FilePath "cmd.exe" `
        -ArgumentList "/c", "$pnpmCmd run start" `
        -WorkingDirectory (Join-Path $RootDir "frontend") `
        -PassThru -NoNewWindow

    Start-Sleep -Seconds 3
    Write-Host ""
    Write-Success "Backend работает: http://127.0.0.1:8000"
    Write-Success "Frontend работает: http://127.0.0.1:3000"
    Write-Host ""
    Write-Host "Открытие браузера..." -ForegroundColor Yellow
    Start-Process "http://127.0.0.1:3000"

    Write-Color "====================================================================" Green
    Write-Color " Приложение активно! Нажмите [Q] или [Ctrl+C] для остановки всех служб. " Yellow
    Write-Color "====================================================================" Green

    try {
        while ($true) {
            if ([Console]::KeyAvailable) {
                $key = [Console]::ReadKey($true)
                if ($key.Key -eq [ConsoleKey]::Q -or $key.Key -eq [ConsoleKey]::Escape) {
                    break
                }
            }
            if ($backendProcess.HasExited -or $frontendProcess.HasExited) {
                Write-Warn "Один из сервисов остановился."
                break
            }
            Start-Sleep -Milliseconds 500
        }
    } finally {
        Write-Host ""
        Write-Warn "Остановка сервисов..."
        if ($backendProcess -and -not $backendProcess.HasExited) {
            Stop-Process -Id $backendProcess.Id -Force -ErrorAction SilentlyContinue
        }
        if ($frontendProcess -and -not $frontendProcess.HasExited) {
            Stop-Process -Id $frontendProcess.Id -Force -ErrorAction SilentlyContinue
        }
        Stop-PortProcesses 8000
        Stop-PortProcesses 3000
        Write-Success "Все службы остановлены."
    }
}

function Start-DevApp {
    Show-Banner
    Write-Color ">> Запуск приложения в режиме РАЗРАБОТКИ (Dev mode)..." Yellow
    Write-Host ""
    
    Stop-PortProcesses 8000
    Stop-PortProcesses 3000

    Write-Host "1. Запуск Backend (FastAPI)..." -ForegroundColor Cyan
    $backendProcess = Start-Process -FilePath $VenvPython `
        -ArgumentList "-m", "uvicorn", "app.main:app", "--port", "8000", "--host", "127.0.0.1" `
        -WorkingDirectory (Join-Path $RootDir "backend") `
        -PassThru -NoNewWindow

    Write-Host "2. Запуск Frontend (Next.js Dev Server)..." -ForegroundColor Cyan
    $pnpmCmd = if (Get-Command "pnpm.cmd" -ErrorAction SilentlyContinue) { "pnpm.cmd" } else { "pnpm" }
    $frontendProcess = Start-Process -FilePath "cmd.exe" `
        -ArgumentList "/c", "$pnpmCmd run dev" `
        -WorkingDirectory (Join-Path $RootDir "frontend") `
        -PassThru -NoNewWindow

    Start-Sleep -Seconds 3
    Write-Host ""
    Write-Success "Backend dev: http://127.0.0.1:8000"
    Write-Success "Frontend dev: http://127.0.0.1:3000"
    Write-Host ""
    Start-Process "http://127.0.0.1:3000"

    Write-Color "====================================================================" Yellow
    Write-Color " Dev-режим активен! Нажмите [Q] или [Ctrl+C] для остановки.          " Yellow
    Write-Color "====================================================================" Yellow

    try {
        while ($true) {
            if ([Console]::KeyAvailable) {
                $key = [Console]::ReadKey($true)
                if ($key.Key -eq [ConsoleKey]::Q -or $key.Key -eq [ConsoleKey]::Escape) {
                    break
                }
            }
            if ($backendProcess.HasExited -or $frontendProcess.HasExited) {
                break
            }
            Start-Sleep -Milliseconds 500
        }
    } finally {
        Write-Host ""
        Write-Warn "Остановка dev-серверов..."
        if ($backendProcess -and -not $backendProcess.HasExited) {
            Stop-Process -Id $backendProcess.Id -Force -ErrorAction SilentlyContinue
        }
        if ($frontendProcess -and -not $frontendProcess.HasExited) {
            Stop-Process -Id $frontendProcess.Id -Force -ErrorAction SilentlyContinue
        }
        Stop-PortProcesses 8000
        Stop-PortProcesses 3000
        Write-Success "Все службы остановлены."
    }
}

function Run-DoctorAndChecks {
    Show-Banner
    Write-Color ">> Диагностика и проверка тестов..." Cyan
    Write-Host ""
    
    Write-Host "--- 1. Backend Doctor (Стек Scrapling / Camoufox) ---" -ForegroundColor Cyan
    Push-Location (Join-Path $RootDir "backend")
    try {
        & $VenvPython -m app.cli doctor
    } finally {
        Pop-Location
    }

    Write-Host ""
    Write-Host "--- 2. Pytest (Тесты бэкенда) ---" -ForegroundColor Cyan
    Push-Location (Join-Path $RootDir "backend")
    try {
        & $VenvPython -m pytest tests -q
    } finally {
        Pop-Location
    }

    Write-Host ""
    Write-Host "--- 3. Vitest (Тесты фронтенда) ---" -ForegroundColor Cyan
    Push-Location (Join-Path $RootDir "frontend")
    try {
        $pnpmCmd = if (Get-Command "pnpm.cmd" -ErrorAction SilentlyContinue) { "pnpm.cmd" } else { "pnpm" }
        & $pnpmCmd run test
    } finally {
        Pop-Location
    }

    Write-Host ""
    Write-Success "Проверка завершена."
    Write-Host "Нажмите любую клавишу для возврата в меню..."
    $null = [Console]::ReadKey($true)
}

function Build-DesktopExecutable {
    Show-Banner
    Write-Color ">> Сборка автономного GrailedAnalyzer.exe..." Cyan
    Write-Host ""

    & (Join-Path $RootDir "installer\windows\build_desktop_exe.ps1")

    Write-Host ""
    Write-Host "Нажмите любую клавишу для возврата в меню..."
    $null = [Console]::ReadKey($true)
}

function Update-Dependencies {
    Show-Banner
    Write-Color ">> Быстрое обновление зависимостей и накатывание миграций..." Cyan
    Write-Host ""

    Write-Host "1. Обновление Python пакетов..." -ForegroundColor Cyan
    & (Join-Path $VenvDir "Scripts\pip.exe") install -r (Join-Path $RootDir "backend\requirements-dev.txt") --disable-pip-version-check

    Write-Host "2. Применение миграций БД Alembic..." -ForegroundColor Cyan
    Push-Location (Join-Path $RootDir "backend")
    try {
        & $VenvPython -m alembic upgrade head
    } finally {
        Pop-Location
    }

    Write-Host "3. Обновление Frontend зависимостей..." -ForegroundColor Cyan
    Push-Location (Join-Path $RootDir "frontend")
    try {
        $pnpmCmd = if (Get-Command "pnpm.cmd" -ErrorAction SilentlyContinue) { "pnpm.cmd" } else { "pnpm" }
        & $pnpmCmd install
        & $pnpmCmd run build
    } finally {
        Pop-Location
    }

    Write-Host ""
    Write-Success "Обновление успешно завершено!"
    Write-Host "Нажмите любую клавишу для возврата в меню..."
    $null = [Console]::ReadKey($true)
}

function Manage-Database {
    Show-Banner
    Write-Color ">> Управление базой данных SQLite" Cyan
    Write-Host ""
    Write-Host "  [1] Создать резервную копию базы данных (db-backup)"
    Write-Host "  [2] Применить политику хранения данных (retention --apply)"
    Write-Host "  [3] Перестроить модели ликвидности (market-rebuild)"
    Write-Host "  [0] Назад в главное меню"
    Write-Host ""
    $dbChoice = Read-Host "Выберите действие [0-3]"

    Push-Location (Join-Path $RootDir "backend")
    try {
        switch ($dbChoice) {
            "1" {
                Write-Host "Создание бэкапа..." -ForegroundColor Cyan
                & $VenvPython -m app.cli db-backup
            }
            "2" {
                Write-Host "Очистка устаревших данных по политике retention..." -ForegroundColor Cyan
                & $VenvPython -m app.cli retention --apply
            }
            "3" {
                Write-Host "Перестроение рыночных моделей..." -ForegroundColor Cyan
                & $VenvPython -m app.cli market-rebuild
            }
        }
    } finally {
        Pop-Location
    }

    if ($dbChoice -ne "0") {
        Write-Host ""
        Write-Host "Нажмите любую клавишу для возврата в меню..."
        $null = [Console]::ReadKey($true)
    }
}

# --- Main Dispatcher ---
Test-Prerequisites

if ($Mode -eq "desktop") {
    Start-DesktopApp
    exit 0
}
if ($Mode -eq "run" -or $Mode -eq "web") {
    Start-ProductionApp
    exit 0
}
if ($Mode -eq "dev") {
    Start-DevApp
    exit 0
}

while ($true) {
    Show-Banner
    Write-Host "  [1] " -NoNewline -ForegroundColor Green
    Write-Host "Запустить Desktop-приложение (Нативное окно без браузера)"
    Write-Host "  [2] " -NoNewline -ForegroundColor Green
    Write-Host "Запустить веб-версию (в браузере)"
    Write-Host "  [3] " -NoNewline -ForegroundColor Yellow
    Write-Host "Запустить в режиме разработки (Dev mode)"
    Write-Host "  [4] " -NoNewline -ForegroundColor Cyan
    Write-Host "Проверка здоровья (Doctor) и запуск тестов"
    Write-Host "  [5] " -NoNewline -ForegroundColor Cyan
    Write-Host "Скомпилировать автономный GrailedAnalyzer.exe"
    Write-Host "  [6] " -NoNewline -ForegroundColor Cyan
    Write-Host "Обновить зависимости и накат миграций"
    Write-Host "  [7] " -NoNewline -ForegroundColor Magenta
    Write-Host "Управление базой данных (Бэкап / Retention / Rebuild)"
    Write-Host "  [0] " -NoNewline -ForegroundColor Red
    Write-Host "Выход"
    Write-Host ""

    $choice = Read-Host "Выберите пункт меню [1, 2, 3, 4, 5, 6, 7, 0]"
    switch ($choice) {
        "1" { Start-DesktopApp }
        "2" { Start-ProductionApp }
        "3" { Start-DevApp }
        "4" { Run-DoctorAndChecks }
        "5" { Build-DesktopExecutable }
        "6" { Update-Dependencies }
        "7" { Manage-Database }
        "0" { exit 0 }
        default { Write-Warn "Неверный выбор. Повторите попытку."; Start-Sleep -Seconds 1 }
    }
}
