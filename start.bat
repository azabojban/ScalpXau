@echo off
chcp 65001 >nul
cd /d "%~dp0"
title ScalpXau Bot

if not exist ".env" (
    echo .env joq
    pause
    exit /b 1
)

set DRY_RUN=false

if exist ".venv\Scripts\python.exe" (
    set "PY=.venv\Scripts\python.exe"
) else (
    set "PY=python"
)

echo.
echo  ScalpXau bot iske qosyluda...
echo  Toqtatu: Ctrl+C
echo.

"%PY%" xau_scalp_main.py
pause
