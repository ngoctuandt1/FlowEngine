"""FlowEngine FastAPI application."""

import asyncio
import logging
import os
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from server.dashboard_auth import (
    DASHBOARD_AUTH_ENABLED,
    DashboardAuthMiddleware,
    api_login,
    api_logout,
    serve_login_page,
)
from server.routes.idea import router as idea_router
from server.routes.projects import router as projects_router
from server.routes.render import router as render_router
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
from server.routes.settings import router as settings_router


FRONTEND_DIR = Path(__file__).parent.parent / "frontend"
# Versioned assets (`?v=...` cache-bust on every script/link tag). Long
# max-age is safe because index.html bumps the query string per release.
STATIC_CACHEABLE_PREFIXES = ("/js/", "/css/", "/assets/")
STATIC_CACHE_CONTROL = "public, max-age=2592000, immutable"
# Poster jpg / png / webp under /downloads/ + /uploads/ are immutable per
# filename (filename includes a unix timestamp). Caching these on the CDN
# is the entire point of the perf work — visitors share a hot edge cache.
MEDIA_IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp", ".gif")
MEDIA_IMAGE_CACHE_CONTROL = "public, max-age=2592000, immutable"
# Videos stay private so Cloudflare passes through `Accept-Ranges: bytes`
# instead of stripping it — without ranges, HTML5 `<video>` stalls at
# readyState 0 (what caused the black-thumbnail bug pre-R9). Browser still
# caches locally for 5 minutes.
MEDIA_PREFIXES = ("/downloads/", "/uploads/")
MEDIA_CACHE_CONTROL = "private, max-age=300"
STALE_REAPER_INTERVAL_SEC = 60
STALE_RUNNING_THRESHOLD_SEC = 600

logger = logging.getLogger(__name__)


def _resolve_data_dir(env_var: str, default: str) -> Path:
    """Resolve a data directory from env and fail fast on invalid file paths."""
    raw_value = (os.environ.get(env_var) or "").strip() or default
    resolved = Path(raw_value).expanduser().resolve()
    if resolved.exists() and not resolved.is_dir():
        raise RuntimeError(f"{env_var} must point to a directory, got file: {resolved}")
    return resolved


def _env_positive_int(name: str, default: int) -> int:
    raw_value = (os.environ.get(name) or "").strip()
    if not raw_value:
        return default
    try:
        value = int(raw_value)
    except ValueError:
        logger.warning("%s must be an integer; using default %s", name, default)
        return default
    if value <= 0:
        logger.warning("%s must be positive; using default %s", name, default)
        return default
    return value


def _stale_reaper_interval_sec() -> int:
    return _env_positive_int(
        "FLOW_STALE_REAPER_INTERVAL_SEC",
        STALE_REAPER_INTERVAL_SEC,
    )


def _stale_running_threshold_sec() -> int:
    return _env_positive_int(
        "FLOW_STALE_RUNNING_THRESHOLD_SEC",
        STALE_RUNNING_THRESHOLD_SEC,
    )


def _parse_db_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _idle_seconds(updated_at: str, observed_at: datetime) -> int:
    return max(0, int((observed_at - _parse_db_datetime(updated_at)).total_seconds()))


def _append_stale_reaper_error(existing: str | None, marker: str) -> str:
    return f"{existing}\n{marker}" if existing else marker


def _is_stale_running_row(row, observed_at: datetime, threshold_sec: int) -> bool:
    return row is not None and row["status"] in {"claimed", "running"} and _idle_seconds(
        row["updated_at"],
        observed_at,
    ) > threshold_sec


async def _reset_stale_running_job(
    job_id: str,
    threshold_sec: int,
    observed_at: datetime,
) -> str | None:
    from server.db.database import get_db

    now = observed_at.isoformat()
    async with get_db() as db:
        await db.execute("BEGIN IMMEDIATE")
        try:
            cursor = await db.execute(
                """
                SELECT id, status, worker_id, updated_at, error
                FROM jobs
                WHERE id = ?
                  AND status IN ('claimed', 'running')
                """,
                (job_id,),
            )
            row = await cursor.fetchone()
            if not _is_stale_running_row(row, observed_at, threshold_sec):
                await db.execute("ROLLBACK")
                return None

            previous_worker = row["worker_id"] or "None"
            idle_for = _idle_seconds(row["updated_at"], observed_at)
            marker = (
                "stale_claim_reaped: "
                f"previous_worker={previous_worker} idle_for={idle_for}s"
            )
            await db.execute(
                """
                UPDATE jobs
                SET status = 'pending',
                    worker_id = NULL,
                    claimed_at = NULL,
                    error = ?,
                    updated_at = ?
                WHERE id = ?
                  AND status IN ('claimed', 'running')
                """,
                (_append_stale_reaper_error(row["error"], marker), now, job_id),
            )
            await db.execute(
                """
                UPDATE profiles
                SET current_job_id = NULL,
                    worker_id = NULL
                WHERE current_job_id = ?
                """,
                (job_id,),
            )
            await db.commit()
            logger.info(
                "reaped stale claim job %s previous_worker=%s idle_for=%ss",
                job_id,
                previous_worker,
                idle_for,
            )
            return job_id
        except Exception:
            with suppress(Exception):
                await db.execute("ROLLBACK")
            raise


