"""Job management endpoints."""

import logging
import uuid
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query, status

from server.models.chain import Chain, ChainCreateResponse
from server.db.chain_store import compute_aggregated_status
from server.models.job import (
    ChainCreate,
    Job,
    JobCreate,
    JobRelatedResponse,
    JobStatus,
    JobWithThumb,
)
from server.db.job_store import (
    create_chain_with_jobs,
    create_job,
    delete_job,
    get_children,
    get_job,
    get_jobs_by_chain,
    get_job_counts,
    get_related_jobs,
    list_jobs,
    list_pending_l1_siblings,
    list_pending_l2_siblings,
    list_pending_l3_siblings,
    recover_stale_jobs,
)
from server.routes.ws import broadcast_job_update

router = APIRouter(prefix="/api", tags=["jobs"])
logger = logging.getLogger(__name__)

VIDEO_EXTENSIONS = frozenset({"mp4", "webm", "mov", "m4v"})
IMAGE_EXTENSIONS = frozenset({"png", "jpg", "jpeg", "webp", "gif", "bmp"})
CHAIN_PROJECT_ID_MISMATCH_ERROR = (
    "All chain steps must use the same project_id as the first step when provided."
)
EXTEND_TERMINAL_BLOCKERS = {"camera-move", "insert-object", "remove-object"}


def _job_type_value(job_type: object) -> str:
    return str(getattr(job_type, "value", job_type))


def _chain_shape_error(
    *,
    child_type: object,
    child_index: int | None = None,
    ancestor_index: int | None = None,
    ancestor_job_id: str | None = None,
) -> str:
    child = (
        f"job[{child_index}] type={_job_type_value(child_type)}"
        if child_index is not None
        else f"submitted job type={_job_type_value(child_type)}"
    )
    ancestor = (
        f"job[{ancestor_index}]"
        if ancestor_index is not None
        else f"job {ancestor_job_id}"
    )
    return (
        f"chain shape invalid: {child} has an extend-video ancestor at "
        f"{ancestor}. Flow UI disables Camera/Insert/Remove on extend-output "
        f"clips (extend-child lockout, FLOW_BUTTON_EXACT §5.1; see memory "
        f"feedback_extend_terminal_op.md). Split this into a separate "
        f"L1-rooted chain or reverse the order (camera/insert/remove BEFORE "
        f"extend)."
    )


def _validate_chain_shape(jobs: list[JobCreate]) -> str | None:
    """Refuse extend-output children blocked by Flow UI submit controls."""
    if not jobs:
        return None

    def parent_index_for(job: JobCreate, index: int) -> int | None:
        explicit_parent = getattr(job, "parent_index", None)
        if explicit_parent is not None:
            return explicit_parent
        return index - 1 if index > 0 else None

    for index, job in enumerate(jobs):
        if _job_type_value(job.type) not in EXTEND_TERMINAL_BLOCKERS:
            continue

        parent_index = parent_index_for(job, index)
        while parent_index is not None and 0 <= parent_index < index:
            parent = jobs[parent_index]
            if _job_type_value(parent.type) == "extend-video":
                return _chain_shape_error(
                    child_type=job.type,
                    child_index=index,
                    ancestor_index=parent_index,
                )
            parent_index = parent_index_for(parent, parent_index)
    return None


