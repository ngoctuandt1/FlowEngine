"""Video merge endpoint backed by ffmpeg concat demuxer."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

def _resolve_dir(env_var: str, default: str) -> Path:
    raw_value = (os.environ.get(env_var) or "").strip() or default
    return Path(raw_value).expanduser().resolve()


router = APIRouter(prefix="/api/media", tags=["media"])

DOWNLOAD_DIR = _resolve_dir("FLOW_DOWNLOAD_DIR", "./downloads")
UPLOAD_DIR = _resolve_dir("FLOW_UPLOAD_DIR", "./uploads")
MERGE_DIR = (DOWNLOAD_DIR / "merges").resolve()
MAX_SOURCE_COUNT = 20
MIN_SOURCE_COUNT = 2
MAX_TOTAL_DURATION_SECONDS = 30 * 60
SUBPROCESS_TIMEOUT_SECONDS = 120


class MediaMergeRequest(BaseModel):
    input_paths: list[str]
    output_name: str | None = None


class MergeResponse(BaseModel):
    output_path: str
    duration_seconds: float
    source_count: int


def _relative_output_path(output_path: Path) -> str:
    return Path("merges", output_path.name).as_posix()


def _cleanup_partial_output(output_path: Path) -> None:
    try:
        output_path.unlink(missing_ok=True)
    except OSError:
        pass


def _strip_known_prefix(raw_path: str, expected_prefix: str) -> Path:
    path = Path(raw_path)
    if path.parts and path.parts[0].lower() == expected_prefix.lower():
        return Path(*path.parts[1:])
    return path


def _resolve_input_path(raw_path: str) -> Path:
    if not raw_path or not raw_path.strip():
        raise HTTPException(status_code=400, detail="input_paths entries must be non-empty")

    candidate = Path(raw_path).expanduser()
    allowed_roots = (DOWNLOAD_DIR.resolve(), UPLOAD_DIR.resolve())

    if candidate.is_absolute():
        resolved = candidate.resolve()
        if any(resolved.is_relative_to(root) for root in allowed_roots):
            return resolved
        raise HTTPException(status_code=400, detail=f"path escapes allowed roots: {raw_path}")

    prefixed_roots = {
        "downloads": DOWNLOAD_DIR.resolve(),
        "uploads": UPLOAD_DIR.resolve(),
    }
    first_segment = candidate.parts[0].lower() if candidate.parts else ""
    if first_segment in prefixed_roots:
        root = prefixed_roots[first_segment]
        resolved = (root / _strip_known_prefix(raw_path, first_segment)).resolve()
        if resolved.is_relative_to(root):
            return resolved
        raise HTTPException(status_code=400, detail=f"path escapes allowed roots: {raw_path}")

    matches: list[Path] = []
    for root in allowed_roots:
        resolved = (root / candidate).resolve()
        if resolved.is_relative_to(root):
            matches.append(resolved)
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        existing = [path for path in matches if path.exists()]
        if len(existing) == 1:
            return existing[0]
    raise HTTPException(status_code=400, detail=f"path must resolve under downloads/ or uploads/: {raw_path}")


def _probe_duration_seconds(path: Path) -> float:
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(status_code=504, detail=f"ffprobe timed out for {path.name}") from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"ffprobe unavailable: {exc}") from exc

    if result.returncode != 0:
        raise HTTPException(
            status_code=400,
            detail=f"ffprobe failed for {path.name}: {result.stderr.strip() or 'unknown error'}",
        )
    try:
        duration = float(result.stdout.strip())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"invalid ffprobe duration for {path.name}") from exc
    if duration < 0:
        raise HTTPException(status_code=400, detail=f"negative duration for {path.name}")
    return duration


def _validate_source_count(paths: list[str]) -> None:
    if not (MIN_SOURCE_COUNT <= len(paths) <= MAX_SOURCE_COUNT):
        raise HTTPException(
            status_code=400,
            detail=f"input_paths must contain between {MIN_SOURCE_COUNT} and {MAX_SOURCE_COUNT} items",
        )


def _concat_file_entry(path: Path) -> str:
    normalized = path.as_posix().replace("'", r"'\''")
    return f"file '{normalized}'\n"


@router.post("/merge", response_model=MergeResponse)
async def merge_media(request: MediaMergeRequest) -> MergeResponse:
    _validate_source_count(request.input_paths)

    resolved_inputs = [_resolve_input_path(raw_path) for raw_path in request.input_paths]
    for resolved_path in resolved_inputs:
        if not resolved_path.is_file():
            raise HTTPException(status_code=400, detail=f"input file not found: {resolved_path}")

    total_duration_seconds = 0.0
    if shutil.which("ffprobe"):
        total_duration_seconds = sum(_probe_duration_seconds(path) for path in resolved_inputs)
        if total_duration_seconds > MAX_TOTAL_DURATION_SECONDS:
            raise HTTPException(status_code=400, detail="total duration exceeds 30 minutes")

    MERGE_DIR.mkdir(parents=True, exist_ok=True)
    if request.output_name is not None and not request.output_name.strip():
        raise HTTPException(status_code=400, detail="output_name must be non-empty when provided")
    output_path = MERGE_DIR / f"merge_{uuid4()}.mp4"

    with tempfile.TemporaryDirectory(prefix="flowengine-merge-") as tmp_dir:
        file_list_path = Path(tmp_dir) / "filelist.txt"
        file_list_path.write_text(
            "".join(_concat_file_entry(path) for path in resolved_inputs),
            encoding="utf-8",
        )
        try:
            result = subprocess.run(
                [
                    "ffmpeg",
                    "-f",
                    "concat",
                    "-safe",
                    "0",
                    "-i",
                    str(file_list_path),
                    "-c",
                    "copy",
                    str(output_path),
                ],
                capture_output=True,
                text=True,
                timeout=SUBPROCESS_TIMEOUT_SECONDS,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            _cleanup_partial_output(output_path)
            raise HTTPException(status_code=504, detail="ffmpeg merge timed out") from exc
        except OSError as exc:
            _cleanup_partial_output(output_path)
            raise HTTPException(status_code=500, detail=f"ffmpeg unavailable: {exc}") from exc

    if result.returncode != 0:
        _cleanup_partial_output(output_path)
        raise HTTPException(
            status_code=500,
            detail=f"ffmpeg merge failed: {result.stderr.strip() or 'unknown error'}",
        )

    return MergeResponse(
        output_path=_relative_output_path(output_path),
        duration_seconds=total_duration_seconds,
        source_count=len(resolved_inputs),
    )
