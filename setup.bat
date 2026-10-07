@echo off
rem koemado setup. ASCII only on purpose (Japanese here breaks cmd parsing).
rem Installs uv into tools\ if missing, then runs setup.py (all messages are printed by Python).
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "UV_PYTHON_INSTALL_DIR=%~dp0tools\python"
set "UV_CACHE_DIR=%~dp0tools\uv-cache"
set "UV_PYTHON_PREFERENCE=only-managed"

where uv >nul 2>nul
if %errorlevel%==0 (
    set "UV=uv"
) else (
    if not exist "%~dp0tools\uv.exe" (
        echo [setup] uv not found. Installing uv into %~dp0tools ...
        set "UV_INSTALL_DIR=%~dp0tools"
        set "UV_NO_MODIFY_PATH=1"
        powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
    )
    set "UV=%~dp0tools\uv.exe"
)
if not exist "%UV%" if not "%UV%"=="uv" (
    echo [setup] Failed to install uv. Check the network connection and try again.
    pause
    exit /b 1
)

"%UV%" run --python 3.12 setup.py %*
set "RC=%errorlevel%"
if not "%RC%"=="0" (
    echo.
    echo [setup] Finished with errors ^(code %RC%^). Run setup.bat again to resume.
)
pause
exit /b %RC%
