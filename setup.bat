@echo off
title Grailed Liquidity Analyzer - Setup
setlocal
cd /d "%~dp0"
echo ====================================================================
echo Starting Grailed Liquidity Analyzer Automated Setup...
echo ====================================================================
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install.ps1" %*
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [ERROR] Setup encountered an error. Press any key to exit.
    pause >nul
)
endlocal
