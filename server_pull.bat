@echo off
chcp 65001 >nul
cd /d "%~dp0"
title ScalpXau — серверде жаңарту

where git >nul 2>&1
if errorlevel 1 (
    echo Git табылмады — орнатыңыз
    pause
    exit /b 1
)

if not exist ".env" (
    echo .env joq — .env.example-дан көшіріңіз:
    echo   copy .env.example .env
    pause
    exit /b 1
)

echo Git pull...
git pull

echo.
echo Bot restart керек ^(NSSM немесе start.bat^)
pause
