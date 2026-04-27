"""Media fetch endpoints for remote video URLs."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

import yt_dlp
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, ValidationError, field_validator

from server.config import DATA_DIR


router = APIRouter(prefix="/api/media", tags=["media"])

ALLOWED_MAX_HEIGHTS = {360, 480, 720, 1080}
DOWNLOAD_TIMEOUT_SECONDS = 60
_ERROR_MESSAGE = "Failed to fetch media from source URL"


class FetchUrlRequest(BaseModel):
    url: str
    max_height: int = Field(default=1080)

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("url must use http or https")
        host = (parsed.hostname or "").strip().lower()
        if not host:
            raise ValueError("url host is required")
        if host == "localhost" or host == "internal" or host.endswith(".internal"):
            raise ValueError("url host is not allowed")
        if host.startswith("127.") or host.startswith("169.254."):
            raise ValueError("url host is not allowed")
        return value

    @field_validator("max_height")
    @classmethod
    def validate_max_height(cls, value: int) -> int:
        if value not in ALLOWED_MAX_HEIGHTS:
            raise ValueError("max_height must be one of 360, 480, 720, 1080")
        return value


class FetchUrlResponse(BaseModel):
    output_path: str
    title: str | None = None
    duration_seconds: int | None = None
    source_url: str


def _extract_info_with_timeout(url: str, options: dict[str, Any], timeout_seconds: int) -> dict[str, Any]:
    result: dict[str, Any] = {}
    error: dict[str, BaseException] = {}

    def run_download() -> None:
        try:
            with yt_dlp.YoutubeDL(options) as downloader:
                result["info"] = downloader.extract_info(url, download=True)
        except BaseException as exc:  # pragma: no cover - rethrown below
            error["exc"] = exc

    worker = threading.Thread(target=run_download, daemon=True)
    worker.start()
    worker.join(timeout_seconds)
    if worker.is_alive():
        raise TimeoutError(f"Download timed out after {timeout_seconds} seconds")
    if "exc" in error:
        raise error["exc"]
    return result["info"]


def _select_primary_info(info: dict[str, Any]) -> dict[str, Any]:
    entries = info.get("entries")
    if isinstance(entries, list):
        for entry in entries:
            if entry:
                return entry
    return info


def _build_output_path() -> Path:
    fetched_dir = DATA_DIR / "fetched"
    fetched_dir.mkdir(parents=True, exist_ok=True)
    output_path = (fetched_dir / f"fetch_{uuid4()}.mp4").resolve()
    output_path.relative_to(fetched_dir.resolve())
    return output_path


@router.post("/fetch-url", response_model=FetchUrlResponse)
async def fetch_media_url(payload: dict[str, Any]) -> FetchUrlResponse:
    try:
        request = FetchUrlRequest.model_validate(payload)
    except ValidationError as exc:
        first_error = exc.errors()[0]
        raise HTTPException(status_code=400, detail=first_error["msg"]) from exc

    output_path = _build_output_path()
    format_selector = (
        f"bestvideo[height<=?{request.max_height}]+bestaudio/"
        f"best[height<=?{request.max_height}]"
    )
    options = {
        "format": format_selector,
        "outtmpl": str(output_path),
        "merge_output_format": "mp4",
        "noplaylist": True,
        "socket_timeout": DOWNLOAD_TIMEOUT_SECONDS,
        "overwrites": True,
        "quiet": True,
        "no_warnings": True,
    }

    try:
        info = _extract_info_with_timeout(request.url, options, DOWNLOAD_TIMEOUT_SECONDS)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except TimeoutError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except yt_dlp.utils.DownloadError as exc:
        raise HTTPException(status_code=502, detail=_ERROR_MESSAGE) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=_ERROR_MESSAGE) from exc

    if not output_path.is_file():
        raise HTTPException(status_code=502, detail=_ERROR_MESSAGE)

    selected_info = _select_primary_info(info)
    source_url = (
        selected_info.get("webpage_url")
        or selected_info.get("original_url")
        or request.url
    )
    return FetchUrlResponse(
        output_path=str(output_path),
        title=selected_info.get("title"),
        duration_seconds=selected_info.get("duration"),
        source_url=source_url,
    )
