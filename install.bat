@echo off
setlocal
cd /d "%~dp0"
title Monster Galaxy Auto-Farm - Installer

echo ==================================================
echo   Monster Galaxy Auto-Farm - First-Time Setup
echo ==================================================
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python was not found in PATH.
    echo Please install Python 3.10 or newer from https://www.python.org/downloads/
    echo IMPORTANT: Check "Add Python to PATH" during installation.
    echo.
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo [0/3] Creating virtual environment ^(.venv^)...
    python -m venv .venv
    if errorlevel 1 (
        echo [ERROR] Failed to create virtual environment.
        pause
        exit /b 1
    )
)

set "PY_EXE=%~dp0.venv\Scripts\python.exe"

echo.
echo [1/3] Installing Python dependencies...
"%PY_EXE%" -m pip install --upgrade pip
"%PY_EXE%" -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo [ERROR] Dependency installation failed.
    pause
    exit /b 1
)

echo.
echo [2/3] Installing Playwright Chromium browser...
"%PY_EXE%" -m playwright install chromium
if errorlevel 1 (
    echo.
    echo [ERROR] Playwright Chromium installation failed.
    pause
    exit /b 1
)

echo.
echo [3/3] Verifying installation...
"%PY_EXE%" -c "import cv2, mss, playwright, fastapi, uvicorn; print('All dependencies verified OK!')"
if errorlevel 1 (
    echo.
    echo [ERROR] Package verification failed.
    pause
    exit /b 1
)

echo.
echo ==================================================
echo   Installation complete!
echo   Double-click start.bat to launch the WebApp.
echo ==================================================
echo.
pause
