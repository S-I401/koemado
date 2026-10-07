@echo off
rem koemado: starts everything WITHOUT any console window (koemado.vbs runs the supervisor hidden).
rem   start.bat            normal start with the saved display mode (progress is shown by the character or the browser)
rem   start.bat --mascot   save the display mode "desktop character", then start (also --browser / --both)
rem   start.bat --console  debug start: each service in its own console window (old behavior)
rem Options can be combined in any order, e.g. start.bat --console --browser
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONUTF8=1"
if not exist "%~dp0app\.venv\Scripts\python.exe" (
    echo Not installed yet. Run setup.bat first.
    pause
    exit /b 1
)
set "DMODE="
set "CONSOLE="
set "EXTRA="
:parse
if "%~1"=="" goto parsed
if /i "%~1"=="--console" (set "CONSOLE=1") else if /i "%~1"=="--mascot" (set "DMODE=mascot") else if /i "%~1"=="--browser" (set "DMODE=browser") else if /i "%~1"=="--both" (set "DMODE=both") else (set "EXTRA=%EXTRA% %~1")
shift
goto parse
:parsed
if defined CONSOLE goto console
if defined DMODE (
    "%~dp0app\.venv\Scripts\python.exe" "%~dp0display.py" %DMODE% --quiet
    rem exit code 10 = already running, and the new display mode was applied on the spot
    if errorlevel 10 exit /b 0
)
wscript.exe //nologo "%~dp0koemado.vbs"
exit /b 0

:console
if defined DMODE "%~dp0app\.venv\Scripts\python.exe" "%~dp0display.py" %DMODE% --save-only
"%~dp0app\.venv\Scripts\python.exe" "%~dp0ctl.py" start %EXTRA%
if errorlevel 1 pause
