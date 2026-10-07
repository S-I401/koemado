@echo off
rem Stops all services started by start.bat.
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONUTF8=1"
if not exist "%~dp0app\.venv\Scripts\python.exe" (
    echo Not installed yet.
    pause
    exit /b 1
)
"%~dp0app\.venv\Scripts\python.exe" "%~dp0ctl.py" stop
timeout /t 3 >nul
