@echo off
chcp 65001 >nul
cd /d "%~dp0"
title ScalpXau — deploy qolmen

git pull
if errorlevel 1 (
    echo git pull qate
    pause
    exit /b 1
)

for /f "tokens=2" %%p in ('tasklist /FI "IMAGENAME eq python.exe" /FO LIST 2^>nul ^| findstr /B /C:"PID:"') do (
    wmic process where "ProcessId=%%p" get CommandLine 2>nul | findstr /I "xau_scalp_main.py" >nul && taskkill /PID %%p /F >nul 2>&1
)

"%SystemRoot%\System32\timeout.exe" /t 2 /nobreak >nul
start "" /MIN cmd /c start.bat
echo OK — bot qayta ishke qosyldy
pause
