@echo off
rem Starts LLM -> TTS -> STT -> AIAvatarKit in separate windows, waits for each, then opens the browser.
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONUTF8=1"
if not exist "%~dp0app\.venv\Scripts\python.exe" (
    echo Not installed yet. Run setup.bat first.
    pause
    exit /b 1
)
"%~dp0app\.venv\Scripts\python.exe" "%~dp0ctl.py" start %*
if errorlevel 1 pause
