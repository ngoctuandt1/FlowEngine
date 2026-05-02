"""Job management endpoints."""

import logging
import uuid
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query, status

from server.models.chain import Chain, ChainCreateResponse
from server.db.chain_store import compute_aggregated_status, create_chain
from server.models.job import (
    ChainCreate,
    Job,
    JobCreate,
    JobRelatedResponse,
    JobStatus,
    JobWithThumb,
)
from server.db.job_store import (
    create_job,
    delete_job,
    get_children,
    get_job,
    get_jobs_by_chain,
    get_job_counts,
    get_related_jobs,
    list_jobs,
    recover_stale_jobs,
)
from server.routes.ws import broadcast_job_update

router = APIRouter(prefix="/api", tags=["jobs"])
logger = logging.getLogger(__name__)

VIDEO_EXTENSIONS = frozenset({"mp4", "webm", "mov", "m4v"})
IMAGE_EXTENSIONS = frozenset({"png", "jpg", "jpeg", "webp", "gif", "bmp"})


def _resolve_model(req: JobCreate) -> str:
    """Apply per-type default models while preserving explicit requests."""
    if req.type.value == "text-to-image" and req.model == "veo-3.1-fast-lp":
        return "nano-banana-pro"
    return req.model


# -- Helpers -------------------------------------------------------------------

def validate_job_create(req: JobCreate) -> None:
    """Reserved for route-level job validation that Pydantic does not cover."""


def _build_job(req: JobCreate, *, profile: Optional[str] = None,
               chain_id: Optional[str] = None, job_level: int = 1) -> Job:
    """Construct a Job record from a creation request."""
    return Job(
        type=req.type,
        prompt=req.prompt,
        model=_resolve_model(req),
        aspect_ratio=req.aspect_ratio,
        parent_job_id=req.parent_job_id,
        chain_id=chain_id or req.chain_id,
        project_url=req.project_url,
        media_id=req.media_id,
        bbox=req.bbox,
        direction=req.direction,
        start_image_path=req.start_image_path,
        end_image_path=req.end_image_path,
        ingredient_image_paths=req.ingredient_image_paths,
        ref_image_path=req.ref_image_path,
        profile=profile,
        job_level=job_level,
    )


def _resolve_parent_chain_id(parent: Job) -> str:
    """Return the canonical chain id for a child of `parent`."""
    return parent.chain_id or parent.id


def _output_media_url(path: str) -> str:
    normalized = str(path or "").replace("\\", "/").strip()
    if not normalized:
        return ""
    if normalized.lower().startswith(("http://", "https://")):
        return normalized
    if normalized.lower().startswith("/downloads/"):
        relative = normalized[len("/downloads/"):]
    elif normalized.lower().startswith("downloads/"):
        relative = normalized[len("downloads/"):]
    else:
        marker = normalized.lower().rfind("/downloads/")
        relative = normalized[marker + len("/downloads/"):] if marker != -1 else normalized
    return f"/downloads/{quote(relative, safe='/')}"


def _renderable_files(job: Job) -> list[dict[str, str]]:
    files = []
    for raw_path in job.output_files:
        normalized = str(raw_path or "").replace("\\", "/")
        filename = normalized.split("/")[-1] or normalized
        extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        if extension in VIDEO_EXTENSIONS:
            kind = "video"
        elif extension in IMAGE_EXTENSIONS:
            kind = "image"
        else:
            continue
        files.append({
            "kind": kind,
            "url": _output_media_url(raw_path),
        })
    return files


def _thumb_url(job: Job) -> Optional[str]:
    media_id = (job.media_id or "").strip()
    if not media_id:
        return None

    files = _renderable_files(job)
    if not files:
        return None

    primary = files[0]
    if primary["kind"] == "image":
        return primary["url"] or None

    poster = next((item["url"] for item in files if item["kind"] == "image"), "")
    return poster or primary["url"] or None


