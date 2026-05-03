"""Worker interaction endpoints."""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel

from server.auth import require_worker_token
from server.models.job import JobUpdate
from server.db.job_store import (
    claim_next_job,
    claim_specific_pending_job,
    get_job,
    update_job,
)
from server.routes.ws import broadcast_job_update


# Bearer-token auth on every worker endpoint. Default API_KEY=dev-key
# keeps local development unauth'd; production deploys MUST set a strong
# API_KEY (the auth helper warns once at startup if the default is in use).
router = APIRouter(
    prefix="/api/worker",
    tags=["worker"],
    dependencies=[Depends(require_worker_token)],
)


# -- Request bodies ------------------------------------------------------------

class ClaimRequest(BaseModel):
    worker_id: str
    profiles: list[str]   # Chrome profiles this worker can use


class HeartbeatRequest(BaseModel):
    worker_id: str


class ClaimByIdRequest(BaseModel):
    worker_id: str
    job_id: str
    profile: str | None = None


# -- In-memory worker tracker --------------------------------------------------
# Maps worker_id -> last heartbeat datetime.
# A proper implementation would persist this, but for now memory is fine.
_workers: dict[str, datetime] = {}


# -- Endpoints -----------------------------------------------------------------

@router.post("/claim")
async def claim_job(req: ClaimRequest):
    """Claim the next available job matching one of the worker's profiles.

    Returns the claimed Job, or 204 No Content if nothing is available.
    """
    job = await claim_next_job(req.worker_id, req.profiles)
    if job is None:
        return Response(status_code=204)

    _workers[req.worker_id] = datetime.now(UTC)
    await broadcast_job_update(job)
    return job


@router.post("/claim-by-id")
async def claim_specific(req: ClaimByIdRequest):
    """Claim a specific pending job by id (batch sibling claim, PRD Phase 1).

    Returns 204 if the job is no longer pending (already claimed by
    another worker, cancelled, etc.). Returns the Job on success.
    """
    job = await claim_specific_pending_job(
        req.worker_id, req.job_id, profile=req.profile,
    )
    if job is None:
        return Response(status_code=204)
    _workers[req.worker_id] = datetime.now(UTC)
    await broadcast_job_update(job)
    return job


@router.put("/jobs/{job_id}")
async def update_job_status(job_id: str, body: JobUpdate):
    """Worker pushes results / status changes for a job it owns."""
    job = await get_job(job_id)
    if job is None:
        raise HTTPException(404, f"Job {job_id} not found")

    updated = await update_job(job_id, body)
    await broadcast_job_update(updated)
    return updated


@router.post("/heartbeat")
async def heartbeat(req: HeartbeatRequest):
    """Worker pings to say it's alive."""
    _workers[req.worker_id] = datetime.now(UTC)
    return {"status": "ok", "worker_id": req.worker_id}


@router.get("/workers")
async def list_workers():
    """List known workers and their last heartbeat (bonus endpoint)."""
    return {
        wid: ts.isoformat()
        for wid, ts in _workers.items()
    }
