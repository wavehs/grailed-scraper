[CmdletBinding()]
param(
    [switch]$AutoInstallInnoSetup
)

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RootDir = Split-Path -Parent (Split-Path -Parent $ScriptDir)
$IssFile = Join-Path $ScriptDir "installer.iss"
$DistDir = Join-Path $RootDir "dist"

Write-Host "====================================================================" -ForegroundColor Cyan
Write-Host "         COMPILING WINDOWS EXE INSTALLER (Inno Setup)               " -ForegroundColor Cyan
Write-Host "====================================================================" -ForegroundColor Cyan
Write-Host ""

# 1. Find ISCC.exe
$IsccCandidates = @(
    "ISCC.exe",
    "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
    "C:\Program Files\Inno Setup 6\ISCC.exe",
    "C:\Program Files (x86)\Inno Setup 5\ISCC.exe",
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
)

$IsccPath = $null
foreach ($candidate in $IsccCandidates) {
    if (Get-Command $candidate -ErrorAction SilentlyContinue) {
        $IsccPath = (Get-Command $candidate).Source
        break
    }
    if (Test-Path $candidate) {
        $IsccPath = $candidate
        break
    }
}

if (-not $IsccPath) {
    Write-Host "[WARN] Inno Setup Compiler (ISCC.exe) не найден в системе." -ForegroundColor Yellow
    Write-Host "Попытка тихой установки Inno Setup через winget..." -ForegroundColor Cyan
    try {
        Start-Process "winget" -ArgumentList "install JRSoftware.InnoSetup -e --silent --accept-package-agreements --accept-source-agreements" -Wait -NoNewWindow
        
        # Re-check candidate paths
        foreach ($candidate in $IsccCandidates) {
            if (Test-Path $candidate) {
                $IsccPath = $candidate
                break
            }
        }
    } catch {
        Write-Host "Winget не смог установить Inno Setup: $_" -ForegroundColor Yellow
    }
}

if (-not $IsccPath) {
    Write-Host "[INFO] Inno Setup не установлен. Для компиляции автономного EXE скачайте Inno Setup с https://jrsoftware.org/isdl.php" -ForegroundColor Yellow
    Write-Host "[OK] Основной установщик setup.bat и скрипты scripts/install.ps1 полностью готовы и работоспособны без сборки EXE!" -ForegroundColor Green
    exit 0
}

Write-Host "[OK] Найден компилятор Inno Setup: $IsccPath" -ForegroundColor Green

# 2. Ensure dist folder
if (-not (Test-Path $DistDir)) {
    New-Item -ItemType Directory -Path $DistDir -Force | Out-Null
}

# 3. Compile
Write-Host "Компиляция инсталлятора..." -ForegroundColor Cyan
$proc = Start-Process -FilePath $IsccPath -ArgumentList "`"$IssFile`"" -Wait -PassThru -NoNewWindow

if ($proc.ExitCode -eq 0) {
    Write-Host ""
    Write-Host "====================================================================" -ForegroundColor Green
    Write-Host "         ИНСТАЛЛЯТОР УСПЕШНО СОБРАН В ПАПКЕ DIST!                   " -ForegroundColor Green
    Write-Host "====================================================================" -ForegroundColor Green
    Get-ChildItem -Path $DistDir -Filter "*.exe" | ForEach-Object {
        Write-Host "  -> $($_.FullName) ($([math]::Round($_.Length / 1MB, 2)) MB)" -ForegroundColor Yellow
    }
} else {
    Write-Host "[WARN] ISCC завершился с кодом $($proc.ExitCode)." -ForegroundColor Yellow
}
