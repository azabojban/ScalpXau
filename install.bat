@echo off
chcp 65001 >nul
cd /d "%~dp0"
title ScalpXau — ornau

if exist ".venv\Scripts\python.exe" (
    set PY=.venv\Scripts\python.exe
    set PIP=.venv\Scripts\pip.exe
) else (
    python -m venv .venv
    set PY=.venv\Scripts\python.exe
    set PIP=.venv\Scripts\pip.exe
)

"%PIP%" install -r requirements.txt
echo OK — start.bat iske qosyngiz
pause
