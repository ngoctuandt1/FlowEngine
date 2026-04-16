@echo off
echo Starting FlowEngine Server...
cd /d "%~dp0.."
python -c "import uvicorn; uvicorn.run('server.app:app', host='0.0.0.0', port=8080, reload=True)"
