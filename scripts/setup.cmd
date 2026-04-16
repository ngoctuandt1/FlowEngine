@echo off
echo FlowEngine Setup
echo =================
cd /d "%~dp0.."

echo.
echo [1/4] Creating virtual environment...
if not exist "venv" (
    python -m venv venv
    echo     Created venv/
) else (
    echo     venv/ already exists
)

echo [2/4] Installing dependencies...
venv\Scripts\pip install -r requirements.txt

echo [3/4] Installing Playwright browsers...
venv\Scripts\python -m playwright install chromium

echo [4/4] Creating directories...
if not exist "data" mkdir data
if not exist "logs" mkdir logs
if not exist "downloads" mkdir downloads
if not exist "profiles" mkdir profiles

echo.
echo Setup complete!
echo   - Copy .env.example to .env and edit settings
echo   - Put Chrome profile(s) in profiles/
echo   - Run scripts\start_all.cmd to launch
pause
