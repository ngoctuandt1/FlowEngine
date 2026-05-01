"""FlowEngine FastAPI application."""

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from server.dashboard_auth import (
    DASHBOARD_AUTH_ENABLED,
    DashboardAuthMiddleware,
    api_login,
    api_logout,
    serve_login_page,
)
from server.routes import (
    characters_router,
    jobs_router,
    llm_router,
    media_cut_router,
    media_fetch_router,
    media_merge_router,
    profiles_router,
    product_pipeline_router,
    prompt_builder_router,
    retarget_router,
    templates_router,
    tts_router,
    uploads_router,
    worker_router,
    ws_router,
)


FRONTEND_DIR = Path(__file__).parent.parent / "frontend"
STATIC_CACHEABLE_PREFIXES = ("/js/", "/css/", "/assets/")
STATIC_CACHE_CONTROL = "public, max-age=60, must-revalidate"


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

# -- CORS ---------------------------------------------------------------------
# Multi-site setup: the same FlowEngine backend can serve many distinct
# frontend sites (ai.hassio.io.vn, ai.ciem, etc) — they all hit
# /api/jobs + /ws/jobs. Set ALLOWED_ORIGINS to a comma-separated list
# of fully-qualified origins ("https://ai.hassio.io.vn,https://ai.ciem")
# in production. Default "*" keeps localhost dev frictionless.
#
# Note: with allow_credentials=True the spec forbids "*" for origins;
# browsers reject the response. So we toggle credentials off when using
# the wildcard and back on when an explicit allowlist is given.
_origins_env = (os.environ.get("ALLOWED_ORIGINS") or "").strip()
if _origins_env and _origins_env != "*":
    _allow_origins = [o.strip() for o in _origins_env.split(",") if o.strip()]
    _allow_credentials = True
else:
    _allow_origins = ["*"]
    _allow_credentials = False

# -- Dashboard password gate --------------------------------------------------
# Active only when DASHBOARD_PASSWORD is set. Browser navigations land on
# /login, API calls return JSON 401. Worker traffic (/api/worker/*) is gated
# separately by Bearer token in server/auth.py and is allowed past this
# middleware.
if DASHBOARD_AUTH_ENABLED:
    app.add_middleware(DashboardAuthMiddleware)

# CORSMiddleware must stay outermost so browser preflight OPTIONS requests
# are answered with Access-Control-Allow-* headers before auth runs.
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allow_origins,
    allow_credentials=_allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -- Auth endpoints (registered regardless so the SPA login page works) -------
app.add_api_route("/login", serve_login_page, methods=["GET"], include_in_schema=False)
app.add_api_route("/api/auth/login", api_login, methods=["POST"])
app.add_api_route("/api/auth/logout", api_logout, methods=["POST"])

# -- API routes ----------------------------------------------------------------
app.include_router(jobs_router)
app.include_router(prompt_builder_router)
app.include_router(media_cut_router)
app.include_router(media_merge_router)
app.include_router(media_fetch_router)
app.include_router(characters_router)
app.include_router(llm_router)
app.include_router(product_pipeline_router)
app.include_router(retarget_router)
app.include_router(uploads_router)
app.include_router(worker_router)
app.include_router(profiles_router)
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


@app.middleware("http")
async def set_static_cache_headers(request: Request, call_next):
    """Keep frontend bundles revalidating quickly after deploys."""
    response = await call_next(request)
    if request.url.path.startswith(STATIC_CACHEABLE_PREFIXES):
        response.headers["Cache-Control"] = STATIC_CACHE_CONTROL
    return response


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
