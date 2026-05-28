# FlowEngine

FlowEngine is a Python automation engine for running browser-driven media workflows with a FastAPI control server, SQLite-backed queue, and Playwright worker processes.

The project separates API orchestration from browser execution: the server manages jobs, assets, projects, and worker coordination, while workers run Chrome automation through Playwright profiles.

## Features

- FastAPI server for job, project, asset, and worker APIs.
- Playwright-based Chrome automation for browser workflows.
- SQLite persistence for lightweight local queue and app state.
- Worker queue system for claiming, running, and reporting job results.
- Docker-oriented deployment assets for repeatable hosting.
- Web dashboard for monitoring and operating automation jobs.

## Tech Stack

- Python
- FastAPI
- Playwright
- SQLite
- Docker
- Chrome automation

## Getting Started

1. Create a virtual environment:

   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```

2. Install dependencies:

   ```powershell
   pip install -r requirements.txt
   playwright install chromium
   ```

3. Copy example configuration and fill local values:

   ```powershell
   Copy-Item .env.example .env
   ```

4. Start the API server:

   ```powershell
   python run_server.py
   ```

5. Start a worker in another terminal:

   ```powershell
   python run_worker.py
   ```

## Security Notes

Runtime secrets, browser profiles, database files, logs, and local environment files are intentionally ignored by git. Do not commit `.env`, `secrets/`, `chrome-profiles/`, `profiles_ultra.txt`, `*.db`, or `*.log` files.