def _job_with_thumb(job: Optional[Job]) -> Optional[JobWithThumb]:
    if job is None:
        return None
    return JobWithThumb(**job.model_dump(mode="json"), thumb_url=_thumb_url(job))


def _pick_chain_root(jobs: list[Job]) -> Optional[Job]:
    return next((job for job in jobs if job.job_level == 1), jobs[0] if jobs else None)


def _build_chain_edges(jobs: list[Job]) -> list[dict[str, str]]:
    chain_job_ids = {job.id for job in jobs}
    return [
        {"parent": job.parent_job_id, "child": job.id}
        for job in jobs
        if job.parent_job_id in chain_job_ids
    ]


def _build_chain_stats(jobs: list[Job]) -> dict[str, int]:
    total = len(jobs)
    completed = sum(1 for job in jobs if job.status == JobStatus.COMPLETED)
    failed = sum(1 for job in jobs if job.status == JobStatus.FAILED)
    running = sum(
        1
        for job in jobs
        if job.status in {JobStatus.CLAIMED, JobStatus.RUNNING}
    )
    return {
        "total": total,
        "completed": completed,
        "failed": failed,
        "pending": max(total - completed - failed - running, 0),
        "running": running,
    }


# -- Endpoints -----------------------------------------------------------------

@router.post("/jobs", status_code=201)
async def create_single_job(req: JobCreate):
    """Create a single job.

    If parent_job_id is given, auto-set job_level = parent.job_level + 1,
    always inherit chain metadata from the direct parent, and inherit
    runtime target fields from a completed parent.
    """
    job_level = 1
    profile = req.profile  # L1 pin (ignored when parent present, see below)
    chain_id = req.chain_id

    if req.parent_job_id:
        parent = await get_job(req.parent_job_id)
        if parent is None:
            raise HTTPException(404, f"Parent job {req.parent_job_id} not found")
        job_level = parent.job_level + 1
        chain_id = _resolve_parent_chain_id(parent)
        # L2+ inherits profile from completed parent — INV-1 account binding.
        # The request's `profile` hint is discarded to avoid accidental cross-
        # account routing on a chain.
        if parent.status == JobStatus.COMPLETED:
            profile = parent.profile
            if req.project_url is None:
                req.project_url = parent.project_url
            if req.media_id is None:
                req.media_id = parent.media_id

    validate_job_create(req)
    job = _build_job(req, profile=profile, chain_id=chain_id, job_level=job_level)
    if job.chain_id is None:
        job.chain_id = job.id
    await create_job(job)
    await broadcast_job_update(job)
    return job


@router.post("/chains", response_model=ChainCreateResponse, status_code=201)
async def create_chain_endpoint(req: ChainCreate) -> ChainCreateResponse:  # POST /api/chains
    """Create a chain of linked jobs.

    All jobs share the same chain_id. Each subsequent job in the list
    becomes a child of the previous one (job_level increments).

    B4: also INSERT a row into the `chains` table (immutable metadata).
    Aggregated status is computed on read — never synced from job updates.
    """
    try:
        effective_profile = req.effective_profile
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    chain = Chain(id=str(uuid.uuid4()), profile=effective_profile)
    await create_chain(chain)

    jobs: list[Job] = []
    prev_id: Optional[str] = None
    level = 1

    for step in req.jobs:
        validate_job_create(step)
        step.parent_job_id = prev_id
        step.chain_id = chain.id
        step.profile = effective_profile
        job = _build_job(step, profile=effective_profile, chain_id=chain.id, job_level=level)
        await create_job(job)
        jobs.append(job)
        prev_id = job.id
        level += 1

    await broadcast_job_update(jobs[0])  # notify once for chain head
    return {"chain_id": chain.id, "jobs": jobs}


