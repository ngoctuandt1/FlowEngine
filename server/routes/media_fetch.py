"""Media fetch endpoints for remote video URLs."""

from __future__ import annotations

import contextlib
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
    try:
        ip = ipaddress.ip_address(ip_text)
    except ValueError:
        return True
    # `is_global` is a strict allow-list covering every special-use range:
    # RFC1918 private, loopback, link-local, CGNAT 100.64/10, documentation,
    # benchmarking, multicast, unspecified, reserved. Plain `is_private`
    # misses CGNAT — see RFC 6598.
    if not ip.is_global:
        return True
    if ip.is_multicast:
        return True
    return False


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


def _validate_url_for_ytdlp(url: str) -> None:
    """Apply the same SSRF rules used by the public API to a URL yt-dlp wants
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
    """Walk an info_dict for every URL yt-dlp might subsequently fetch.

    Covers: top-level `url`/`webpage_url`/`manifest_url`, every entry under
    `entries`, every `formats[].url` plus its `fragments[].url`, and
    `requested_formats[]`. Caller validates each against the SSRF allow-list
    before letting yt-dlp redownload with `download=True`.
    """
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


@contextlib.contextmanager
def _socket_ssrf_guard():
    """Patch socket.create_connection so every outgoing TCP connect is
    validated. yt-dlp may follow redirects or fetch HLS/DASH segments whose
    URLs were never seen by the pre-flight allow-list (an info_dict can omit
    server-driven redirects). This is the last line of defence.
    """
    original = socket.create_connection

    def guarded(address, *args, **kwargs):
        host = address[0] if isinstance(address, tuple) and address else None
        if isinstance(host, str) and host:
            stripped = host.strip().split("%", 1)[0]
            try:
                ip = ipaddress.ip_address(stripped)
            except ValueError:
                _validate_hostname_resolution(stripped)
            else:
                if _is_forbidden_ip(str(ip)):
                    raise ValueError("yt-dlp target host is not allowed")
        return original(address, *args, **kwargs)

    socket.create_connection = guarded  # type: ignore[assignment]
    try:
        yield
    finally:
        socket.create_connection = original  # type: ignore[assignment]


def _terminate_child_processes() -> None:
    """Best-effort kill of ffmpeg/aria2c grandchildren spawned by yt-dlp.

    yt-dlp does not expose a reliable handle to its children, and we
    deliberately avoid psutil here: it is an optional dependency that was
    missing from requirements.txt, and even when present it walks *all*
    descendants of the FastAPI worker — that nukes unrelated ffmpeg renders.

    Stdlib path: enumerate immediate children via /proc on POSIX, or fall
    back to a no-op on Windows. yt-dlp's own `cancel()` hook is the primary
    signal; this helper is the safety net only.
    """
    if os.name != "posix":
        return
    try:
        my_pid = os.getpid()
        proc_root = Path("/proc")
        if not proc_root.is_dir():
            return
        children: list[int] = []
        for entry in proc_root.iterdir():
            if not entry.name.isdigit():
                continue
            try:
                status = (entry / "status").read_text(encoding="ascii", errors="ignore")
            except OSError:
                continue
            for line in status.splitlines():
                if line.startswith("PPid:"):
                    try:
                        ppid = int(line.split()[1])
                    except (IndexError, ValueError):
                        ppid = -1
                    if ppid == my_pid:
                        children.append(int(entry.name))
                    break
    except Exception:  # pragma: no cover - defensive
        return

    import signal
    import time

    for pid in children:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            continue
    deadline = time.monotonic() + 2.0
    while children and time.monotonic() < deadline:
        children = [pid for pid in children if _pid_alive(pid)]
        if not children:
            break
        time.sleep(0.05)
    for pid in children:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            continue


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


def _probe_and_validate(url: str, options: dict[str, Any]) -> None:
    """First pass: ask yt-dlp to resolve the URL without downloading, then
    validate every candidate URL it intends to touch. Defeats the SSRF
    bypass where an attacker URL serves an HLS manifest pointing at
    169.254.169.254 or an internal IP.
    """
    probe_opts = dict(options)
    probe_opts["skip_download"] = True
    probe_opts["quiet"] = True
    probe_opts["no_warnings"] = True
    try:
        with yt_dlp.YoutubeDL(probe_opts) as probe:
            info = probe.extract_info(url, download=False)
    except yt_dlp.utils.DownloadError:
        raise
    candidates = _collect_candidate_urls(info)
    for candidate in candidates:
        _validate_url_for_ytdlp(candidate)


def _extract_info_with_timeout(url: str, options: dict[str, Any], timeout_seconds: int) -> dict[str, Any]:
    result: dict[str, Any] = {}
    error: dict[str, BaseException] = {}
    downloader_ref: dict[str, Any] = {}

    def run_download() -> None:
        try:
            # Pass 1: probe + validate every URL yt-dlp would touch.
            _probe_and_validate(url, options)
            # Pass 2: actual download under a socket-level SSRF guard so any
            # server-driven redirect or late-resolved fragment URL still
            # cannot reach a private IP.
            with _socket_ssrf_guard():
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
