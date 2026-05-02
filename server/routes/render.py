"""Timeline render endpoints."""

from __future__ import annotations

import asyncio
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
    render_job = RenderJob(ratio=payload.ratio, payload=payload)
    await create_render_job(render_job)
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
