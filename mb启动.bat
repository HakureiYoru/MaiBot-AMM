@echo off
chcp 65001 >nul
title MaiBot Launcher
color 0B

echo ========================================
echo            MaiBot Launcher
echo ========================================
echo.

cd /d "%~dp0"

set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

if exist ".venv\Scripts\activate.bat" (
    echo [INFO] Found virtual environment: .venv
    set "VENV_PATH=.venv"
) else if exist "venv\Scripts\activate.bat" (
    echo [INFO] Found virtual environment: venv
    set "VENV_PATH=venv"
) else if exist ".venv\Scripts\python.exe" (
    echo [INFO] Found virtual environment: .venv
    set "VENV_PATH=.venv"
) else if exist "venv\Scripts\python.exe" (
    echo [INFO] Found virtual environment: venv
    set "VENV_PATH=venv"
) else (
    echo [ERROR] Virtual environment not found.
    echo [HINT] Please create or sync the virtual environment in MaiBot root.
    echo.
    pause
    exit /b 1
)

echo [INFO] Virtual environment path: %CD%\%VENV_PATH%
echo [INFO] Activating virtual environment...
call "%VENV_PATH%\Scripts\activate.bat"

echo.
echo ========================================
echo            Environment
echo ========================================
echo [Python] Executable:
python -c "import sys; print('        ', sys.executable)"
echo [Python] Version:
python --version
echo [Python] Prefix:
python -c "import sys; print('        ', sys.prefix)"
echo ========================================
echo.

if "%~1"=="--check" (
    echo [INFO] Check mode only. MaiBot will not be started.
    exit /b 0
)

if exist "bot.py" (
    echo [INFO] Starting MaiBot with bot.py...
    python bot.py
) else (
    echo [ERROR] bot.py not found.
    echo [HINT] Please make sure this file is in MaiBot root.
    echo.
    pause
    exit /b 1
)

if %errorlevel% neq 0 (
    echo.
    echo [ERROR] MaiBot failed or exited unexpectedly.
    echo [ERROR_CODE] %errorlevel%
    echo.
    pause
)
