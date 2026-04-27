"""Video re-targeting endpoint."""

import os
import subprocess
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from server.config import DATA_DIR
from server.db.job_store import create_job
from server.models.job import Job, JobType

router = APIRouter(prefix="/api/retarget", tags=["retarget"])


class RetargetRequest(BaseModel):
    """Request body for video re-targeting."""

    reference_video_path: str
    new_prompt: str = Field(min_length=1)
    profile: str | None = None
    aspect_ratio: str = "16:9"
    model: str = "veo-3.1-fast-lp"
    frame_seconds: float = Field(default=1.0, ge=0)


def _resolve_data_dir(env_var: str, default: str) -> Path:
    """Resolve a directory from env using the same contract as server.app."""
    raw_value = (os.environ.get(env_var) or "").strip() or default
    resolved = Path(raw_value).expanduser().resolve()
    if resolved.exists() and not resolved.is_dir():
        raise RuntimeError(f"{env_var} must point to a directory, got file: {resolved}")
    return resolved


def _is_within(path: Path, root: Path) -> bool:
    """Return True when path stays inside root after resolution."""
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _resolve_reference_video_path(raw_path: str) -> Path:
    """Accept only paths inside FLOW_DOWNLOAD_DIR or FLOW_UPLOAD_DIR."""
    candidate_raw = (raw_path or "").strip()
    if not candidate_raw:
        raise HTTPException(status_code=400, detail="reference_video_path is required")

    download_dir = _resolve_data_dir("FLOW_DOWNLOAD_DIR", "./downloads")
    upload_dir = _resolve_data_dir("FLOW_UPLOAD_DIR", "./uploads")
    raw = Path(candidate_raw)

    candidates: list[Path] = []
    if raw.is_absolute():
        candidates.append(raw.expanduser().resolve(strict=False))
    else:
        parts = raw.parts
        if parts:
            head = parts[0].lower()
            tail = Path(*parts[1:]) if len(parts) > 1 else Path()
            if head == "downloads":
                candidates.append((download_dir / tail).resolve(strict=False))
            elif head == "uploads":
                candidates.append((upload_dir / tail).resolve(strict=False))
        candidates.append((download_dir / raw).resolve(strict=False))
        candidates.append((upload_dir / raw).resolve(strict=False))

    for candidate in candidates:
        if _is_within(candidate, download_dir) or _is_within(candidate, upload_dir):
            return candidate

    raise HTTPException(
        status_code=400,
        detail="reference_video_path must stay within FLOW_DOWNLOAD_DIR or FLOW_UPLOAD_DIR",
    )


@router.post("")
async def create_retarget_job(req: RetargetRequest):
    """Extract a representative frame and queue a frames-to-video job."""
    reference_video = _resolve_reference_video_path(req.reference_video_path)

    retarget_dir = DATA_DIR / "retarget"
    retarget_dir.mkdir(parents=True, exist_ok=True)
    frame_path = retarget_dir / f"frame_{uuid.uuid4()}.jpg"

    try:
        subprocess.run(
            [
                "ffmpeg",
                "-ss",
                str(req.frame_seconds),
                "-i",
                str(reference_video),
                "-frames:v",
                "1",
                "-q:v",
                "2",
                str(frame_path),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        detail = getattr(exc, "stderr", None) or str(exc)
        raise HTTPException(status_code=500, detail=f"ffmpeg frame extraction failed: {detail}") from exc

    job = Job(
        type=JobType.FRAMES_TO_VIDEO,
        prompt=req.new_prompt,
        model=req.model,
        aspect_ratio=req.aspect_ratio,
        profile=req.profile,
        start_image_path=str(frame_path),
    )

    try:
        await create_job(job)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"job submission failed: {exc}") from exc

    return {
        "job_id": job.id,
        "frame_path": str(frame_path),
        "message": "Reference frame extracted and frames-to-video job queued.",
    }
