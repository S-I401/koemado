@echo off
rem Interactive settings (voice / LLM / character). Fallback when the /settings page cannot be opened.
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONUTF8=1"
if not exist "%~dp0app\.venv\Scripts\python.exe" (
    echo Not installed yet. Run setup.bat first.
    pause
    exit /b 1
)
"%~dp0app\.venv\Scripts\python.exe" "%~dp0configure.py"
pause