@router.get("/chains/{chain_id}")
async def get_chain(chain_id: str):
    """Return one chain as a bulk DAG payload plus legacy aggregate fields."""
    jobs = await get_jobs_by_chain(chain_id)
    if not jobs:
        raise HTTPException(404, f"Chain {chain_id} not found")

    root_job = _pick_chain_root(jobs)
    stats = _build_chain_stats(jobs)
    latest_updated_at = max((job.updated_at for job in jobs), default=jobs[0].updated_at)
    profile = (
        (root_job.profile if root_job else None)
        or next((job.profile for job in jobs if job.profile), None)
    )

    return {
        "id": chain_id,
        "chain_id": chain_id,
        "profile": profile,
        "created_at": jobs[0].created_at.isoformat(),
        "updated_at": latest_updated_at.isoformat(),
        "status": compute_aggregated_status([job.status.value for job in jobs]),
        "progress": {
            "completed": stats["completed"],
            "total": stats["total"],
        },
        "root_prompt": (root_job.prompt if root_job else "") or "",
        "root_id": root_job.id if root_job else None,
        "jobs": [_job_with_thumb(job).model_dump(mode="json") for job in jobs],
        "edges": _build_chain_edges(jobs),
        "stats": stats,
    }


@router.get("/jobs/counts")
async def job_counts():
    """Return job counts grouped by status."""
    return await get_job_counts()


@router.post("/jobs/recover")
async def recover_stale():
    """Recover jobs stuck in claimed/running state.

    Resets stale jobs back to pending so they can be re-claimed.
    """
    recovered = await recover_stale_jobs()
    for job in recovered:
        await broadcast_job_update(job)
    return {
        "recovered": len(recovered),
        "jobs": [{"id": j.id, "type": j.type.value, "status": "pending"} for j in recovered],
    }


@router.get("/jobs")
async def list_all_jobs(
    status: Optional[str] = Query(None),
    type: Optional[str] = Query(None),
    profile: Optional[str] = Query(None),
    chain_id: Optional[str] = Query(None),
):
    """List jobs with optional filters."""
    filters = {}
    if status:
        filters["status"] = status
    if type:
        filters["type"] = type
    if profile:
        filters["profile"] = profile
    if chain_id:
        filters["chain_id"] = chain_id
    return await list_jobs(**filters)


@router.get("/jobs/{job_id}/related", response_model=JobRelatedResponse)
async def get_job_related(job_id: str) -> JobRelatedResponse:
    """Return one job plus parent/ancestor/sibling/child context."""
    try:
        related = await get_related_jobs(job_id)
        if related is None:
            raise HTTPException(404, f"Job {job_id} not found")

        chain_root_id = related["chain_root_id"]
        chain_id = related["chain_id"] or chain_root_id
        return JobRelatedResponse(
            self=_job_with_thumb(related["self"]),
            parent=_job_with_thumb(related["parent"]),
            ancestors=[_job_with_thumb(job) for job in related["ancestors"]],
            siblings=[_job_with_thumb(job) for job in related["siblings"]],
            children=[_job_with_thumb(job) for job in related["children"]],
            chain_id=chain_id,
            chain_root_id=chain_root_id,
            stats=related["stats"],
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception(
            "get_job_related failed job_id=%s exc_class=%s",
            job_id,
            exc.__class__.__name__,
        )
        raise HTTPException(500, "Failed to load related jobs") from exc


@router.get("/jobs/{job_id}")
async def get_single_job(job_id: str):
    """Get a single job by ID."""
    job = await get_job(job_id)
    if job is None:
        raise HTTPException(404, f"Job {job_id} not found")
    return job


@router.get("/jobs/{job_id}/children")
async def get_job_children(job_id: str):
    """Get child jobs of a given parent."""
    parent = await get_job(job_id)
    if parent is None:
        raise HTTPException(404, f"Job {job_id} not found")
    return await get_children(job_id)


@router.delete("/jobs/{job_id}")
async def cancel_job(job_id: str):
    """Cancel / delete a job.

    Running or claimed jobs are marked cancelled; pending jobs are deleted.
    """
    job = await get_job(job_id)
    if job is None:
        raise HTTPException(404, f"Job {job_id} not found")

    await delete_job(job_id)
    job.status = JobStatus.CANCELLED
    await broadcast_job_update(job)
    return {"deleted": job_id}
