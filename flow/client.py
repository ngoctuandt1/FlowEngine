"""FlowClient -- async Playwright wrapper for Google Flow browser automation.

Two launch modes:
  A) Native Chrome via CDP  (default on Windows, ``FLOW_REAL_CHROME=1``)
  B) Playwright persistent context (Docker / headless)

Usage::

    async with FlowClient("my_profile") as client:
        await client.page.goto("https://labs.google/fx/tools/flow")

Security note
-------------
Mode A is the only path that mimics a real-user Chrome launch closely
enough to avoid Google's automation fingerprinting: the Chrome process
starts under ``subprocess.Popen`` BEFORE Playwright attaches via
``connect_over_cdp``, so Playwright does not inject its bootstrap hooks
into page contexts. Mode B sets
``--disable-blink-features=AutomationControlled`` and
``ignore_default_args=["--enable-automation"]`` — the presence of those
flags is itself a detection signal, which is why Mode B is gated behind
Docker / non-Windows and should not be extended to Windows production.
Adding bot-hider flags, stealth patches, or pipe-mode CDP requires
explicit user approval. See ``docs/CHROME_LAUNCH_SECURITY.md`` and
memory ``feedback_chrome_launch_real_user.md``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import platform
import re
import signal
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from playwright.async_api import async_playwright, Browser, BrowserContext, Page

from flow.media_id import media_id_from_url, normalize_media_id, looks_like_media_id

logger = logging.getLogger(__name__)

_IS_WINDOWS = platform.system() == "Windows"

# ---- CDP port allocation ----
# Each FlowClient instance needs a unique debug port so concurrent Chrome
# processes on the same host don't conflict. allocate_cdp_port() picks the
# next free port in the configured range, falling back to OS-assigned.
_CDP_PORT_BASE = int(os.environ.get("FLOW_CDP_PORT_BASE", "19300"))
_CDP_PORT_RANGE = int(os.environ.get("FLOW_CDP_PORT_RANGE", "100"))
_cdp_port_lock = threading.Lock()
_cdp_port_counter = 0


def allocate_cdp_port() -> int:
    """Return an available TCP port for Chrome CDP, cycling through the range."""
    global _cdp_port_counter
    with _cdp_port_lock:
        for _ in range(_CDP_PORT_RANGE):
            port = _CDP_PORT_BASE + (_cdp_port_counter % _CDP_PORT_RANGE)
            _cdp_port_counter += 1
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                try:
                    s.bind(("127.0.0.1", port))
                    return port
                except OSError:
                    continue
        # All ports in range busy — let OS pick
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]

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


_DEFAULT_WINDOW_SIZE = "1920,1080"


def _parse_int_pair(raw: str, sep_chars: str, *, allow_zero: bool) -> tuple[int, int] | None:
    """Parse ``"A<sep>B"`` into two ints. Returns None on malformed input."""
    for sep in sep_chars:
        if sep in raw:
            parts = raw.split(sep, 1)
            break
    else:
        return None
    try:
        a, b = int(parts[0].strip()), int(parts[1].strip())
    except ValueError:
        return None
    lo = 0 if allow_zero else 1
    if a < lo or b < lo:
        return None
    return a, b


def _build_window_geometry_args() -> list[str]:
    """Resolve ``--window-size`` / ``--window-position`` from env vars.

    ``FLOW_WINDOW_SIZE=WxH`` (or ``W,H``) overrides the default 1920x1080.
    ``FLOW_WINDOW_POSITION=X,Y`` (or ``XxY``) adds a position flag; unset
    leaves Chrome's default placement. Malformed values are ignored with
    a warning — never crash the worker over a typo.
    """
    size_raw = os.environ.get("FLOW_WINDOW_SIZE", "").strip()
    size = _DEFAULT_WINDOW_SIZE
    if size_raw:
        parsed = _parse_int_pair(size_raw, "x,X", allow_zero=False)
        if parsed and parsed[0] >= 100 and parsed[1] >= 100:
            size = f"{parsed[0]},{parsed[1]}"
        else:
            logger.warning(
                "Ignoring invalid FLOW_WINDOW_SIZE=%r (expected WxH, both >= 100)",
                size_raw,
            )

    args = [f"--window-size={size}"]

    pos_raw = os.environ.get("FLOW_WINDOW_POSITION", "").strip()
    if pos_raw:
        parsed = _parse_int_pair(pos_raw, ",x", allow_zero=True)
        if parsed:
            args.append(f"--window-position={parsed[0]},{parsed[1]}")
        else:
            logger.warning(
                "Ignoring invalid FLOW_WINDOW_POSITION=%r (expected X,Y)",
                pos_raw,
            )
    return args


def _build_perf_args() -> list[str]:
    """CPU/memory hygiene flags always safe to apply, env-gated by
    ``FLOW_CHROME_PERF`` (default ``on`` — opt-out only).

    Zero-risk additions for an automation-only Chrome:

      * ``--mute-audio`` — Flow's editor decodes audio for video previews
        we never play; muting drops a renderer thread.
      * ``--no-pings`` — silences hyperlink ping beacons.
      * ``--disable-extensions`` / ``--disable-default-apps`` — clean
        profile, no extension event loops.
      * ``--disable-component-update`` — Chrome auto-update pings.
      * ``--disable-features=Translate,MediaRouter,GlobalMediaControls``
        — Flow doesn't use translation; MediaRouter/GMC drive Cast
        infrastructure we don't need.
      * ``--disable-background-timer-throttling=false`` is omitted on
        purpose — we WANT background tabs throttled when multi-tab.

    Set ``FLOW_CHROME_PERF=off`` to skip (e.g. when diagnosing).
    """
    mode = (os.environ.get("FLOW_CHROME_PERF", "on") or "on").strip().lower()
    if mode in ("0", "off", "false", "no"):
        return []
    return [
        "--mute-audio",
        "--no-pings",
        "--disable-extensions",
        "--disable-default-apps",
        "--disable-component-update",
        "--disable-features=Translate,MediaRouter,GlobalMediaControls,OptimizationHints",
        # Stop Flow's editor from auto-looping its 13+ camera-preset
        # and history thumbnail <video> tags. Worker drives by clicks
        # only — never watches playback — so requiring a user gesture
        # before any media plays drops the renderer's per-frame paint
        # loop and the audio-decode thread (which still wakes even
        # with --mute-audio because ``muted`` ≠ ``no decode``).
        "--autoplay-policy=user-gesture-required",
    ]


def _build_gpu_args() -> list[str]:
    """GPU / hardware-accel flags, env-gated.

    ``FLOW_CHROME_GPU`` (default off — keeps prior behaviour stable):

      ``off`` / unset            → no extra flags (legacy SwiftShader path)
      ``vaapi``                  → VAAPI HW video decode only. Lightest
                                   touch — leaves Chrome's GL choice to
                                   defaults. Best for CPU-loaded Xvfb
                                   hosts where the bottleneck is video
                                   element decode (13+ history clips
                                   on the Flow editor page).
      ``full`` / ``1`` / ``on``  → VAAPI + GPU rasterization + zero-copy
                                   + accelerated video decode. Lets
                                   Chrome pick the system GL backend
                                   (Intel iHD / Mesa via DRI). DOES NOT
                                   force ``--use-gl=angle`` — that
                                   indirection on Xvfb burned 277 %
                                   CPU on the gpu-process during a
                                   2026-05-05 test (ANGLE→Mesa SW
                                   double-emulation). Stay native.
      ``headless``               → ``--headless=new`` (no X server
                                   required). Lowest CPU but breaks
                                   any code that asserts on a visible
                                   window.

    Requires (linux): ``/dev/dri/renderD*`` present; Chrome user in
    the ``render`` group; ``libva2`` + Intel/Mesa VAAPI driver.

    Linux-only; on Windows / macOS Chrome already picks the system GPU
    by default and these flags would force-enable paths the platform
    blocks for security or compatibility.
    """
    if _IS_WINDOWS or platform.system() != "Linux":
        return []
    mode = (os.environ.get("FLOW_CHROME_GPU", "") or "").strip().lower()
    if mode in ("", "0", "off", "false", "no"):
        return []
    if mode == "headless":
        return ["--headless=new"]
    if mode in ("swiftshader", "sw", "soft"):
        # Magic combo for headless / Xvfb hosts without a real GPU
        # passthrough — forces ANGLE to deterministically use the
        # SwiftShader CPU rasterizer instead of probing for a hardware
        # GL surface (which Xvfb cannot provide and which sends the
        # gpu-process into a 250 %+ CPU spin-loop, measured 2026-05-05).
        # Documented in Chromium dev list / Intel community as the
        # only stable path on Xvfb.
        return ["--use-gl=angle", "--use-angle=swiftshader"]
    if mode in ("disable", "no-gpu"):
        # Xvfb can't expose DRM/GLX, so Chrome's gpu-process spin-loops
        # when it tries to negotiate a GPU surface — 250 %+ CPU on the
        # gpu-process even with VAAPI (measured 2026-05-05). Killing
        # the gpu-process entirely (renderer falls back to in-process
        # SwiftShader) reclaims that core. Pragmatic default for any
        # Xvfb host without DRM passthrough.
        return ["--disable-gpu"]
    if mode == "egl":
        # Direct EGL via GBM, bypassing X11. Talks to /dev/dri/renderD*
        # without needing an X server's GLX bridge — the only path that
        # actually engages the iGPU on a software X server like Xvfb.
        # Requires libgbm + libegl1 + driver (mesa-vulkan-drivers /
        # intel-media-va-driver) and Chrome user in the ``render`` group.
        return [
            "--ozone-platform=headless",
            "--use-gl=egl",
            "--enable-features=VaapiVideoDecoder",
            "--enable-gpu-rasterization",
        ]
    if mode == "vaapi":
        return [
            "--ignore-gpu-blocklist",
            "--enable-features=VaapiVideoDecoder",
            "--enable-accelerated-video-decode",
        ]
    # ``full`` / ``1`` / ``on``
    return [
        "--ignore-gpu-blocklist",
        "--enable-gpu-rasterization",
        "--enable-zero-copy",
        "--enable-features=VaapiVideoDecoder,VaapiVideoEncoder",
        "--enable-accelerated-video-decode",
    ]


def _apply_root_sandbox_guard(args: list[str]) -> list[str]:
    """Return launch args after enforcing the Linux root sandbox policy."""
    if _IS_WINDOWS or platform.system() != "Linux":
        return args

    geteuid = getattr(os, "geteuid", None)
    if geteuid is None or geteuid() != 0:
        return args

    if os.environ.get("FLOW_ALLOW_ROOT_NO_SANDBOX", "").strip() == "1":
        if "--no-sandbox" not in args:
            args.append("--no-sandbox")
        logger.warning(
            "Running Chrome as root with FLOW_ALLOW_ROOT_NO_SANDBOX=1 and "
            "--no-sandbox. Prefer running as a non-root user."
        )
        return args

    raise RuntimeError(
        "Refusing to launch Chrome as root without --no-sandbox. Either run "
        "the worker as a non-root user (recommended; see "
        "deploy/debian/README.md) or set FLOW_ALLOW_ROOT_NO_SANDBOX=1 to "
        "enable --no-sandbox automatically. Auto-adding --no-sandbox is "
        "gated to avoid silent security boundary changes."
    )


def _read_proc_comm(pid: int) -> str | None:
    """Return ``/proc/<pid>/comm`` when available."""
    try:
        return Path(f"/proc/{pid}/comm").read_text(
            encoding="utf-8", errors="replace"
        ).strip()
    except OSError:
        return None


def _read_proc_cmdline(pid: int) -> list[str] | None:
    """Return ``/proc/<pid>/cmdline`` split into argv entries."""
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return None
    return [part.decode("utf-8", errors="replace") for part in raw.split(b"\0") if part]


def _looks_like_owned_user_data_dir(profile_dir: Path | None) -> bool:
    """Restrict selective Chrome cleanup to FlowEngine-owned profiles."""
    if profile_dir is None:
        return False
    normalized = str(profile_dir).replace("\\", "/")
    return "/chrome-profiles/" in normalized or profile_dir.name.startswith("flow_")


def _cmdline_has_user_data_dir(cmdline: list[str] | None, profile_dir: Path | None) -> bool:
    """Return True when argv contains the expected ``--user-data-dir``."""
    if not cmdline or profile_dir is None:
        return False

    expected = str(profile_dir)
    if f"--user-data-dir={expected}" in cmdline:
        return True

    for index, arg in enumerate(cmdline[:-1]):
        if arg == "--user-data-dir" and cmdline[index + 1] == expected:
            return True
    return False


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
        # Per-instance CDP port: when multiple FlowClients run concurrently
        # (same-profile clone path or cross-profile pool), they MUST bind
        # different debug ports — otherwise the second Chrome silently
        # fails to bind / Playwright attaches to the wrong instance and
        # network events leak across operations. Pick a free port unless
        # the caller explicitly pinned one.
        if debug_port == 19300:
            try:
                import socket
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.bind(("127.0.0.1", 0))
                    self.debug_port = s.getsockname()[1]
            except OSError:
                self.debug_port = debug_port
        else:
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
        self._image_names: list[str] = []
        self._account_info: dict[str, Any] | None = None
        self._hooks_bound: bool = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def _clear_failure_cache(self) -> None:
        for attr in ("_last_failure_capture", "_last_failure_kind"):
            if hasattr(self, attr):
                delattr(self, attr)

    async def start(self) -> "FlowClient":
        """Launch the browser and return *self*."""
        self._clear_failure_cache()
        self.download_dir.mkdir(parents=True, exist_ok=True)

        self._pw = await async_playwright().start()

        if self.real_chrome:
            await self._start_cdp()
        else:
            await self._start_persistent()

        # Layer-1 guard: helpers bind hooks immediately after self.page is
        # assigned; this call is a no-op if they did, but keeps the old
        # contract for any future code path that sets page elsewhere.
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
        self._hooks_bound = False

    async def __aenter__(self) -> "FlowClient":
        return await self.start()

    async def __aexit__(self, *args: Any) -> None:
        await self.stop()

    # ------------------------------------------------------------------
    # Pool helpers — reuse one browser across multiple jobs
    # ------------------------------------------------------------------

    def is_healthy(self) -> bool:
        """Return True if browser + page are still usable.

        Used by BrowserPool to decide whether to reuse or restart the
        client between jobs. Cheap: no round-trip to Chrome.
        """
        if self.page is None or self.browser is None:
            return False
        try:
            if self.page.is_closed():
                return False
        except Exception:
            return False
        if self._chrome_proc is not None and self._chrome_proc.poll() is not None:
            return False
        return True

    async def reset_for_next_job(self, target_url: str | None = None) -> None:
        """Clear per-job state so the same client can run another job.

        Clears captured network buffers and optional navigation target.
        Does NOT close the browser. Caller typically passes the Flow
        homepage for L1 jobs; L2+ handlers navigate to the project/edit
        URL themselves so target_url is usually None for those.
        """
        self._clear_failure_cache()
        self._video_urls.clear()
        self._calls.clear()
        self._media_id_events.clear()
        self._gen_id = None
        self._image_names.clear()
        # Keep _account_info — it's a cached read of /v1/credits and
        # stays valid across jobs on the same session.

        if target_url and self.page is not None:
            try:
                await self.page.goto(
                    target_url, wait_until="domcontentloaded", timeout=30000
                )
            except Exception as exc:
                logger.warning(
                    "reset_for_next_job: goto %r failed: %s", target_url[:80], exc
                )
                raise

    # ------------------------------------------------------------------
    # Mode A -- Native Chrome via CDP
    # ------------------------------------------------------------------

    async def _start_cdp(self) -> None:
        """Clone profile, launch chrome.exe, connect via CDP."""
        self._clear_failure_cache()
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
            *_build_window_geometry_args(),
            *_build_gpu_args(),
            *_build_perf_args(),
        ]
        popen_kwargs: dict[str, Any] = {
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
        }
        if _IS_WINDOWS:
            popen_kwargs["creationflags"] = getattr(
                subprocess, "CREATE_NEW_PROCESS_GROUP", 0
            )
        else:
            # Put Chrome in its own session/process group so teardown can
            # reap zygote, renderer, and GPU children via killpg().
            popen_kwargs["start_new_session"] = True

        cmd = _apply_root_sandbox_guard(cmd)
        logger.info("Launching Chrome CDP: port=%d  profile=%s", self.debug_port, self._temp_profile)
        self._chrome_proc = subprocess.Popen(cmd, **popen_kwargs)

        # Wait for the debug port to become available. 60s ceiling
        # because under concurrent dispatch (3+ Chromes launching
        # simultaneously on Xvfb) cold start can exceed 30s — the
        # original 20s killed parallel L2 fan-out runs.
        port_ready_timeout = float(
            os.environ.get("FLOW_CHROME_PORT_READY_TIMEOUT", "60")
        )
        if not await self._wait_for_port(self.debug_port, timeout_sec=port_ready_timeout):
            raise RuntimeError(
                f"Chrome debug port {self.debug_port} not ready after "
                f"{port_ready_timeout:.0f} s"
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

        # Diagnostic: enumerate every context/page so we can spot stray
        # session-restore windows that Playwright isn't driving.
        try:
            for ci, ctx in enumerate(self.browser.contexts):
                for pi, pg in enumerate(ctx.pages):
                    logger.info(
                        "CDP attach: ctx[%d].pages[%d] url=%r", ci, pi, pg.url
                    )
        except Exception as exc:
            logger.debug("page enumeration failed: %s", exc)

        # Pick primary page — skip internal Chrome UI surfaces that CDP
        # exposes as pages (omnibox popup dropdown, tab search, etc.).
        # User-report 2026-04-24: `pages[0]` was `chrome://omnibox-popup.
        # top-chrome/` which is the URL-bar autocomplete dropdown, not a
        # web tab. Navigating it "succeeded" but the visible tab stayed
        # on `chrome://new-tab-page/` → worker thought it was on Flow
        # while the user saw a blank URL bar.
        pages = self.context.pages

        def _is_real_tab(p) -> bool:
            u = (p.url or "").lower()
            if u.startswith("chrome://omnibox"):
                return False
            if u.startswith("chrome://tab-search"):
                return False
            if u.startswith("devtools://"):
                return False
            return True

        real_tabs = [p for p in pages if _is_real_tab(p)]
        if real_tabs:
            self.page = real_tabs[0]
        elif pages:
            # No real tab — open a fresh one we fully control.
            self.page = await self.context.new_page()
        else:
            self.page = await self.context.new_page()
        logger.info(
            "CDP primary page picked: url=%r (real_tabs=%d/%d)",
            self.page.url, len(real_tabs), len(pages),
        )
        # Bind before any caller-driven navigation so the first generation's
        # response events can't slip past an unbound hook (issue #45).
        await self._setup_route_blocking()
        self._setup_network_hooks()

    # ------------------------------------------------------------------
    # Mode B -- Playwright persistent context
    # ------------------------------------------------------------------

    async def _start_persistent(self) -> None:
        """Launch using Playwright's persistent-context API."""
        self._clear_failure_cache()
        self._prepare_profile()

        args = [
            "--disable-blink-features=AutomationControlled",
            "--no-first-run",
            "--no-default-browser-check",
            *_build_window_geometry_args(),
            *_build_gpu_args(),
            *_build_perf_args(),
        ]
        args = _apply_root_sandbox_guard(args)

        in_docker = Path("/.dockerenv").exists() or os.environ.get(
            "IS_DOCKER", ""
        ).strip().lower() in ("1", "true")
        if in_docker:
            if "--no-sandbox" not in args:
                args.append("--no-sandbox")
            args.extend(["--disable-setuid-sandbox", "--disable-dev-shm-usage"])
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
        # Bind before any caller-driven navigation so the first generation's
        # response events can't slip past an unbound hook (issue #45).
        await self._setup_route_blocking()
        self._setup_network_hooks()

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
        """Bind ``page.on('response')`` to capture network traffic passively.

        Idempotent: safe to call multiple times per client lifecycle.
        """
        if self.page is None or self._hooks_bound:
            return
        self.page.on("response", self._on_response)
        self._hooks_bound = True

    async def _setup_route_blocking(self) -> None:
        """Block font CDN requests to reduce bandwidth and render work.

        Safe for all launch modes — operates at Playwright route level,
        not via Chrome flags, so it does not affect anti-bot fingerprinting.
        Idempotent: repeated calls are harmless (Playwright deduplicates).
        """
        if self.page is None:
            return
        for pattern in (
            "**/fonts.googleapis.com/**",
            "**/fonts.gstatic.com/**",
            "**/*.{woff,woff2,ttf,eot}",
        ):
            await self.page.route(
                pattern, lambda route: asyncio.ensure_future(route.abort())
            )

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
                or "batchgenerateimages" in url_l
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

            # --- Image media names (text-to-image fast path) ---
            if "batchgenerateimages" in url_l and status == 200:
                body = call_entry.get("body")
                if isinstance(body, dict):
                    for m in body.get("media", []):
                        if not isinstance(m, dict):
                            continue
                        name = m.get("name")
                        if name and name not in self._image_names:
                            self._image_names.append(name)
                            logger.debug("Captured image media name: %s", name[:20])

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
        self._image_names.clear()

    def pop_image_names(self, before_count: int = 0) -> list[str]:
        """Return new image media names captured since before_count."""
        return list(self._image_names[before_count:])

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

    def _resolve_owned_chrome_pgid(self, proc: subprocess.Popen) -> int | None:
        """Return a safe process group id for POSIX Chrome tree teardown."""
        pid = getattr(proc, "pid", 0)
        if not pid or not _looks_like_owned_user_data_dir(self._temp_profile):
            return None

        cmdline = _read_proc_cmdline(pid)
        if not _cmdline_has_user_data_dir(cmdline, self._temp_profile):
            logger.debug(
                "Skipping killpg for pid=%s: cmdline missing owned --user-data-dir",
                pid,
            )
            return None

        try:
            pgid = os.getpgid(pid)
        except OSError as exc:
            logger.debug("Skipping killpg for pid=%s: getpgid failed: %s", pid, exc)
            return None

        comm = _read_proc_comm(pid)
        if not comm or not any(name in comm.lower() for name in ("chrome", "chromium")):
            logger.debug(
                "Skipping killpg for pid=%s: /proc/%s/comm=%r is not Chrome",
                pid,
                pid,
                comm,
            )
            return None

        if pgid != pid:
            leader_comm = _read_proc_comm(pgid)
            logger.warning(
                "Refusing killpg for Chrome pid=%s: pgid=%s leader=%r is not our process",
                pid,
                pgid,
                leader_comm,
            )
            return None

        return pgid

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
                pgid = self._resolve_owned_chrome_pgid(proc)
                if pgid is not None:
                    try:
                        os.killpg(pgid, signal.SIGTERM)
                    except (ProcessLookupError, PermissionError, OSError) as exc:
                        logger.debug(
                            "killpg(SIGTERM) failed for pid=%s pgid=%s: %s; falling back to terminate()",
                            pid,
                            pgid,
                            exc,
                        )
                        proc.terminate()
                else:
                    proc.terminate()

                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    if pgid is not None:
                        try:
                            os.killpg(pgid, signal.SIGKILL)
                        except (ProcessLookupError, PermissionError, OSError) as exc:
                            logger.debug(
                                "killpg(SIGKILL) failed for pid=%s pgid=%s: %s; falling back to kill()",
                                pid,
                                pgid,
                                exc,
                            )
                            proc.kill()
                    else:
                        proc.kill()
                    proc.wait(timeout=3)
        except Exception:
            try:
                if not _IS_WINDOWS and pid:
                    pgid = self._resolve_owned_chrome_pgid(proc)
                    if pgid is not None:
                        os.killpg(pgid, signal.SIGKILL)
                    else:
                        proc.kill()
                    try:
                        proc.wait(timeout=3)
                    except Exception:
                        pass
                else:
                    proc.kill()
                    try:
                        proc.wait(timeout=3)
                    except Exception:
                        pass
            except Exception:
                pass
        finally:
            self._chrome_proc = None

    def __repr__(self) -> str:
        state = "connected" if self.page else "disconnected"
        mode = "cdp" if self.real_chrome else "persistent"
        return f"<FlowClient profile={self.profile_name!r} mode={mode} {state}>"
