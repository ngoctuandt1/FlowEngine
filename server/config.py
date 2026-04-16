"""Server configuration."""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Paths
BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

# Server
SERVER_HOST = os.getenv("SERVER_HOST", "0.0.0.0")
SERVER_PORT = int(os.getenv("SERVER_PORT", "8000"))
API_KEY = os.getenv("API_KEY", "dev-key")

# Database
DATABASE_PATH = os.getenv("DATABASE_PATH", str(DATA_DIR / "flowengine.db"))
