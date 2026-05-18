"""Timeline render endpoints."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, HTTPException, status

from server.db.render_store import (
    create_render_job,
    get_render_job,
    update_render_job,
)
from server.models.render import (
    RenderCreateResponse,
    RenderJob,
    RenderStatus,
    RenderStatusResponse,
    TimelinePayload,
)
from server.services.render_compose import compose_timeline

router = APIRouter(prefix="/api/render", tags=["render"])
RENDER_OUTPUT_DIR = Path("downloads/renders")

# Concurrency cap protects the worker from a flood of expensive ffmpeg jobs.
# Tunable via env so ops can dial up on bigger machines.
_DEFAULT_RENDER_CONCURRENT_MAX = 2


def _render_concurrent_cap() -> int:
    raw = os.environ.get("FLOW_RENDER_CONCURRENT_MAX")
    if not raw:
        return _DEFAULT_RENDER_CONCURRENT_MAX
    try:
        value = int(raw)
    except ValueError:
        return _DEFAULT_RENDER_CONCURRENT_MAX
    return value if value > 0 else _DEFAULT_RENDER_CONCURRENT_MAX


_active_renders = 0
_active_renders_lock = asyncio.Lock()


def _output_url(output_path: str | None) -> str | None:
    normalized = str(output_path or "").replace("\\", "/").strip()
    if not normalized:
        return None
    if normalized.startswith("/downloads/"):
        relative = normalized[len("/downloads/"):]
    elif normalized.startswith("downloads/"):
        relative = normalized[len("downloads/"):]
    else:
        relative = Path(normalized).name
    return f"/downloads/{quote(relative, safe='/')}"


async def _process_render_job(render_id: str, payload: TimelinePayload) -> None:
    global _active_renders
    output_path = RENDER_OUTPUT_DIR / f"{render_id}.mp4"
    await update_render_job(
        render_id,
        status=RenderStatus.RUNNING,
        progress=25,
        error=None,
    )
    try:
        await asyncio.to_thread(compose_timeline, payload, output_path)
    except Exception as exc:
        await update_render_job(
            render_id,
            status=RenderStatus.FAILED,
            progress=100,
            output_path=None,
            error=str(exc),
        )
        return
    finally:
        async with _active_renders_lock:
            if _active_renders > 0:
                _active_renders -= 1

    await update_render_job(
        render_id,
        status=RenderStatus.COMPLETED,
        progress=100,
        output_path=output_path.as_posix(),
        error=None,
    )


@router.post("/timeline", response_model=RenderCreateResponse, status_code=status.HTTP_202_ACCEPTED)
async def create_timeline_render(
    payload: TimelinePayload,
    background_tasks: BackgroundTasks,
) -> RenderCreateResponse:
    global _active_renders
    cap = _render_concurrent_cap()
    async with _active_renders_lock:
        if _active_renders >= cap:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Render concurrency cap reached ({cap}); retry later",
            )
        _active_renders += 1

    try:
        render_job = RenderJob(ratio=payload.ratio, payload=payload)
        await create_render_job(render_job)
    except Exception:
        async with _active_renders_lock:
            if _active_renders > 0:
                _active_renders -= 1
        raise

    background_tasks.add_task(_process_render_job, render_job.id, payload)
    return RenderCreateResponse(render_id=render_job.id, status=render_job.status)


@router.get("/{render_id}", response_model=RenderStatusResponse)
async def get_render_status(render_id: str) -> RenderStatusResponse:
    render_job = await get_render_job(render_id)
    if render_job is None:
        raise HTTPException(404, f"Render job {render_id} not found")

    return RenderStatusResponse(
        render_id=render_job.id,
        status=render_job.status,
        progress=render_job.progress,
        output_url=_output_url(render_job.output_path),
        error=render_job.error,
    )