async def _validate_existing_parent_chain_shape(
    req: JobCreate,
    parent: Job,
) -> str | None:
    """Refuse a new blocker job when existing parent lineage contains extend."""
    if _job_type_value(req.type) not in EXTEND_TERMINAL_BLOCKERS:
        return None

    seen: set[str] = set()
    ancestor: Job | None = parent
    while ancestor is not None and ancestor.id not in seen:
        seen.add(ancestor.id)
        if _job_type_value(ancestor.type) == "extend-video":
            return _chain_shape_error(
                child_type=req.type,
                ancestor_job_id=ancestor.id,
            )
        if not ancestor.parent_job_id:
            break
        ancestor = await get_job(ancestor.parent_job_id)

    return None


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
        project_id=req.project_id,
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
        err = await _validate_existing_parent_chain_shape(req, parent)
        if err:
            raise HTTPException(status_code=400, detail=err)
        job_level = parent.job_level + 1
        chain_id = _resolve_parent_chain_id(parent)
        # L2+ inherits profile from completed parent — INV-1 account binding.
        # The request's `profile` hint is discarded to avoid accidental cross-
        # account routing on a chain.
        if parent.status == JobStatus.COMPLETED:
            profile = parent.profile
            req.project_id = parent.project_id
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

    err = _validate_chain_shape(req.jobs)
    if err:
        raise HTTPException(status_code=400, detail=err)

    chain = Chain(id=str(uuid.uuid4()), profile=effective_profile)

    jobs: list[Job] = []
    prev_id: Optional[str] = None
    level = 1
    effective_project_id = req.jobs[0].project_id

    for index, step in enumerate(req.jobs):
        if step.project_id is not None and step.project_id != effective_project_id:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"{CHAIN_PROJECT_ID_MISMATCH_ERROR} "
                    f"jobs[0]={effective_project_id!r}, jobs[{index}]={step.project_id!r}"
                ),
            )

    for step in req.jobs:
        validate_job_create(step)
        step.parent_job_id = prev_id
        step.chain_id = chain.id
        step.profile = effective_profile
        step.project_id = effective_project_id
        job = _build_job(step, profile=effective_profile, chain_id=chain.id, job_level=level)
        jobs.append(job)
        prev_id = job.id
        level += 1

    await create_chain_with_jobs(chain, jobs)

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
    limit: int = Query(200, ge=1, le=2000),
    offset: int = Query(0, ge=0),
):
    """List jobs with optional filters. Default limit 200, max 2000."""
    filters: dict = {"limit": limit, "offset": offset}
    if status:
        filters["status"] = status
    if type:
        filters["type"] = type
    if profile:
        filters["profile"] = profile
    if chain_id:
        filters["chain_id"] = chain_id
    return await list_jobs(**filters)


@router.get("/jobs/l1-siblings")
async def get_pending_l1_siblings(
    project_url: Optional[str] = Query(None),
    profile: Optional[str] = Query(None),
    limit: int = Query(5, ge=1, le=20),
):
    """List pending L1 t2v jobs eligible for batch dispatch.

    PRD §3.2 Phase 1. Worker uses this after claiming an L1 t2v to
    discover up to N-1 sibling jobs that can be batched into the same
    Chrome (same profile, same target project — or unbound when the
    project is about to be created).
    """
    return await list_pending_l1_siblings(
        project_url=project_url, profile=profile, limit=limit,
    )


@router.get("/jobs/l2-siblings")
async def get_pending_l2_siblings(
    parent_job_id: str = Query(..., min_length=1),
    profile: Optional[str] = Query(None),
    limit: int = Query(5, ge=1, le=20),
):
    """List pending L2 ops sharing one L1 parent — eligible for batch.

    PRD §4. Worker uses this after claiming an L2 op to fan out the
    remaining siblings into one Chrome.
    """
    return await list_pending_l2_siblings(
        parent_job_id=parent_job_id, profile=profile, limit=limit,
    )


@router.get("/jobs/l3-siblings")
async def get_pending_l3_siblings(
    parent_job_id: str = Query(..., min_length=1),
    profile: Optional[str] = Query(None),
    limit: int = Query(5, ge=1, le=20),
):
    """List pending L3+ ops sharing one direct parent — eligible for batch.

    PRD §5. Worker uses this after claiming an L3+ op to fan out the
    remaining siblings into one Chrome.
    """
    return await list_pending_l3_siblings(
        parent_job_id=parent_job_id, profile=profile, limit=limit,
    )


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
