"""FlowEngine FastAPI application."""

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from server.config import SERVER_HOST, SERVER_PORT
from server.routes import jobs_router, uploads_router, worker_router, profiles_router, ws_router


FRONTEND_DIR = Path(__file__).parent.parent / "frontend"
# Worker writes output mp4s to FLOW_DOWNLOAD_DIR (see flow/download.py).
# Keep this resolution in lockstep so the dashboard "download" links
# produced from Job.output_files resolve against the same directory.
DOWNLOAD_DIR = Path(os.environ.get("FLOW_DOWNLOAD_DIR", "./downloads")).resolve()
UPLOAD_DIR = Path(os.environ.get("FLOW_UPLOAD_DIR", "./uploads")).resolve()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown hooks."""
    # --- startup ---
    from server.config import setup_logging
    setup_logging("server")
    from server.db import init_db
    await init_db()
    yield
    # --- shutdown ---


app = FastAPI(
    title="FlowEngine",
    version="0.1.0",
    lifespan=lifespan,
)

# -- CORS (local dev) ---------------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -- API routes ----------------------------------------------------------------
app.include_router(jobs_router)
app.include_router(uploads_router)
app.include_router(worker_router)
app.include_router(profiles_router)
app.include_router(ws_router)

# -- Static files (frontend) --------------------------------------------------
# Serve css/, js/, assets/ directories directly so relative paths in index.html work
if FRONTEND_DIR.is_dir():
    for subdir in ["css", "js", "assets"]:
        sub_path = FRONTEND_DIR / subdir
        if sub_path.is_dir():
            app.mount(f"/{subdir}", StaticFiles(directory=str(sub_path)), name=subdir)

# Expose generated video outputs so the dashboard can link to them.
# Created on first run if missing; worker needs the same dir to exist.
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/downloads", StaticFiles(directory=str(DOWNLOAD_DIR)), name="downloads")
app.mount("/uploads", StaticFiles(directory=str(UPLOAD_DIR)), name="uploads")


@app.get("/")
async def serve_index():
    """Serve the frontend SPA."""
    index = FRONTEND_DIR / "index.html"
    if index.exists():
        return FileResponse(str(index))
    return {"message": "FlowEngine API is running. No frontend found."}


@app.get("/health")
async def health():
    return {"status": "ok"}
