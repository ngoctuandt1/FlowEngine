"""Media fetch endpoints for remote video URLs."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import platform
import shutil
import signal
import socket
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

import psutil
import yt_dlp  # imported for test compatibility (test monkeypatches yt_dlp.utils.DownloadError)
from fastapi import APIRouter, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, Field, field_validator


_ENABLED = os.environ.get("FLOW_MEDIA_FETCH_ENABLED", "0") == "1"
_DISABLED_MESSAGE = (
    "endpoint disabled; set FLOW_MEDIA_FETCH_ENABLED=1 to enable with caveats"
)


class _ValidationErrorAsBadRequestRoute(APIRoute):
    def get_route_handler(self) -> Any:
        original_route_handler = super().get_route_handler()

        async def custom_route_handler(request: Request) -> Any:
            if (
                not _ENABLED
                and request.method == "POST"
                and request.url.path.endswith("/fetch-url")
            ):
                raise HTTPException(status_code=410, detail=_DISABLED_MESSAGE)
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
PROBE_TIMEOUT_SECONDS = 30
_ERROR_MESSAGE = "Failed to fetch media from source URL"
_YTDLP_BIN_ENV = "FLOW_YTDLP_BIN"


def _resolve_download_dir() -> Path:
    return Path(os.environ.get("FLOW_DOWNLOAD_DIR", "./downloads")).expanduser().resolve()


def _resolve_ytdlp_binary() -> str:
    """Locate the yt-dlp executable.

    Override path with ``FLOW_YTDLP_BIN`` for tests. Falls back to whatever
    ``shutil.which`` finds on PATH; raises if absent so the route surfaces
    a 502 instead of a stale ``FileNotFoundError`` from the event loop.
    """
    override = os.environ.get(_YTDLP_BIN_ENV)
    if override:
        return override
    found = shutil.which("yt-dlp")
    if not found:
        raise RuntimeError("yt-dlp executable not found on PATH")
    return found


def _is_forbidden_ip(ip_text: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_text)
    except ValueError:
        return True
    # ``is_global`` is the strict allow-list. ``is_private`` misses
    # CGNAT 100.64/10 (RFC 6598) which can still reach ISP-internal hosts.
    if not ip.is_global:
        return True
    if ip.is_multicast:
        return True
    return False


def _validate_hostname_resolution(host: str) -> None:
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


def _validate_url_for_ytdlp(url: str) -> None:
    """Apply the same SSRF rules as the public API to a URL yt-dlp wants
    to fetch (initial page, manifest, fragment, or final media)."""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError(f"yt-dlp target uses disallowed scheme: {parsed.scheme!r}")
    host = (parsed.hostname or "").strip().lower()
    if not host:
        raise ValueError("yt-dlp target is missing a host")
    if host == "localhost" or host == "internal" or host.endswith(".internal"):
        raise ValueError("yt-dlp target host is not allowed")
    try:
        parsed_ip = ipaddress.ip_address(host)
    except ValueError:
        _validate_hostname_resolution(host)
    else:
        if _is_forbidden_ip(str(parsed_ip)):
            raise ValueError("yt-dlp target host is not allowed")


def _collect_candidate_urls(info: Any) -> list[str]:
    """Walk an info_dict for every URL yt-dlp might fetch."""
    found: list[str] = []
    seen: set[str] = set()

    def _push(value: Any) -> None:
        if not isinstance(value, str) or not value:
            return
        if value in seen:
            return
        seen.add(value)
        found.append(value)

    def _walk(node: Any) -> None:
        if isinstance(node, dict):
            for key in ("url", "manifest_url", "fragment_base_url", "webpage_url"):
                _push(node.get(key))
            for nested_key in ("formats", "requested_formats", "fragments", "entries"):
                nested = node.get(nested_key)
                if isinstance(nested, list):
                    for item in nested:
                        _walk(item)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(info)
    return found


def _kill_tree(proc: asyncio.subprocess.Process, timeout_sec: float = 2.0) -> None:
    """Kill the subprocess and every descendant in its process tree.

    POSIX: ``start_new_session=True`` made the child the leader of a new
    process group, so ``killpg`` reaches yt-dlp plus any ffmpeg/aria2c it
    spawned without touching unrelated server children.

    Windows: ``CREATE_NEW_PROCESS_GROUP`` does not make ``terminate()`` or
    ``kill()`` reap descendants, so signal the parent before enumerating
    children to prevent late respawns, then escalate every survivor.
    """
    if proc.returncode is not None:
        return
    if platform.system() == "Windows":
        try:
            proc.terminate()
        except (ProcessLookupError, OSError):
            pass

        descendants: list[psutil.Process] = []
        try:
            descendants = psutil.Process(proc.pid).children(recursive=True)
        except psutil.Error:
            pass

        for child in descendants:
            try:
                child.terminate()
            except psutil.Error:
                pass

        _, alive = (
            psutil.wait_procs(descendants, timeout=timeout_sec)
            if descendants
            else ([], [])
        )
        for child in alive:
            try:
                child.kill()
            except psutil.Error:
                pass

        try:
            proc.kill()
        except (ProcessLookupError, OSError):
            pass
        return

    sigterm = getattr(signal, "SIGTERM", 15)
    # ``signal.SIGKILL`` only exists on POSIX; this branch is unreachable on
    # Windows (handled above), so resolve the constant lazily to keep the
    # module importable on Windows hosts (FlowEngine dev boxes).
    sigkill = getattr(signal, "SIGKILL", getattr(signal, "SIGTERM", 15))
    try:
        os.killpg(os.getpgid(proc.pid), sigterm)
        os.killpg(os.getpgid(proc.pid), sigkill)
    except (ProcessLookupError, PermissionError, OSError, AttributeError):
        # Fall back to direct kill; the child may have exited between the
        # ``returncode`` check and the ``killpg`` call.
        try:
            proc.terminate()
        except (ProcessLookupError, OSError):
            pass
        try:
            proc.kill()
        except (ProcessLookupError, OSError):
            pass


async def _run_ytdlp(
    args: list[str],
    timeout_seconds: int,
) -> tuple[int, bytes, bytes]:
    """Run yt-dlp as an isolated subprocess in its own process tree.

    Returns ``(returncode, stdout, stderr)``. Raises ``TimeoutError`` after
    killing the entire process tree.
    """
    binary = _resolve_ytdlp_binary()
    popen_kwargs: dict[str, Any] = {
        "stdout": asyncio.subprocess.PIPE,
        "stderr": asyncio.subprocess.PIPE,
        "stdin": asyncio.subprocess.DEVNULL,
    }
    if platform.system() == "Windows":
        # Windows lacks POSIX sessions; CREATE_NEW_PROCESS_GROUP lets us
        # signal the group via ``terminate``/``kill`` without affecting peers.
        import subprocess as _subprocess

        creation_flags = getattr(_subprocess, "CREATE_NEW_PROCESS_GROUP", None)
        if creation_flags is not None:
            popen_kwargs["creationflags"] = creation_flags
        else:
            popen_kwargs["start_new_session"] = True
    else:
        popen_kwargs["start_new_session"] = True

    proc = await asyncio.create_subprocess_exec(binary, *args, **popen_kwargs)
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_seconds)
    except asyncio.TimeoutError as exc:
        _kill_tree(proc)
        # Drain the pipes so ``proc`` releases its fds.
        try:
            await asyncio.wait_for(proc.wait(), timeout=2.0)
        except asyncio.TimeoutError:
            pass
        raise TimeoutError(f"yt-dlp timed out after {timeout_seconds} seconds") from exc
    assert proc.returncode is not None
    return proc.returncode, stdout, stderr


async def _probe_and_collect_urls(url: str, max_height: int) -> dict[str, Any]:
    """Pass 1: ``yt-dlp -j`` (simulate + dump JSON). Returns parsed info_dict.

    Validates every URL the info_dict references before pass-2 runs.
    """
    format_selector = (
        f"bestvideo[height<=?{max_height}]+bestaudio/best[height<=?{max_height}]"
    )
    args = [
        "--no-warnings",
        "--no-playlist",
        "--simulate",
        "--dump-single-json",
        "--no-call-home",
        "-f",
        format_selector,
        url,
    ]
    rc, stdout, stderr = await _run_ytdlp(args, PROBE_TIMEOUT_SECONDS)
    if rc != 0:
        # Surface as DownloadError so the route maps it to a sanitized 502.
        raise yt_dlp.utils.DownloadError(
            f"yt-dlp probe failed (rc={rc}): {stderr.decode('utf-8', 'replace')[:200]}"
        )
    try:
        info = json.loads(stdout.decode("utf-8", "replace"))
    except json.JSONDecodeError as exc:
        raise yt_dlp.utils.DownloadError(f"yt-dlp probe returned non-JSON: {exc}") from exc

    for candidate in _collect_candidate_urls(info):
        _validate_url_for_ytdlp(candidate)
    return info


async def _download_validated(url: str, max_height: int, output_path: Path) -> None:
    """Pass 2: actual download. URL hosts re-validated immediately prior."""
    _validate_url_for_ytdlp(url)
    format_selector = (
        f"bestvideo[height<=?{max_height}]+bestaudio/best[height<=?{max_height}]"
    )
    args = [
        "--no-warnings",
        "--no-playlist",
        "--quiet",
        "--no-call-home",
        "--merge-output-format",
        "mp4",
        "-f",
        format_selector,
        "-o",
        str(output_path),
        url,
    ]
    rc, _stdout, stderr = await _run_ytdlp(args, DOWNLOAD_TIMEOUT_SECONDS)
    if rc != 0:
        raise yt_dlp.utils.DownloadError(
            f"yt-dlp download failed (rc={rc}): {stderr.decode('utf-8', 'replace')[:200]}"
        )


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
    if not _ENABLED:
        raise HTTPException(status_code=410, detail=_DISABLED_MESSAGE)

    output_path = _build_output_path()

    try:
        info = await _probe_and_collect_urls(request.url, request.max_height)
        await _download_validated(request.url, request.max_height, output_path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except TimeoutError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except yt_dlp.utils.DownloadError:
        raise HTTPException(status_code=502, detail=_ERROR_MESSAGE)
    except RuntimeError as exc:
        # yt-dlp binary missing → operational failure, not user error.
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception:
        raise HTTPException(status_code=502, detail=_ERROR_MESSAGE)

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
