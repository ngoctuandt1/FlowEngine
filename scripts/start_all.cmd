@echo off
echo Starting FlowEngine (Server + Worker)...
cd /d "%~dp0.."

echo.
echo [1/2] Starting Server on port 8080...
start "FlowEngine Server" cmd /k "cd /d "%~dp0.." && python -c "import uvicorn; uvicorn.run('server.app:app', host='0.0.0.0', port=8080)""

echo [2/2] Starting Worker (waiting 3s for server)...
timeout /t 3 /nobreak >nul
start "FlowEngine Worker" cmd /k "cd /d "%~dp0.." && python -m worker.main"

echo.
echo FlowEngine started. Open http://localhost:8080
echo Press any key to close this window...
pause >nul
