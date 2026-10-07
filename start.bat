@echo off
rem koemado: starts everything WITHOUT any console window (koemado.vbs runs the supervisor hidden).
rem   start.bat            normal start (this window closes right away; progress is shown in the browser)
rem   start.bat --console  debug start: each service in its own console window (old behavior)
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONUTF8=1"
if not exist "%~dp0app\.venv\Scripts\python.exe" (
    echo Not installed yet. Run setup.bat first.
    pause
    exit /b 1
)
if /i "%~1"=="--console" goto console
wscript.exe //nologo "%~dp0koemado.vbs"
exit /b 0

:console
shift
"%~dp0app\.venv\Scripts\python.exe" "%~dp0ctl.py" start %1 %2 %3 %4
if errorlevel 1 pause
