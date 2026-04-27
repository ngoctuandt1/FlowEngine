"""Product ad pipeline orchestration endpoint."""

import os
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from server.models.job import ChainCreate, JobCreate, JobType
from server.routes.jobs import create_chain_endpoint


router = APIRouter(prefix="/api/product-pipeline", tags=["product"])


class ProductPipelineCreate(BaseModel):
    product_image_path: str
    brief: str
    profile: str | None = None
    aspect_ratio: str = "16:9"


def _get_upload_dir() -> Path:
    return Path(os.environ.get("FLOW_UPLOAD_DIR", "./uploads")).expanduser().resolve()


def _resolve_product_image_path(path_value: str) -> str:
    raw_text = str(path_value or "").strip()
    if not raw_text:
        raise HTTPException(400, "product_image_path is required")

    upload_dir = _get_upload_dir()
    raw_path = Path(raw_text).expanduser()

    if raw_path.is_absolute():
        resolved = raw_path.resolve()
    else:
        parts = list(raw_path.parts)
        if parts and parts[0].lower() == "uploads":
            parts = parts[1:]
        resolved = upload_dir.joinpath(*parts).resolve()

    if not resolved.is_relative_to(upload_dir):
        raise HTTPException(400, "product_image_path must resolve under FLOW_UPLOAD_DIR")

    return raw_text


def _validate_brief(brief: str) -> str:
    normalized = str(brief or "").strip()
    if not normalized:
        raise HTTPException(400, "brief must be between 1 and 500 characters")
    if len(normalized) > 500:
        raise HTTPException(400, "brief must be between 1 and 500 characters")
    return normalized


@router.post("/", status_code=201)
async def create_product_pipeline(req: ProductPipelineCreate):
    """Queue a frames-to-video job plus extend-video follow-up.

    Keep this as a pure video chain for now: Flow L1 image projects cannot be
    shared with the frames-to-video step, and the backend does not resolve
    placeholder outputs like "{step1_output}" across chain steps.
    """
    product_image_path = _resolve_product_image_path(req.product_image_path)
    brief = _validate_brief(req.brief)

    chain_req = ChainCreate(
        profile=req.profile,
        jobs=[
            JobCreate(
                type=JobType.FRAMES_TO_VIDEO,
                prompt=f"{brief}, smooth camera dolly-in",
                aspect_ratio=req.aspect_ratio,
                start_image_path=product_image_path,
            ),
            JobCreate(
                type=JobType.EXTEND_VIDEO,
                prompt=f"{brief}, dramatic reveal",
                aspect_ratio=req.aspect_ratio,
            ),
        ],
    )

    result = await create_chain_endpoint(chain_req)
    jobs = result["jobs"]
    return {
        "chain_id": result["chain_id"],
        "step_ids": [job.id for job in jobs],
    }
