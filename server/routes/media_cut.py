"""Video cut endpoint backed by ffmpeg stream copy."""

import os
import subprocess
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/media", tags=["media"])

DOWNLOAD_DIR = Path(os.environ.get("FLOW_DOWNLOAD_DIR", "./downloads")).expanduser().resolve()
UPLOAD_DIR = Path(os.environ.get("FLOW_UPLOAD_DIR", "./uploads")).expanduser().resolve()
CUTS_DIR = (DOWNLOAD_DIR / "cuts").resolve()
MAX_CUT_DURATION_SECONDS = 600
FFMPEG_TIMEOUT_SECONDS = 120


class MediaCutRequest(BaseModel):
    input_path: str
    start_seconds: float
    end_seconds: float


def _relative_output_path(output_path: Path) -> str:
    return Path("cuts", output_path.name).as_posix()


def _cleanup_partial_output(output_path: Path) -> None:
    try:
        output_path.unlink(missing_ok=True)
    except OSError:
        pass


def _resolve_input_path(input_path: str) -> Path:
    raw_text = str(input_path).strip()
    if not raw_text:
        raise HTTPException(status_code=400, detail="input_path is required")

    raw_path = Path(raw_text).expanduser()
    if raw_path.is_absolute():
        resolved = raw_path.resolve()
    else:
        parts = list(raw_path.parts)
        if parts and parts[0].lower() in {"downloads", "uploads"}:
            prefix = parts[0].lower()
            parts = parts[1:]
            base_dir = DOWNLOAD_DIR if prefix == "downloads" else UPLOAD_DIR
            resolved = base_dir.joinpath(*parts).resolve()
        else:
            resolved = DOWNLOAD_DIR.joinpath(*parts).resolve()

    if resolved.is_relative_to(DOWNLOAD_DIR) or resolved.is_relative_to(UPLOAD_DIR):
        return resolved

    raise HTTPException(
        status_code=400,
        detail=(
            "input_path must resolve under FLOW_DOWNLOAD_DIR or FLOW_UPLOAD_DIR"
        ),
    )


def _validate_range(start_seconds: float, end_seconds: float) -> float:
    if start_seconds < 0:
        raise HTTPException(status_code=400, detail="start_seconds must be >= 0")
    if start_seconds >= end_seconds:
        raise HTTPException(status_code=400, detail="start_seconds must be < end_seconds")

    duration_seconds = end_seconds - start_seconds
    if duration_seconds > MAX_CUT_DURATION_SECONDS:
        raise HTTPException(
            status_code=400,
            detail=f"Cut duration must be <= {MAX_CUT_DURATION_SECONDS} seconds",
        )
    return duration_seconds


@router.post("/cut")
async def cut_media(request: MediaCutRequest):
    """Create a new MP4 cut from an uploaded or downloaded source file."""
    input_path = _resolve_input_path(request.input_path)
    duration_seconds = _validate_range(request.start_seconds, request.end_seconds)

    CUTS_DIR.mkdir(parents=True, exist_ok=True)
    output_path = CUTS_DIR / f"cut_{uuid4()}.mp4"

    try:
        result = subprocess.run(
            [
                "ffmpeg",
                "-ss",
                str(request.start_seconds),
                "-to",
                str(request.end_seconds),
                "-i",
                str(input_path),
                "-c",
                "copy",
                str(output_path),
            ],
            capture_output=True,
            text=True,
            timeout=FFMPEG_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        _cleanup_partial_output(output_path)
        raise HTTPException(status_code=504, detail="ffmpeg cut timed out") from exc
    except OSError as exc:
        _cleanup_partial_output(output_path)
        raise HTTPException(status_code=500, detail=f"ffmpeg unavailable: {exc}") from exc

    if result.returncode != 0:
        _cleanup_partial_output(output_path)
        raise HTTPException(status_code=500, detail="ffmpeg failed to cut media")

    return {
        "output_path": _relative_output_path(output_path),
        "duration_seconds": duration_seconds,
    }
