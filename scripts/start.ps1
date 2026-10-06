# ==============================================================================
# Grailed Liquidity Analyzer - launcher
#   start.bat            -> build UI if needed, migrate DB, serve app on :8000
#   start.bat -Mode dev  -> backend :8000 + Next.js dev server :3000
# ==============================================================================
[CmdletBinding()]
param(
    [ValidateSet("run", "dev")]
    [string]$Mode = "run"
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RootDir = Split-Path -Parent $ScriptDir
$BackendDir = Join-Path $RootDir "backend"
$FrontendDir = Join-Path $RootDir "frontend"
$VenvPython = Join-Path $BackendDir ".venv\Scripts\python.exe"
$Pnpm = if (Get-Command "pnpm.cmd" -ErrorAction SilentlyContinue) { "pnpm.cmd" } else { "pnpm" }

function Write-Step([string]$msg) { Write-Host ">> $msg" -ForegroundColor Cyan }
function Write-Warn([string]$msg) { Write-Host "[WARN] $msg" -ForegroundColor Yellow }

function Stop-PortProcesses([int]$Port) {
    $pids = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique
    foreach ($p in $pids) {
        if ($p -and $p -ne 0 -and $p -ne $PID) {
            Write-Warn "Освобождаю порт $Port (PID $p)"
            Stop-Process -Id $p -Force -ErrorAction SilentlyContinue
        }
    }
}

function Test-FrontendStale {
    $index = Join-Path $FrontendDir "out\index.html"
    if (-not (Test-Path $index)) { return $true }
    $built = (Get-Item $index).LastWriteTimeUtc
    $newer = Get-ChildItem (Join-Path $FrontendDir "src") -Recurse -File |
        Where-Object { $_.LastWriteTimeUtc -gt $built } | Select-Object -First 1
    return [bool]$newer
}

function Wait-Exit($processes) {
    Write-Host "Нажмите [Q] или Ctrl+C для остановки." -ForegroundColor Yellow
    try {
        while ($true) {
            if ([Console]::KeyAvailable -and [Console]::ReadKey($true).Key -eq [ConsoleKey]::Q) { break }
            if ($processes | Where-Object { $_.HasExited }) { Write-Warn "Процесс остановился."; break }
            Start-Sleep -Milliseconds 500
        }
    } finally {
        foreach ($process in $processes) {
            if (-not $process.HasExited) { Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue }
        }
        Stop-PortProcesses 8000
        Stop-PortProcesses 3000
    }
}

if (-not (Test-Path $VenvPython)) {
    Write-Warn "Окружение не установлено, запускаю setup."
    & (Join-Path $ScriptDir "install.ps1")
    if (-not (Test-Path $VenvPython)) { exit 1 }
}

Write-Step "Миграции базы данных"
Push-Location $BackendDir
try { & $VenvPython -m alembic upgrade head } finally { Pop-Location }

Stop-PortProcesses 8000
# Uvicorn must run without --reload/--workers on Windows (selector event loop).
$uvicornArgs = @("-m", "uvicorn", "app.main:app", "--port", "8000", "--host", "127.0.0.1")

if ($Mode -eq "dev") {
    Stop-PortProcesses 3000
    $backend = Start-Process -FilePath $VenvPython -ArgumentList $uvicornArgs -WorkingDirectory $BackendDir -PassThru -NoNewWindow
    $frontend = Start-Process -FilePath "cmd.exe" -ArgumentList "/c", "$Pnpm run dev" -WorkingDirectory $FrontendDir -PassThru -NoNewWindow
    Start-Sleep -Seconds 3
    Start-Process "http://127.0.0.1:3000"
    Wait-Exit @($backend, $frontend)
    exit 0
}

if (Test-FrontendStale) {
    Write-Step "Сборка интерфейса"
    Push-Location $FrontendDir
    try { & $Pnpm run build } finally { Pop-Location }
}

Write-Step "Запуск http://127.0.0.1:8000"
$backend = Start-Process -FilePath $VenvPython -ArgumentList $uvicornArgs -WorkingDirectory $BackendDir -PassThru -NoNewWindow
Start-Sleep -Seconds 3
Start-Process "http://127.0.0.1:8000"
Wait-Exit @($backend)