async def _reap_stale_claims(threshold_sec: int | None = None) -> list[str]:
    from server.db.database import get_db

    effective_threshold_sec = (
        _stale_running_threshold_sec() if threshold_sec is None else threshold_sec
    )
    observed_at = datetime.now(UTC)
    cutoff = (observed_at - timedelta(seconds=effective_threshold_sec)).isoformat()

    async with get_db() as db:
        cursor = await db.execute(
            """
            SELECT id
            FROM jobs
            WHERE status IN ('claimed', 'running')
              AND updated_at < ?
            ORDER BY updated_at ASC
            """,
            (cutoff,),
        )
        rows = await cursor.fetchall()

    reaped: list[str] = []
    for row in rows:
        job_id = row["id"]
        try:
            reaped_id = await _reset_stale_running_job(
                job_id,
                effective_threshold_sec,
                observed_at,
            )
        except Exception:
            logger.exception("failed to reap stale claim job %s", job_id)
            continue
        if reaped_id is not None:
            reaped.append(reaped_id)
    return reaped


async def _reap_stale_running_claims(threshold_sec: int | None = None) -> list[str]:
    return await _reap_stale_claims(threshold_sec)


async def _stale_claim_reaper() -> None:
    interval_sec = _stale_reaper_interval_sec()
    threshold_sec = _stale_running_threshold_sec()
    logger.info(
        "stale claim reaper running interval_sec=%s threshold_sec=%s",
        interval_sec,
        threshold_sec,
    )
    while True:
        await asyncio.sleep(interval_sec)
        try:
            await _reap_stale_claims(threshold_sec)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("stale claim reaper iteration failed")


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
    from flow.credentials.sheet_loader import (
        MalformedSheetError,
        sheet_mode_enabled,
        sync_profiles_from_sheet,
    )
    if sheet_mode_enabled():
        try:
            sync_profiles_from_sheet()
        except MalformedSheetError:
            logger.exception(
                "Malformed sheet credentials at startup; existing profiles cache remains unchanged"
            )
    await _reap_stale_claims()
    stale_reaper_task = asyncio.create_task(
        _stale_claim_reaper(),
        name="stale-claim-reaper",
    )
    logger.info("stale claim reaper task started")
    try:
        yield
    finally:
        stale_reaper_task.cancel()
        with suppress(asyncio.CancelledError):
            await stale_reaper_task
        logger.info("stale claim reaper task stopped")
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

class _APIGZipMiddleware(GZipMiddleware):
    """GZip middleware scoped to non-media paths.

    /downloads/ and /uploads/ are served with Accept-Ranges support for HTML5
    <video> seeking. Compressing those responses would corrupt the byte offsets
    inside Content-Range headers on 206 Partial Content replies, breaking video
    playback. All other paths (API JSON, JS, CSS) are compressed normally.
    """

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http":
            path: str = scope.get("path", "")
            if any(path.startswith(p) for p in MEDIA_PREFIXES):
                await self.app(scope, receive, send)
                return
        await super().__call__(scope, receive, send)


# GZip API/asset responses >= 1 KB. Media paths are excluded (see above).
app.add_middleware(_APIGZipMiddleware, minimum_size=1000)

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
app.include_router(projects_router)
app.include_router(prompt_builder_router)
app.include_router(media_cut_router)
app.include_router(media_merge_router)
app.include_router(media_fetch_router)
app.include_router(characters_router)
app.include_router(llm_router)
app.include_router(idea_router)
app.include_router(product_pipeline_router)
app.include_router(retarget_router)
app.include_router(render_router)
app.include_router(uploads_router)
app.include_router(worker_router)
app.include_router(profiles_router)
app.include_router(templates_router)
app.include_router(tts_router)
app.include_router(settings_router)
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
    """Long-cache versioned bundles + media images, keep videos CDN-private."""
    response = await call_next(request)
    path = request.url.path
    if path.startswith(STATIC_CACHEABLE_PREFIXES):
        response.headers["Cache-Control"] = STATIC_CACHE_CONTROL
    elif path.startswith(MEDIA_PREFIXES):
        # Images in /downloads/ + /uploads/ (poster jpg, generated png, etc.)
        # are immutable per filename, so let Cloudflare cache them forever.
        # Videos stay private so the CDN doesn't strip Accept-Ranges.
        lower = path.lower()
        if lower.endswith(MEDIA_IMAGE_SUFFIXES):
            response.headers["Cache-Control"] = MEDIA_IMAGE_CACHE_CONTROL
        else:
            response.headers["Cache-Control"] = MEDIA_CACHE_CONTROL
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
