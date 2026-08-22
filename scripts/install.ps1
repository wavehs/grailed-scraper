# ==============================================================================
# Grailed Liquidity Analyzer - Universal Zero-Dependency Windows Installer
# ==============================================================================
[CmdletBinding()]
param(
    [switch]$NonInteractive,
    [switch]$SkipBuild,
    [switch]$SkipDoctor,
    [switch]$Force,
    [switch]$CreateDesktopShortcut,
    [switch]$AcknowledgeCompliance
)

$ErrorActionPreference = "Continue"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 -bor [Net.SecurityProtocolType]::Tls13

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RootDir = Split-Path -Parent $ScriptDir
$ToolsDir = Join-Path $RootDir ".tools"

# --- Output Helpers ---
function Write-Color([string]$text, [ConsoleColor]$color = [ConsoleColor]::White) {
    Write-Host $text -ForegroundColor $color
}
function Write-Step([string]$step, [string]$msg) {
    Write-Host "[$step] " -ForegroundColor Cyan -NoNewline
    Write-Host $msg -ForegroundColor White
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

function Update-SessionEnvironment {
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    $machinePath = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $env:Path = "$userPath;$machinePath;$env:Path"
}

function Download-FileWithProgress([string]$url, [string]$destination) {
    Write-Host "Загрузка: $url ..." -ForegroundColor Cyan
    try {
        $webClient = New-Object System.Net.WebClient
        $webClient.Headers.Add("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64)")
        $webClient.DownloadFile($url, $destination)
    } catch {
        # Fallback to Invoke-WebRequest
        Invoke-WebRequest -Uri $url -OutFile $destination -UseBasicParsing -UserAgent "Mozilla/5.0"
    }
}

# --- Smart Dependency Locators & Auto-Installers ---

function Find-Python {
    # Check current session PATH
    foreach ($cmd in @("python.exe", "python", "py.exe", "py", "python3.exe", "python3")) {
        if (Get-Command $cmd -ErrorAction SilentlyContinue) {
            try {
                $verOutput = & $cmd -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')" 2>$null
                if ($verOutput) {
                    $ver = [version]$verOutput.Trim()
                    if ($ver -ge [version]"3.11.0") {
                        return @{ Command = $cmd; Version = $ver }
                    }
                }
            } catch {}
        }
    }

    # Check common installation locations on Windows
    $commonPythonPaths = @(
        "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
        "$env:ProgramFiles\Python311\python.exe",
        "$env:ProgramFiles\Python312\python.exe",
        "$ToolsDir\python\python.exe"
    )
    foreach ($p in $commonPythonPaths) {
        if (Test-Path $p) {
            try {
                $verOutput = & $p -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')" 2>$null
                if ($verOutput) {
                    $ver = [version]$verOutput.Trim()
                    if ($ver -ge [version]"3.11.0") {
                        $env:Path = "$(Split-Path -Parent $p);$env:Path"
                        return @{ Command = $p; Version = $ver }
                    }
                }
            } catch {}
        }
    }
    return $null
}

function Install-PythonAuto {
    Write-Warn "Python 3.11+ не обнаружен. Начинается АВТОМАТИЧЕСКАЯ УСТАНОВКА..."
    
    # Tier 1: Try winget
    if (Get-Command "winget" -ErrorAction SilentlyContinue) {
        Write-Host "Попытка установки Python 3.11 через Windows Package Manager (winget)..." -ForegroundColor Cyan
        try {
            $wingetProcess = Start-Process "winget" -ArgumentList "install Python.Python.3.11 -e --silent --accept-package-agreements --accept-source-agreements" -Wait -PassThru -NoNewWindow
            Update-SessionEnvironment
            $found = Find-Python
            if ($found) {
                Write-Success "Python 3.11 успешно установлен через winget!"
                return $found
            }
        } catch {
            Write-Warn "Установка через winget не удалась, переход к прямому скачиванию..."
        }
    }

    # Tier 2: Direct official installer download
    Write-Host "Загрузка официального установщика Python 3.11.9 (python.org)..." -ForegroundColor Cyan
    $installerUrl = "https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe"
    $installerPath = Join-Path $env:TEMP "python-3.11.9-installer.exe"
    
    try {
        Download-FileWithProgress -url $installerUrl -destination $installerPath
        Write-Host "Запуск тихой установки Python 3.11..." -ForegroundColor Cyan
        $proc = Start-Process -FilePath $installerPath `
            -ArgumentList "/quiet", "InstallAllUsers=0", "PrependPath=1", "Include_test=0", "Include_pip=1", "SimpleInstall=1" `
            -Wait -PassThru
        
        Start-Sleep -Seconds 3
        Update-SessionEnvironment
        Remove-Item -Path $installerPath -Force -ErrorAction SilentlyContinue

        $found = Find-Python
        if ($found) {
            Write-Success "Python 3.11 успешно установлен и добавлен в систему!"
            return $found
        }
    } catch {
        Write-Err "Ошибка при установке официального пакета Python: $_"
    }

    return $null
}

function Find-Node {
    # Check session PATH
    if (Get-Command "node" -ErrorAction SilentlyContinue) {
        try {
            $verStr = (& node -v).Trim().TrimStart('v')
            $ver = [version]$verStr
            if ($ver -ge [version]"20.0.0") {
                return @{ Command = "node"; Version = $verStr }
            }
        } catch {}
    }

    # Check common paths & portable tools dir
    $commonNodePaths = @(
        "$env:ProgramFiles\nodejs\node.exe",
        "$env:LOCALAPPDATA\Programs\nodejs\node.exe",
        "$ToolsDir\node\node.exe"
    )
    foreach ($p in $commonNodePaths) {
        if (Test-Path $p) {
            try {
                $verStr = (& $p -v).Trim().TrimStart('v')
                $ver = [version]$verStr
                if ($ver -ge [version]"20.0.0") {
                    $nodeDir = Split-Path -Parent $p
                    $env:Path = "$nodeDir;$env:Path"
                    return @{ Command = $p; Version = $verStr }
                }
            } catch {}
        }
    }
    return $null
}

function Install-NodeAuto {
    Write-Warn "Node.js (v20+) не обнаружен. Начинается АВТОМАТИЧЕСКАЯ УСТАНОВКА..."

    # Tier 1: Try winget
    if (Get-Command "winget" -ErrorAction SilentlyContinue) {
        Write-Host "Попытка установки Node.js LTS через winget..." -ForegroundColor Cyan
        try {
            $proc = Start-Process "winget" -ArgumentList "install OpenJS.NodeJS.LTS -e --silent --accept-package-agreements --accept-source-agreements" -Wait -PassThru -NoNewWindow
            Update-SessionEnvironment
            $found = Find-Node
            if ($found) {
                Write-Success "Node.js успешно установлен через winget!"
                return $found
            }
        } catch {
            Write-Warn "Установка Node.js через winget не удалась, переход к портативной сборке..."
        }
    }

    # Tier 2: Official Portable Node.js v20 ZIP
    Write-Host "Загрузка официального портативного пакета Node.js 20 LTS (nodejs.org)..." -ForegroundColor Cyan
    $nodeZipUrl = "https://nodejs.org/dist/v20.18.3/node-v20.18.3-win-x64.zip"
    $nodeZipPath = Join-Path $env:TEMP "node-v20-win-x64.zip"
    $nodeExtractDir = Join-Path $ToolsDir "node_tmp"
    $nodeFinalDir = Join-Path $ToolsDir "node"

    try {
        if (-not (Test-Path $ToolsDir)) { New-Item -ItemType Directory -Path $ToolsDir -Force | Out-Null }
        Download-FileWithProgress -url $nodeZipUrl -destination $nodeZipPath
        
        Write-Host "Распаковка портативного Node.js..." -ForegroundColor Cyan
        if (Test-Path $nodeExtractDir) { Remove-Item -Path $nodeExtractDir -Recurse -Force -ErrorAction SilentlyContinue }
        if (Test-Path $nodeFinalDir) { Remove-Item -Path $nodeFinalDir -Recurse -Force -ErrorAction SilentlyContinue }
        
        Expand-Archive -Path $nodeZipPath -DestinationPath $nodeExtractDir -Force
        $innerDir = Get-ChildItem -Path $nodeExtractDir -Directory | Select-Object -First 1
        if ($innerDir) {
            Move-Item -Path $innerDir.FullName -Destination $nodeFinalDir -Force
        } else {
            Move-Item -Path $nodeExtractDir -Destination $nodeFinalDir -Force
        }
        
        Remove-Item -Path $nodeZipPath -Force -ErrorAction SilentlyContinue
        Remove-Item -Path $nodeExtractDir -Recurse -Force -ErrorAction SilentlyContinue

        $env:Path = "$nodeFinalDir;$env:Path"
        $found = Find-Node
        if ($found) {
            Write-Success "Портативный Node.js v$($found.Version) успешно развернут в $nodeFinalDir!"
            return $found
        }
    } catch {
        Write-Err "Ошибка при развертывании портативного Node.js: $_"
    }

    return $null
}

function Setup-PnpmAuto {
    Write-Step "-->" "Настройка менеджера пакетов pnpm..."
    
    # 1. Check existing
    foreach ($cmd in @("pnpm.cmd", "pnpm")) {
        if (Get-Command $cmd -ErrorAction SilentlyContinue) {
            $ver = (& $cmd -v 2>$null)
            if ($ver) {
                Write-Success "pnpm найден: v$($ver.Trim()) ($cmd)"
                return $cmd
            }
        }
    }

    # 2. Try Corepack
    Write-Host "Активация pnpm через Corepack..." -ForegroundColor Cyan
    try {
        & corepack enable 2>$null
        & corepack prepare pnpm@9.15.9 --activate 2>$null
        Update-SessionEnvironment
        foreach ($cmd in @("pnpm.cmd", "pnpm")) {
            if (Get-Command $cmd -ErrorAction SilentlyContinue) {
                return $cmd
            }
        }
    } catch {}

    # 3. Try npm global install
    Write-Host "Установка pnpm глобально через npm..." -ForegroundColor Cyan
    try {
        & npm.cmd install -g pnpm@9.15.9 2>$null
        $npmGlobalPath = "$env:APPDATA\npm"
        if (Test-Path $npmGlobalPath) {
            $env:Path = "$npmGlobalPath;$env:Path"
        }
        foreach ($cmd in @("$npmGlobalPath\pnpm.cmd", "pnpm.cmd", "pnpm")) {
            if (Test-Path $cmd -or (Get-Command $cmd -ErrorAction SilentlyContinue)) {
                Write-Success "pnpm успешно установлен через npm!"
                return $cmd
            }
        }
    } catch {}

    return $null
}

# --- Main Installer Workflow ---

Clear-Host
Write-Color "====================================================================" Cyan
Write-Color "     GRAILED LIQUIDITY ANALYZER - ZERO-DEPENDENCY SMART INSTALLER   " Cyan
Write-Color "====================================================================" Cyan
Write-Host ""

# --- 1. System Requirements & Auto-Bootstrap ---
Write-Step "1/8" "Проверка и автоматическая подготовка среды..."

# Check / Auto-Install Python
$PythonInfo = Find-Python
if (-not $PythonInfo) {
    $PythonInfo = Install-PythonAuto
}

if (-not $PythonInfo) {
    Write-Err "Критическая ошибка: Не удалось автоматически установить Python 3.11!"
    Write-Host "Пожалуйста, установите Python 3.11 вручную: https://www.python.org/downloads/" -ForegroundColor Yellow
    exit 1
}
$PythonCmd = $PythonInfo.Command
Write-Success "Python готов к работе: $($PythonInfo.Version) ($PythonCmd)"

# Check / Auto-Install Node.js
$NodeInfo = Find-Node
if (-not $NodeInfo) {
    $NodeInfo = Install-NodeAuto
}

if (-not $NodeInfo) {
    Write-Err "Критическая ошибка: Не удалось автоматически установить Node.js!"
    Write-Host "Пожалуйста, установите Node.js 20+: https://nodejs.org/" -ForegroundColor Yellow
    exit 1
}
Write-Success "Node.js готов к работе: v$($NodeInfo.Version)"

# Check / Auto-Install pnpm
$PnpmCmd = Setup-PnpmAuto
if (-not $PnpmCmd) {
    Write-Err "Не удалось автоматически настроить pnpm!"
    exit 1
}
Write-Success "pnpm готов к работе: $PnpmCmd"

# --- 2. Create Required Directories ---
Write-Step "2/8" "Инициализация директорий проекта..."
$dirsToCreate = @(
    (Join-Path $RootDir "data"),
    (Join-Path $RootDir "data\logs"),
    (Join-Path $RootDir "data\cache"),
    (Join-Path $RootDir "data\backups"),
    (Join-Path $RootDir "data\secrets")
)
foreach ($dir in $dirsToCreate) {
    if (-not (Test-Path $dir)) {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
    }
}
Write-Success "Директории хранилища data/ готовы."

# --- 3. Configuration (.env) Setup ---
Write-Step "3/8" "Настройка конфигурационного файла .env..."
$EnvFile = Join-Path $RootDir ".env"
$EnvExampleFile = Join-Path $RootDir ".env.example"

if (-not (Test-Path $EnvFile)) {
    if (Test-Path $EnvExampleFile) {
        Copy-Item -Path $EnvExampleFile -Destination $EnvFile -Force
        Write-Success "Создан файл .env из шаблона .env.example."
    } else {
        Write-Warn ".env.example не найден, создается базовый .env."
        @"
APP_SOURCE_MODE=live
APP_ENVIRONMENT=development
APP_BACKEND_BIND_HOST=127.0.0.1
APP_FRONTEND_BIND_HOST=127.0.0.1
APP_LOG_LEVEL=INFO
APP_LIVE_COMPLIANCE_ACKNOWLEDGED=false
APP_REQUESTS_PER_MINUTE=90
APP_MAX_CONCURRENT_REQUESTS=3
APP_FETCH_TIER_PREFERRED=T1
APP_FETCH_TIER_ALLOW_BROWSER=true
APP_FETCH_TIER_ALLOW_DOM=true
"@ | Out-File -FilePath $EnvFile -Encoding utf8
    }
} else {
    Write-Success "Файл .env уже существует."
}

# Check Compliance Acknowledgment
if ($AcknowledgeCompliance) {
    $envContent = Get-Content $EnvFile -Raw
    $envContent = $envContent -replace "APP_LIVE_COMPLIANCE_ACKNOWLEDGED=\w+", "APP_LIVE_COMPLIANCE_ACKNOWLEDGED=true"
    Set-Content -Path $EnvFile -Value $envContent -Encoding utf8
    Write-Success "Флаг APP_LIVE_COMPLIANCE_ACKNOWLEDGED установлен в true."
} else {
    $envContent = Get-Content $EnvFile -Raw
    if ($envContent -match "APP_LIVE_COMPLIANCE_ACKNOWLEDGED=false") {
        if (-not $NonInteractive) {
            Write-Host ""
            Write-Color "ВНИМАНИЕ: Для работы с живым источником Grailed требуется согласие с ToS и правилами доступа." Yellow
            $reply = Read-Host "Подтвердить соблюдение комплаенса и включить реальный парсинг? (Y/n)"
            if ($reply -eq "" -or $reply -match "^[yYдД]") {
                $envContent = $envContent -replace "APP_LIVE_COMPLIANCE_ACKNOWLEDGED=false", "APP_LIVE_COMPLIANCE_ACKNOWLEDGED=true"
                Set-Content -Path $EnvFile -Value $envContent -Encoding utf8
                Write-Success "Комплаенс подтвержден: APP_LIVE_COMPLIANCE_ACKNOWLEDGED=true"
            }
        }
    }
}

# --- 4. Python Virtual Environment Setup ---
Write-Step "4/8" "Настройка виртуального окружения Python (backend/.venv)..."
$VenvDir = Join-Path $RootDir "backend\.venv"
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
$VenvPip = Join-Path $VenvDir "Scripts\pip.exe"

if (-not (Test-Path $VenvPython) -or $Force) {
    if (Test-Path $VenvDir) {
        Remove-Item -Path $VenvDir -Recurse -Force -ErrorAction SilentlyContinue
    }
    Write-Host "Создание нового .venv с помощью $PythonCmd..."
    & $PythonCmd -m venv $VenvDir
    if (-not (Test-Path $VenvPython)) {
        Write-Err "Не удалось создать виртуальное окружение в $VenvDir!"
        exit 1
    }
    Write-Success "Виртуальное окружение создано."
} else {
    Write-Success "Виртуальное окружение уже существует."
}

# --- 5. Install Backend Dependencies & Scrapling Browser ---
Write-Step "5/8" "Установка зависимостей Backend и движков браузера..."
Write-Host "Обновление pip..."
& $VenvPython -m pip install --upgrade pip setuptools wheel --quiet

$ReqFile = Join-Path $RootDir "backend\requirements-dev.txt"
if (-not (Test-Path $ReqFile)) {
    $ReqFile = Join-Path $RootDir "backend\requirements.txt"
}
Write-Host "Установка Python пакетов из $(Split-Path -Leaf $ReqFile)..."
& $VenvPip install -r $ReqFile --disable-pip-version-check
if ($LASTEXITCODE -ne 0) {
    Write-Err "Ошибка при установке зависимостей Backend!"
    exit 1
}
Write-Success "Python зависимости успешно установлены."

# Scrapling browser install (Camoufox engine)
$ScraplingExe = Join-Path $VenvDir "Scripts\scrapling.exe"
if (Test-Path $ScraplingExe) {
    Write-Host "Запуск scrapling install для загрузки stealth-браузера..."
    try {
        & $ScraplingExe install
        Write-Success "Браузерные движки Scrapling / Camoufox готовы."
    } catch {
        Write-Warn "Предупреждение при установке scrapling: $_"
    }
} else {
    & $VenvPython -m scrapling install 2>$null
}

# --- 6. Database Migrations (Alembic) ---
Write-Step "6/8" "Применение миграций базы данных SQLite..."
Push-Location (Join-Path $RootDir "backend")
try {
    $AlembicExe = Join-Path $VenvDir "Scripts\alembic.exe"
    if (Test-Path $AlembicExe) {
        & $AlembicExe upgrade head
    } else {
        & $VenvPython -m alembic upgrade head
    }
    if ($LASTEXITCODE -eq 0) {
        Write-Success "Миграции базы данных успешно применены (alembic upgrade head)."
    } else {
        Write-Warn "Alembic завершился с кодом $LASTEXITCODE. Проверьте конфигурацию БД."
    }
} finally {
    Pop-Location
}

# --- 7. Frontend Setup & Build ---
Write-Step "7/8" "Установка зависимостей и сборка Frontend..."
Push-Location (Join-Path $RootDir "frontend")
try {
    Write-Host "Выполнение $PnpmCmd install..."
    & $PnpmCmd install --frozen-lockfile
    if ($LASTEXITCODE -ne 0) {
        Write-Warn "$PnpmCmd install --frozen-lockfile не удался, пробуем $PnpmCmd install..."
        & $PnpmCmd install
    }
    
    if (-not $SkipBuild) {
        Write-Host "Сборка production версии Next.js фронтенда..."
        & $PnpmCmd run build
        if ($LASTEXITCODE -eq 0) {
            Write-Success "Frontend успешно собран (Next.js production build готов)."
        } else {
            Write-Warn "Сборка frontend завершилась с ошибкой. Приложение можно будет запустить в dev-режиме."
        }
    } else {
        Write-Host "Сборка фронтенда пропущена по флагу -SkipBuild."
    }
} finally {
    Pop-Location
}

# --- 8. Doctor Verification & Launcher Shortcuts ---
Write-Step "8/8" "Диагностика системы и создание ярлыков..."
if (-not $SkipDoctor) {
    Push-Location (Join-Path $RootDir "backend")
    try {
        Write-Host "Запуск app.cli doctor..."
        & $VenvPython -m app.cli doctor
    } catch {
        Write-Warn "Doctor завершился с предупреждением: $_"
    } finally {
        Pop-Location
    }
}

# Ensure root start.bat exists
$StartBat = Join-Path $RootDir "start.bat"
@"
@echo off
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start.ps1" %*
"@ | Set-Content -Path $StartBat -Encoding ascii

# Create Desktop shortcut if requested or interactive
if ($CreateDesktopShortcut -or (-not $NonInteractive)) {
    try {
        $WshShell = New-Object -ComObject WScript.Shell
        $DesktopPath = [System.Environment]::GetFolderPath([System.Environment+SpecialFolder]::Desktop)
        $ShortcutPath = Join-Path $DesktopPath "Grailed Liquidity Analyzer.lnk"
        $Shortcut = $WshShell.CreateShortcut($ShortcutPath)
        $Shortcut.TargetPath = $StartBat
        $Shortcut.WorkingDirectory = $RootDir
        $Shortcut.Description = "Запуск Grailed Liquidity Analyzer"
        $Shortcut.Save()
        Write-Success "Создан ярлык на Рабочем столе: Grailed Liquidity Analyzer"
    } catch {
        Write-Warn "Не удалось автоматически создать ярлык на рабочем столе: $_"
    }
}

Write-Host ""
Write-Color "====================================================================" Green
Write-Color "                 УСТАНОВКА УСПЕШНО ЗАВЕРШЕНА!                       " Green
Write-Color "====================================================================" Green
Write-Host ""
Write-Host "Для запуска приложения используйте:" -ForegroundColor White
Write-Host "  -> Двойной клик по " -NoNewline
Write-Host "start.bat" -ForegroundColor Cyan -NoNewline
Write-Host " в корне проекта или ярлыку на Рабочем столе"
Write-Host "  -> Или в PowerShell: " -NoNewline
Write-Host ".\scripts\start.ps1" -ForegroundColor Yellow
Write-Host ""
