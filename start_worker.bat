@echo off
cd /d D:\AI\FlowEngine
set FLOW_USE_BASE_PROFILE=1
set CHROME_USER_DATA_DIR=D:\AI\chrome-profiles
set FLOW_DOWNLOAD_DIR=D:\AI\FlowEngine\downloads
set WORKER_PROFILES=ngoctuandt20
python -m worker.main
