@echo off
rem Switch the display mode (desktop character / browser / both).
rem   display.bat mascot|browser|both   save it; if koemado is running, apply it on the spot
rem   display.bat                       show the current mode and choose by number
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONUTF8=1"
if not exist "%~dp0app\.venv\Scripts\python.exe" (
    echo Not installed yet. Run setup.bat first.
    pause
    exit /b 1
)
"%~dp0app\.venv\Scripts\python.exe" "%~dp0display.py" %*
if "%~1"=="" pause
