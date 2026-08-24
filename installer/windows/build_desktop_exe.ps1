# Grailed Liquidity Analyzer - Desktop EXE Packaging Script
# Compiles the Python runtime, FastAPI, Next.js static bundle, and PyWebView into dist/GrailedAnalyzer

[CmdletBinding()]
param (
    [switch]$Clean
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RootDir = (Resolve-Path "$ScriptDir\..\..").Path

Write-Host "=========================================================" -ForegroundColor Cyan
Write-Host "  Grailed Liquidity Analyzer - Desktop App Builder" -ForegroundColor Cyan
Write-Host "=========================================================" -ForegroundColor Cyan
Write-Host "Root directory: $RootDir" -ForegroundColor Gray

# 1. Ensure Frontend is built and exported
Write-Host "`n[1/4] Building Next.js static frontend..." -ForegroundColor Yellow
Push-Location "$RootDir\frontend"
try {
    pnpm.cmd run build
    if ($LASTEXITCODE -ne 0) {
        throw "Frontend build failed with exit code $LASTEXITCODE"
    }
} finally {
    Pop-Location
}

if (-not (Test-Path "$RootDir\frontend\out\index.html")) {
    throw "Frontend static export directory $RootDir\frontend\out does not exist or lacks index.html!"
}
Write-Host "  [OK] Frontend static export generated in frontend/out" -ForegroundColor Green

# 2. Ensure PyInstaller is installed in backend/.venv
Write-Host "`n[2/4] Verifying PyInstaller in backend virtualenv..." -ForegroundColor Yellow
$PyInstallerExe = "$RootDir\backend\.venv\Scripts\pyinstaller.exe"
if (-not (Test-Path $PyInstallerExe)) {
    & "$RootDir\backend\.venv\Scripts\pip.exe" install pyinstaller
}
Write-Host "  [OK] PyInstaller found: $PyInstallerExe" -ForegroundColor Green

# 3. Clean old build outputs if requested
if ($Clean) {
    Write-Host "`n[3/4] Cleaning previous build artifacts..." -ForegroundColor Yellow
    Remove-Item -Path "$RootDir\build" -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item -Path "$RootDir\dist\GrailedAnalyzer" -Recurse -Force -ErrorAction SilentlyContinue
} else {
    Write-Host "`n[3/4] Preparing build directories..." -ForegroundColor Yellow
}

# 4. Run PyInstaller
Write-Host "`n[4/4] Compiling native desktop bundle with PyInstaller..." -ForegroundColor Yellow
Push-Location $RootDir
try {
    & $PyInstallerExe "$RootDir\installer\windows\GrailedAnalyzer.spec" --noconfirm --distpath "$RootDir\dist" --workpath "$RootDir\build"
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed with exit code $LASTEXITCODE"
    }
} finally {
    Pop-Location
}

$OutputExe = "$RootDir\dist\GrailedAnalyzer\GrailedAnalyzer.exe"
if (Test-Path $OutputExe) {
    $ExeSize = (Get-Item $OutputExe).Length / 1MB
    Write-Host "`n=========================================================" -ForegroundColor Green
    Write-Host "  SUCCESS! Native Desktop Application Compiled!" -ForegroundColor Green
    Write-Host "  Location: $OutputExe" -ForegroundColor White
    Write-Host "  Target size: $('{0:N2}' -f $ExeSize) MB" -ForegroundColor White
    Write-Host "=========================================================" -ForegroundColor Green
} else {
    throw "Build failed: $OutputExe was not found after compilation."
}
