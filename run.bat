@echo off
chcp 65001 >nul 2>&1
title Mask Review Tool v2

echo ============================================
echo   Mask Review Tool v2 - Setup ^& Launch
echo ============================================
echo.

cd /d "%~dp0"

:: Check Python
where python >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python is not installed.
    echo         Install from https://www.python.org/downloads/
    echo         Make sure to check "Add Python to PATH".
    pause
    exit /b 1
)

:: Create venv if not exists
if not exist "venv" (
    echo [1/3] Creating virtual environment...
    python -m venv venv
    if %errorlevel% neq 0 (
        echo [ERROR] Failed to create virtual environment.
        pause
        exit /b 1
    )
    echo       Done.
) else (
    echo [1/3] Virtual environment found.
)

call venv\Scripts\activate.bat

:: Install dependencies
if not exist "venv\.deps_installed" (
    echo [2/3] Installing packages... (first time only, may take a few minutes)
    pip install -r requirements.txt --quiet
    if %errorlevel% neq 0 (
        echo [ERROR] Package installation failed.
        echo         Check your network connection.
        pause
        exit /b 1
    )
    echo. > "venv\.deps_installed"
    echo       Done.
) else (
    echo [2/3] Packages already installed.
)

:: pycocotools Windows fallback
python -c "from pycocotools import mask" >nul 2>&1
if %errorlevel% neq 0 (
    echo [INFO] Retrying pycocotools install (Windows build)...
    pip install pycocotools-windows --quiet 2>nul
)

:: Launch
echo [3/3] Starting server...
echo.
echo ============================================
echo   Open in browser: http://localhost:5000
echo   Quit: Close this window or Ctrl+C
echo ============================================
echo.

start "" "http://localhost:5000"
python app.py
pause
