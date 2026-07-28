@echo off
chcp 65001 >nul
cd /d "%~dp0"
title ScalpXau — Auto Deploy Task

set "TASK=ScalpXau_AutoDeploy"
set "SCRIPT=%~dp0server_auto_deploy.ps1"

schtasks /Query /TN "%TASK%" >nul 2>&1
if not errorlevel 1 (
    echo Task bar — almasyru...
    schtasks /Delete /TN "%TASK%" /F
)

schtasks /Create /TN "%TASK%" /TR "powershell -NoProfile -ExecutionPolicy Bypass -File \"%SCRIPT%\"" /SC MINUTE /MO 3 /RU "%USERNAME%" /RL HIGHEST /F

if errorlevel 1 (
    echo QATe: Administrator retinde ishke qosyngyz
    pause
    exit /b 1
)

echo OK: %TASK% — 3 minut saiyan git pull + bot restart
echo Log: data\deploy.log
pause
