@echo off
chcp 65001 >nul
cd /d "%~dp0"
title ScalpXau — Watchdog Task Scheduler

set "TASK=ScalpXau_Watchdog"
set "SCRIPT=%~dp0watchdog.ps1"

schtasks /Query /TN "%TASK%" >nul 2>&1
if not errorlevel 1 (
    echo Task bar — almasyru...
    schtasks /Delete /TN "%TASK%" /F
)

schtasks /Create /TN "%TASK%" /TR "powershell -NoProfile -ExecutionPolicy Bypass -File \"%SCRIPT%\"" /SC MINUTE /MO 5 /RU "%USERNAME%" /RL HIGHEST /F

if errorlevel 1 (
    echo QATe: Administrator retinde ishke qosyngyz
    pause
    exit /b 1
)

echo OK: %TASK% — 5 minut saiyan tekseredi
echo Log: data\watchdog.log
pause
