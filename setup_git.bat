@echo off
chcp 65001 >nul
cd /d "%~dp0"
title ScalpXau — Git орнату

where git >nul 2>&1
if errorlevel 1 (
    echo Git табылмады.
    echo.
    echo 1^) Орнатыңыз: https://git-scm.com/download/win
    echo    ^(немесе: winget install Git.Git^)
    echo 2^) Орнатудан кейін осы bat-ты қайта іске қосыңыз
    pause
    exit /b 1
)

if not exist ".git" (
    git init
    git branch -M main
)

git add -A
git status

echo.
echo --- Алғашқы commit ---
set /p MSG="Commit хабарламасы [ScalpXau bot initial]: "
if "%MSG%"=="" set MSG=ScalpXau bot initial
git commit -m "%MSG%"

echo.
echo OK. Git repo дайын: %CD%
echo.
echo GitHub private repo жасап:
echo   git remote add origin https://github.com/SIZIN_USER/ScalpXau.git
echo   git push -u origin main
echo.
pause
