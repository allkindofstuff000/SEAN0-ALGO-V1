@echo off
title SEAN0-ALGO — Local Dev Server
color 0A

echo.
echo  ============================================
echo   SEAN0-ALGO  ^|  Local Development Server
echo  ============================================
echo.

:: Move to project root
cd /d "%~dp0"

:: Check Python
where python >nul 2>&1 || (
    echo  [ERROR] Python not found. Add Python to PATH.
    pause & exit /b 1
)

:: Check .env exists
if not exist ".env" (
    echo  [WARN]  .env not found — OANDA stream will not connect
)

:: Install deps if needed
if not exist "venv\Scripts\activate.bat" (
    echo  [SETUP] Creating virtual environment...
    python -m venv venv
    echo  [SETUP] Installing requirements...
    venv\Scripts\pip install -r requirements.txt --quiet
)

:: Activate venv if present
if exist "venv\Scripts\activate.bat" (
    call venv\Scripts\activate.bat
)

echo  [INFO]  Starting server on http://localhost:8000
echo  [INFO]  Dashboard   : http://localhost:8000
echo  [INFO]  XAU Scalp   : http://localhost:8000/dashboard/xau-scalp
echo  [INFO]  API Docs    : http://localhost:8000/docs
echo  [INFO]  Press Ctrl+C to stop
echo.

:: Launch server — reload=True for live code edits
python -m uvicorn web.web_server:app --host 127.0.0.1 --port 8000 --reload --log-level info

pause
