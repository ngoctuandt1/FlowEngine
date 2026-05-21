"""Job share-link API routes."""

from __future__ import annotations

import re
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Request

from server.db.share_store import (
    get_job_by_share_token,
    mint_job_share,
    revoke_job_share,
)
from server.models.share import JobShareResponse, PublicJobShareResponse, PublicSharedJob


router = APIRouter(prefix="/api", tags=["share"])

SHARE_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{16,256}$")


def _share_url_for_request(request: Request, token: str) -> str:
    return str(request.url_for("get_shared_job", share_token=token))


def _origin_for_url(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}".lower()


def _assert_same_origin_mutation(request: Request) -> None:
    from server import dashboard_auth

    if not dashboard_auth.DASHBOARD_AUTH_ENABLED:
        return

    request_origin = _origin_for_url(str(request.url))
    header_origin = request.headers.get("origin")
    if header_origin:
        if _origin_for_url(header_origin) != request_origin:
            raise HTTPException(status_code=403, detail="Cross-origin share mutation rejected")
        return

    referer = request.headers.get("referer")
    if referer and _origin_for_url(referer) == request_origin:
        return

    raise HTTPException(status_code=403, detail="Share mutation requires same-origin header")


@router.post("/jobs/{job_id}/share", response_model=JobShareResponse)
async def share_job(job_id: str, request: Request) -> JobShareResponse:
    _assert_same_origin_mutation(request)
    share = await mint_job_share(
        job_id,
        lambda token: _share_url_for_request(request, token),
    )
    if share is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    return JobShareResponse(**share.model_dump())


@router.delete("/jobs/{job_id}/share", response_model=JobShareResponse)
async def revoke_share_job(job_id: str, request: Request) -> JobShareResponse:
    _assert_same_origin_mutation(request)
    share = await revoke_job_share(job_id)
    if share is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    return JobShareResponse(**share.model_dump())


@router.get("/shares/{share_token}", response_model=PublicJobShareResponse)
async def get_shared_job(share_token: str) -> PublicJobShareResponse:
    if not SHARE_TOKEN_RE.fullmatch(share_token):
        raise HTTPException(status_code=404, detail="Shared job not found")

    result = await get_job_by_share_token(share_token)
    if result is None:
        raise HTTPException(status_code=404, detail="Shared job not found")

    job, share = result
    if share.share_url is None or share.shared_at is None:
        raise HTTPException(status_code=404, detail="Shared job not found")
    return PublicJobShareResponse(
        job=PublicSharedJob.model_validate(job.model_dump()),
        share_url=share.share_url,
        shared_at=share.shared_at,
    )
