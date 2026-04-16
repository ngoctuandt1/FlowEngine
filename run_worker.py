#!/usr/bin/env python3
"""Start the FlowEngine worker.

Usage:
    python run_worker.py

Environment variables (or .env file):
    SERVER_URL          - FlowEngine server URL  (default: http://localhost:8000)
    WORKER_ID           - Unique worker identifier (default: worker-1)
    API_KEY             - Server API key          (default: empty)
    CHROME_USER_DATA_DIR - Path to Chrome user data directory
    WORKER_PROFILES     - Comma-separated profile names (default: default)
    POLL_INTERVAL_SEC   - Seconds between claim attempts (default: 5)
    MAX_CONCURRENT_JOBS - Max jobs at once        (default: 1)
"""

import sys
from pathlib import Path

# Ensure project root is on sys.path so "worker" package resolves
sys.path.insert(0, str(Path(__file__).resolve().parent))

from worker.main import main  # noqa: E402

if __name__ == "__main__":
    main()
