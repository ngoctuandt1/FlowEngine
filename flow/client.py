"""FlowClient -- async Playwright wrapper for Google Flow browser automation.

Two launch modes:
  A) Native Chrome via CDP  (default on Windows, ``FLOW_REAL_CHROME=1``)
  B) Playwright persistent context (Docker / headless)

Usage::

    async with FlowClient("my_profile") as client:
        await client.page.goto("https://labs.google/fx/tools/flow")
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import platform
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from playwright.async_api import async_playwright, Browser, BrowserContext, Page

from flow.media_id import media_id_from_url, normalize_media_id, looks_like_media_id

logger = logging.getLogger(__name__)

_IS_WINDOWS = platform.system() == "Windows"

# ---------------------------------------------------------------------------
# Profile cloning
# ---------------------------------------------------------------------------

PROFILE_IGNORE_PATTERNS = [
    "Singleton*",
    "lockfile",
    "Cache",
    "Code Cache",
    "GPUCache",
    "Service Worker",
    "ShaderCache",
    "GrShaderCache",
    "GraphiteDawnCache",
    "DawnWebGPUCache",
    "DawnGraphiteCache",
    "blob_storage",
    "Crashpad",
    "BrowserMetrics",
]

_LOCK_NAMES = ("SingletonLock", "SingletonSocket", "SingletonCookie", "lockfile")


def _clone_profile(src: Path, dst: Path) -> Path:
    """Copy a Chrome profile directory, skipping caches and lock files.

    Returns *dst* on success or *src* as fallback when the copy fails.
    """
    try:
        shutil.copytree(
            str(src),
            str(dst),
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns(*PROFILE_IGNORE_PATTERNS),
        )
        logger.info("Cloned profile %s -> %s", src.name, dst.name)
        return dst
    except Exception as exc:
        logger.warning("Profile clone failed (%s), using original: %s", src, exc)
        return src


def _cleanup_locks(profile_dir: Path) -> None:
    """Remove Chrome singleton / lock files so the profile can be reopened."""
    for name in _LOCK_NAMES:
        try:
            (Path(profile_dir) / name).unlink(missing_ok=True)
        except Exception:
            pass


def _fix_crash_state(profile_dir: Path) -> None:
    """Reset exit-type in Preferences so Chrome does not show the crash bar."""
    replacements = [
        ('"exit_type":"Crashed"', '"exit_type":"Normal"'),
        ('"exited_cleanly":false', '"exited_cleanly":true'),
    ]
    for fname in ("Default/Preferences", "Local State"):
        fpath = Path(profile_dir) / fname
        try:
            if not fpath.exists():
                continue
            text = fpath.read_text(encoding="utf-8", errors="replace")
            changed = False
            for old, new in replacements:
                if old in text:
                    text = text.replace(old, new)
                    changed = True
            if changed:
                fpath.write_text(text, encoding="utf-8")
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Chrome executable resolution
# ---------------------------------------------------------------------------

_CHROME_PATHS_WIN = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
]

_CHROME_PATHS_LINUX = [
    "/usr/bin/google-chrome",
    "/usr/bin/google-chrome-stable",
    "/usr/bin/chromium-browser",
    "/usr/bin/chromium",
]


def _find_chrome_executable() -> str:
    """Return the path to the system Chrome binary.

    Respects ``CHROME_PATH`` env var, then searches common install locations.
    """
    env = os.environ.get("CHROME_PATH", "").strip()
    if env and Path(env).is_file():
        return env

    candidates = _CHROME_PATHS_WIN if _IS_WINDOWS else _CHROME_PATHS_LINUX
    for p in candidates:
        if Path(p).is_file():
            return p

    # Last resort: hope it is on PATH.
    return "chrome" if _IS_WINDOWS else "google-chrome"


# ---------------------------------------------------------------------------
# FlowClient
# ---------------------------------------------------------------------------

class FlowClient:
    """Async Playwright client for Google Flow automation.

    Parameters
    ----------
    profile_name:
        Sub-directory name inside *profile_base_dir*.
    profile_base_dir:
        Parent folder that holds Chrome profile directories.
    headless:
        Launch Chromium in headless mode (useful in Docker).
    real_chrome:
        Use native Chrome subprocess + CDP instead of Playwright's
        built-in Chromium.  Defaults to ``True`` on Windows, ``False``
        otherwise.  Override with ``FLOW_REAL_CHROME`` env var.
    debug_port:
        TCP port for Chrome DevTools protocol when *real_chrome* is True.
    action_delay_ms:
        Playwright ``slow_mo`` value -- milliseconds between actions.
    download_dir:
        Directory for downloaded files.
    """

    def __init__(
        self,
        profile_name: str,
        profile_base_dir: str = "./chrome-profiles",
        headless: bool = False,
        real_chrome: bool | None = None,
        debug_port: int = 19300,
        action_delay_ms: int = 800,
        download_dir: str = "./downloads",
    ) -> None:
        self.profile_name = profile_name
        self.profile_path = Path(profile_base_dir).resolve() / profile_name
        self.headless = headless
        self.debug_port = debug_port
        self.action_delay_ms = max(0, min(5000, action_delay_ms))
        self.download_dir = Path(download_dir).resolve()

        # Resolve real-chrome mode: env > explicit arg > platform default
        env_rc = os.environ.get("FLOW_REAL_CHROME", "").strip().lower()
        if env_rc:
            self.real_chrome = env_rc in ("1", "true", "yes")
        elif real_chrome is not None:
            self.real_chrome = real_chrome
        else:
            in_docker = Path("/.dockerenv").exists() or os.environ.get(
                "IS_DOCKER", ""
            ).strip().lower() in ("1", "true")
            self.real_chrome = _IS_WINDOWS and not in_docker

        # Playwright objects -- populated by ``start()``.
        self.page: Page | None = None
        self.browser: Browser | None = None
        self.context: BrowserContext | None = None
        self._pw: Any = None  # Playwright instance
        self._chrome_proc: subprocess.Popen | None = None
        self._temp_profile: Path | None = None

        # Passive network capture buffers.
        self._video_urls: list[dict[str, Any]] = []
        self._calls: list[dict[str, Any]] = []
        self._media_id_events: list[dict[str, Any]] = []
        self._gen_id: str | None = None
        self._account_info: dict[str, Any] | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> "FlowClient":
        """Launch the browser and return *self*."""
        self.download_dir.mkdir(parents=True, exist_ok=True)

        self._pw = await async_playwright().start()

        if self.real_chrome:
            await self._start_cdp()
        else:
            await self._start_persistent()

        self._setup_network_hooks()
        logger.info(
            "FlowClient ready  profile=%s  real_chrome=%s  headless=%s",
            self.profile_name,
            self.real_chrome,
            self.headless,
        )
        return self

    async def stop(self) -> None:
        """Shut down the browser and clean up temporary profile."""
        # Close Playwright objects.
        try:
            if self.context and not self.real_chrome:
                await self.context.close()
        except Exception:
            pass

        try:
            if self.browser:
                await self.browser.close()
        except Exception:
            pass

        try:
            if self._pw:
                await self._pw.stop()
        except Exception:
            pass

        # Kill native Chrome process (CDP mode).
        self._terminate_chrome_proc()

        # Remove cloned temp profile.
        if self._temp_profile and self._temp_profile != self.profile_path:
            try:
                shutil.rmtree(str(self._temp_profile), ignore_errors=True)
                logger.debug("Removed temp profile %s", self._temp_profile)
            except Exception:
                pass

        self.page = None
        self.browser = None
        self.context = None
        self._pw = None

    async def __aenter__(self) -> "FlowClient":
        return await self.start()

    async def __aexit__(self, *args: Any) -> None:
        await self.stop()

    # ------------------------------------------------------------------
    # Mode A -- Native Chrome via CDP
    # ------------------------------------------------------------------

    async def _start_cdp(self) -> None:
        """Clone profile, launch chrome.exe, connect via CDP."""
        # Prepare profile.
        self._prepare_profile()

        # Launch Chrome subprocess.
        exe = _find_chrome_executable()
        cmd = [
            exe,
            f"--user-data-dir={self._temp_profile}",
            f"--remote-debugging-port={self.debug_port}",
            "--no-first-run",
            "--no-default-browser-check",
            "--new-window",
            f"--window-size=1920,1080",
        ]
        popen_kwargs: dict[str, Any] = {
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
        }
        if _IS_WINDOWS:
            popen_kwargs["creationflags"] = getattr(
                subprocess, "CREATE_NEW_PROCESS_GROUP", 0
            )

        logger.info("Launching Chrome CDP: port=%d  profile=%s", self.debug_port, self._temp_profile)
        self._chrome_proc = subprocess.Popen(cmd, **popen_kwargs)

        # Wait for the debug port to become available.
        if not await self._wait_for_port(self.debug_port, timeout_sec=20.0):
            raise RuntimeError(
                f"Chrome debug port {self.debug_port} not ready after 20 s"
            )

        # Connect Playwright over CDP.
        cdp_url = f"http://127.0.0.1:{self.debug_port}"
        self.browser = await self._pw.chromium.connect_over_cdp(cdp_url)

        contexts = self.browser.contexts
        if contexts:
            self.context = contexts[0]
        else:
            self.context = await self.browser.new_context(
                no_viewport=True, accept_downloads=True
            )

        # Pick primary page.
        pages = self.context.pages
        self.page = pages[0] if pages else await self.context.new_page()

    # ------------------------------------------------------------------
    # Mode B -- Playwright persistent context
    # ------------------------------------------------------------------

    async def _start_persistent(self) -> None:
        """Launch using Playwright's persistent-context API."""
        self._prepare_profile()

        args = [
            "--disable-blink-features=AutomationControlled",
            "--no-first-run",
            "--no-default-browser-check",
            "--window-size=1920,1080",
        ]

        in_docker = Path("/.dockerenv").exists() or os.environ.get(
            "IS_DOCKER", ""
        ).strip().lower() in ("1", "true")
        if in_docker:
            args.extend(["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"])

        self.context = await self._pw.chromium.launch_persistent_context(
            user_data_dir=str(self._temp_profile),
            channel="chrome",
            headless=self.headless,
            no_viewport=True,
            args=args,
            slow_mo=self.action_delay_ms,
            accept_downloads=True,
            ignore_default_args=["--enable-automation"],
            downloads_path=str(self.download_dir),
        )

        pages = self.context.pages
        self.page = pages[0] if pages else await self.context.new_page()

    # ------------------------------------------------------------------
    # Profile preparation
    # ------------------------------------------------------------------

    def _prepare_profile(self) -> None:
        """Clone the source profile (or use it directly) and clean lock files."""
        use_base = os.environ.get("FLOW_USE_BASE_PROFILE", "").strip().lower() in (
            "1", "true", "yes",
        )

        if use_base or not self.profile_path.exists():
            self._temp_profile = self.profile_path
            self.profile_path.mkdir(parents=True, exist_ok=True)
        else:
            import tempfile
            dst = Path(tempfile.gettempdir()) / f"flow_{self.profile_name}_{time.time_ns()}"
            self._temp_profile = _clone_profile(self.profile_path, dst)

        _cleanup_locks(self._temp_profile)
        _fix_crash_state(self._temp_profile)

    # ------------------------------------------------------------------
    # Network hooks (passive capture)
    # ------------------------------------------------------------------

    def _setup_network_hooks(self) -> None:
        """Bind ``page.on('response')`` to capture network traffic passively."""
        if self.page is None:
            return
        self.page.on("response", self._on_response)

    async def _on_response(self, response: Any) -> None:
        """Classify and store interesting network responses.

        Categories:
          - **Video URLs**: ``.mp4``, ``.webm``, ``video/``, ``mediaurlredirect``
          - **Account info**: ``/v1/credits`` responses
          - **API calls**: ``operations/`` and other tracked endpoints
          - **Media IDs**: ``name=`` params in redirect URLs
        """
        try:
            url: str = response.url
            status: int = response.status
            url_l = url.lower()

            # --- Record raw call ---
            call_entry: dict[str, Any] = {
                "url": url,
                "status": status,
                "method": response.request.method,
                "ts": time.time(),
            }
            # Try to capture JSON body for API endpoints.
            if status == 200 and (
                "operations/" in url_l
                or "/v1/credits" in url_l
                or "getmediaurlredirect" in url_l
            ):
                try:
                    call_entry["body"] = await response.json()
                except Exception:
                    try:
                        call_entry["body"] = await response.text()
                    except Exception:
                        pass

            self._calls.append(call_entry)
            if len(self._calls) > 500:
                self._calls = self._calls[-300:]

            # --- Media ID from URL ---
            mid = media_id_from_url(url)
            if mid and looks_like_media_id(normalize_media_id(mid)):
                self._record_media_id(mid, source="response_url", url=url)

            # --- Video URL capture ---
            is_video = (
                ".mp4" in url_l
                or ".webm" in url_l
                or ".mov" in url_l
                or "video/" in (response.headers.get("content-type", "")).lower()
                or (
                    "getmediaurlredirect" in url_l
                    and (
                        "mediaurltype=media_url_type_video" in url_l
                        or ".mp4" in url_l
                        or ".webm" in url_l
                    )
                )
            )
            if is_video and status == 200:
                if url not in [v["url"] for v in self._video_urls]:
                    self._video_urls.append({"url": url, "ts": time.time()})
                    logger.debug("Captured video URL: %s", url[:100])
                    if len(self._video_urls) > 400:
                        self._video_urls = self._video_urls[-250:]

            # --- Account / credits capture ---
            if "/v1/credits" in url_l and status == 200:
                body = call_entry.get("body")
                if isinstance(body, dict):
                    self._account_info = body
                    logger.debug("Captured account info: %s", body)

            # --- Generation ID capture ---
            if "operations/" in url_l and status == 200:
                body = call_entry.get("body")
                if isinstance(body, dict):
                    name = body.get("name", "")
                    if name and not self._gen_id:
                        self._gen_id = str(name)
                        logger.debug("Captured gen_id: %s", self._gen_id)

        except Exception as exc:
            logger.debug("_on_response error: %s", exc)

    # ------------------------------------------------------------------
    # Capture helpers
    # ------------------------------------------------------------------

    def _record_media_id(
        self, mid: str, source: str = "", url: str = ""
    ) -> None:
        """De-duplicate and store a media-ID event."""
        n = normalize_media_id(mid)
        if not n or not looks_like_media_id(n):
            return
        # Skip if already recorded.
        for rec in reversed(self._media_id_events[-200:]):
            if rec.get("mid") == n:
                return
        self._media_id_events.append(
            {"mid": n, "source": source, "url": url, "ts": time.time()}
        )
        if len(self._media_id_events) > 600:
            self._media_id_events = self._media_id_events[-350:]

    def clear_captures(self) -> None:
        """Reset passive network buffers before a new operation."""
        self._video_urls.clear()
        self._calls.clear()
        self._media_id_events.clear()
        self._gen_id = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    async def _wait_for_port(port: int, timeout_sec: float = 20.0) -> bool:
        """Poll until a TCP port is accepting connections."""
        import socket

        deadline = time.monotonic() + timeout_sec
        while time.monotonic() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=1):
                    return True
            except OSError:
                await asyncio.sleep(0.4)
        return False

    def _terminate_chrome_proc(self) -> None:
        """Kill the native Chrome subprocess if it is still running."""
        proc = self._chrome_proc
        if proc is None:
            return

        try:
            alive = proc.poll() is None
        except Exception:
            alive = False

        if not alive:
            self._chrome_proc = None
            return

        pid = getattr(proc, "pid", 0)
        try:
            if _IS_WINDOWS and pid:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(pid)],
                    capture_output=True,
                    timeout=8,
                )
            else:
                proc.terminate()
                proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        finally:
            self._chrome_proc = None

    def __repr__(self) -> str:
        state = "connected" if self.page else "disconnected"
        mode = "cdp" if self.real_chrome else "persistent"
        return f"<FlowClient profile={self.profile_name!r} mode={mode} {state}>"
