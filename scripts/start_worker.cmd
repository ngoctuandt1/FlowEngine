@echo off
echo Starting FlowEngine Worker...
cd /d "%~dp0.."
python -m worker.main
