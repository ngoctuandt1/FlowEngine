"""FlowEngine FastAPI application."""

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from server.routes import (
    characters_router,
    jobs_router,
    media_cut_router,
    media_fetch_router,
    media_merge_router,
    profiles_router,
    prompt_builder_router,
    templates_router,
    tts_router,
    uploads_router,
    worker_router,
    ws_router,
)


FRONTEND_DIR = Path(__file__).parent.parent / "frontend"


def _resolve_data_dir(env_var: str, default: str) -> Path:
    """Resolve a data directory from env and fail fast on invalid file paths."""
    raw_value = (os.environ.get(env_var) or "").strip() or default
    resolved = Path(raw_value).expanduser().resolve()
    if resolved.exists() and not resolved.is_dir():
        raise RuntimeError(f"{env_var} must point to a directory, got file: {resolved}")
    return resolved


# Worker writes output media to FLOW_DOWNLOAD_DIR (see flow/download.py).
# Keep this resolution in lockstep so dashboard links from Job.output_files
# resolve against the same directory.
DOWNLOAD_DIR = _resolve_data_dir("FLOW_DOWNLOAD_DIR", "./downloads")
UPLOAD_DIR = _resolve_data_dir("FLOW_UPLOAD_DIR", "./uploads")


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
app.include_router(prompt_builder_router)
app.include_router(media_cut_router)
app.include_router(media_merge_router)
app.include_router(media_fetch_router)
app.include_router(characters_router)
app.include_router(uploads_router)
app.include_router(worker_router)
app.include_router(profiles_router)
app.include_router(tts_router)
app.include_router(templates_router)
app.include_router(tts_router)
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
