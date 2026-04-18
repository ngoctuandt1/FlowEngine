"""Job management endpoints."""

import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from server.models.chain import Chain
from server.models.job import Job, JobCreate, JobStatus, ChainCreate
from server.db.chain_store import create_chain, get_chain_aggregate
from server.db.job_store import create_job, get_job, list_jobs, get_children, delete_job, get_job_counts, recover_stale_jobs
from server.routes.ws import broadcast_job_update

router = APIRouter(prefix="/api", tags=["jobs"])


# -- Helpers -------------------------------------------------------------------

def _build_job(req: JobCreate, *, profile: Optional[str] = None,
               chain_id: Optional[str] = None, job_level: int = 1) -> Job:
    """Construct a Job record from a creation request."""
    return Job(
        type=req.type,
        prompt=req.prompt,
        model=req.model,
        aspect_ratio=req.aspect_ratio,
        parent_job_id=req.parent_job_id,
        chain_id=chain_id or req.chain_id,
        project_url=req.project_url,
        media_id=req.media_id,
        bbox=req.bbox,
        direction=req.direction,
        profile=profile,
        job_level=job_level,
    )


# -- Endpoints -----------------------------------------------------------------

@router.post("/jobs", status_code=201)
async def create_single_job(req: JobCreate):
    """Create a single job.

    If parent_job_id is given, auto-set job_level = parent.job_level + 1
    and inherit profile / project_url / media_id from the completed parent.
    """
    job_level = 1
    profile = None

    if req.parent_job_id:
        parent = await get_job(req.parent_job_id)
        if parent is None:
            raise HTTPException(404, f"Parent job {req.parent_job_id} not found")
        job_level = parent.job_level + 1
        # Inherit context from completed parent
        if parent.status == JobStatus.COMPLETED:
            profile = parent.profile
            if req.project_url is None:
                req.project_url = parent.project_url
            if req.media_id is None:
                req.media_id = parent.media_id

    job = _build_job(req, profile=profile, job_level=job_level)
    await create_job(job)
    await broadcast_job_update(job)
    return job


@router.post("/chains", status_code=201)
async def create_chain_endpoint(req: ChainCreate):  # POST /api/chains
    """Create a chain of linked jobs.

    All jobs share the same chain_id. Each subsequent job in the list
    becomes a child of the previous one (job_level increments).

    B4: also INSERT a row into the `chains` table (immutable metadata).
    Aggregated status is computed on read — never synced from job updates.
    """
    chain = Chain(id=str(uuid.uuid4()), profile=req.profile)
    await create_chain(chain)

    jobs: list[Job] = []
    prev_id: Optional[str] = None
    level = 1

    for step in req.jobs:
        step.parent_job_id = prev_id
        step.chain_id = chain.id
        job = _build_job(step, profile=req.profile, chain_id=chain.id, job_level=level)
        await create_job(job)
        jobs.append(job)
        prev_id = job.id
        level += 1

    await broadcast_job_update(jobs[0])  # notify once for chain head
    return {"chain_id": chain.id, "jobs": jobs}


@router.get("/chains/{chain_id}")
async def get_chain(chain_id: str):
    """Return chain metadata + aggregated status + job ids (B4)."""
    aggregate = await get_chain_aggregate(chain_id)
    if aggregate is None:
        raise HTTPException(404, f"Chain {chain_id} not found")
    return aggregate


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
