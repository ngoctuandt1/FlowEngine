"""Media fetch endpoints for remote video URLs."""

from __future__ import annotations

import ipaddress
import os
import socket
import threading
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

import yt_dlp
from fastapi import APIRouter, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, Field, field_validator


class _ValidationErrorAsBadRequestRoute(APIRoute):
    def get_route_handler(self) -> Any:
        original_route_handler = super().get_route_handler()

        async def custom_route_handler(request: Request) -> Any:
            try:
                return await original_route_handler(request)
            except RequestValidationError as exc:
                first_error = exc.errors()[0]
                raise HTTPException(status_code=400, detail=first_error["msg"]) from exc

        return custom_route_handler


router = APIRouter(
    prefix="/api/media",
    tags=["media"],
    route_class=_ValidationErrorAsBadRequestRoute,
)

ALLOWED_MAX_HEIGHTS = {360, 480, 720, 1080}
DOWNLOAD_TIMEOUT_SECONDS = 60
_ERROR_MESSAGE = "Failed to fetch media from source URL"


def _resolve_download_dir() -> Path:
    return Path(os.environ.get("FLOW_DOWNLOAD_DIR", "./downloads")).expanduser().resolve()


def _is_forbidden_ip(ip_text: str) -> bool:
    ip = ipaddress.ip_address(ip_text)
    return any(
        (
            ip.is_private,
            ip.is_loopback,
            ip.is_link_local,
            ip.is_multicast,
            ip.is_reserved,
        )
    )


def _validate_hostname_resolution(host: str) -> None:
    # Validate every resolved address. yt-dlp re-resolves later so a DNS
    # rebinding TOCTOU window remains; we narrow it by rejecting any host
    # whose record set mixes public + private addresses (a common rebinding
    # tell) and by failing closed on an empty result.
    try:
        addrinfos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise ValueError("url host could not be resolved") from exc

    saw_address = False
    for family, *_rest, sockaddr in addrinfos:
        if family not in (socket.AF_INET, socket.AF_INET6):
            continue
        candidate_ip = sockaddr[0] if sockaddr else None
        if not isinstance(candidate_ip, str):
            continue
        candidate_ip = candidate_ip.split("%", 1)[0]
        saw_address = True
        if _is_forbidden_ip(candidate_ip):
            raise ValueError("url host is not allowed")
    if not saw_address:
        raise ValueError("url host could not be resolved")


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
        try:
            parsed_ip = ipaddress.ip_address(host)
        except ValueError:
            _validate_hostname_resolution(host)
        else:
            if _is_forbidden_ip(str(parsed_ip)):
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


def _terminate_child_processes() -> None:
    """Best-effort kill of ffmpeg/aria2c subprocesses yt-dlp may have spawned.

    yt-dlp does not expose a reliable handle to its child processes, so we
    walk this process's children via ``psutil`` when available. If psutil is
    absent we degrade silently — the worst case is the same orphaned-process
    behaviour we had before this change.
    """

    try:
        import psutil  # type: ignore[import-not-found]
    except ImportError:
        return
    try:
        current = psutil.Process()
        children = current.children(recursive=True)
    except Exception:  # pragma: no cover - defensive
        return
    for child in children:
        try:
            child.terminate()
        except Exception:  # pragma: no cover - defensive
            continue
    _gone, alive = psutil.wait_procs(children, timeout=2)
    for child in alive:
        try:
            child.kill()
        except Exception:  # pragma: no cover - defensive
            continue


def _extract_info_with_timeout(url: str, options: dict[str, Any], timeout_seconds: int) -> dict[str, Any]:
    result: dict[str, Any] = {}
    error: dict[str, BaseException] = {}
    downloader_ref: dict[str, Any] = {}

    def run_download() -> None:
        try:
            with yt_dlp.YoutubeDL(options) as downloader:
                downloader_ref["instance"] = downloader
                result["info"] = downloader.extract_info(url, download=True)
        except BaseException as exc:  # pragma: no cover - rethrown below
            error["exc"] = exc

    worker = threading.Thread(target=run_download, daemon=True)
    worker.start()
    worker.join(timeout_seconds)
    if worker.is_alive():
        # Best effort teardown: call yt-dlp's documented cancel hook, also try
        # interrupting any spawned ffmpeg/aria2c child processes so a CPU/disk
        # leak does not outlive the HTTP request.
        downloader = downloader_ref.get("instance")
        cancel = getattr(downloader, "cancel", None)
        if callable(cancel):
            try:
                cancel()
            except Exception:  # pragma: no cover - best-effort cleanup
                pass
        _terminate_child_processes()
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
    fetched_dir = _resolve_download_dir() / "fetched"
    fetched_dir.mkdir(parents=True, exist_ok=True)
    output_path = (fetched_dir / f"fetch_{uuid4()}.mp4").resolve()
    output_path.relative_to(fetched_dir.resolve())
    return output_path


@router.post("/fetch-url", response_model=FetchUrlResponse)
async def fetch_media_url(request: FetchUrlRequest) -> FetchUrlResponse:
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
        output_path=Path("fetched", output_path.name).as_posix(),
        title=selected_info.get("title"),
        duration_seconds=selected_info.get("duration"),
        source_url=source_url,
    )
