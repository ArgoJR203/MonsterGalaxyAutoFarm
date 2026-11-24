@echo off
setlocal
cd /d "%~dp0"
title Monster Galaxy Auto-Farm - WebApp Server

if exist ".venv\Scripts\python.exe" (
    set "PY_EXE=%~dp0.venv\Scripts\python.exe"
) else (
    python --version >nul 2>&1
    if errorlevel 1 (
        echo [ERROR] Python environment not found.
        echo Please double-click install.bat first!
        echo.
        pause
        exit /b 1
    )
    set "PY_EXE=python"
)

echo ==================================================
echo   Starting Monster Galaxy Auto-Farm WebApp...
echo   Dashboard: http://127.0.0.1:8000
echo   Press Ctrl+C in this window to stop the server.
echo ==================================================
echo.

start "" /min cmd /c "timeout /t 2 /nobreak >nul && start "" http://127.0.0.1:8000"
"%PY_EXE%" webapp.py

echo.
echo Server stopped.
pause
