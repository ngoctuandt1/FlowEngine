"""Open a profile in visible Chrome and auto-login to Google via Flow.

Usage
-----
    python scripts/warm_profile.py ngoctuandt20

Opens a real Chrome window against
``${CHROME_USER_DATA_DIR:-chrome-profiles}/{profile}`` via a local CDP port,
navigates to Flow (``labs.google/fx/tools/flow``), and drives the Google
sign-in flow via ``flow.login.handle_login_redirect`` (credentials read
from ``profiles_ultra.txt``). Cookies + IndexedDB state persist to the
profile directory; subsequent FlowEngine worker launches clone that
profile and inherit the Google session.

Why Flow, not Gmail
-------------------
Earlier revisions used ``mail.google.com`` as the entry URL. That broke
for Workspace accounts where the org admin had disabled Gmail but
kept Flow enabled — warm exited "Already signed in" pointing at
``access.workspace.google.com/ServiceNotAllowed?application=740348119625``
(Gmail's blocked app ID), and the profile looked healthy even though
it could never reach Gmail's inbox. The 2026-04-20 ``ServiceLogin``
revision and the manual ``wait_for_event("close")`` revision are both
still rejected (memory ``feedback_warm_profile_manual_gmail.md``); this
revision swaps the entry URL to Flow so the warm signal matches what
the worker actually needs (Flow access), not an unrelated proxy app.

Landing is resolved via :func:`_resolve_flow_landing`, which polls until
it matches one of three terminal states:

* :func:`flow.login.is_flow_app_authenticated` — Flow app shell is mounted
  or a project/editor URL is reached; cookies are persisted and warm exits 0.
* :func:`flow.login.is_login_page` — Google sign-in redirect;
  :func:`flow.login.handle_login_redirect` drives the auto-login flow.
* :func:`flow.login.is_service_blocked` — Workspace ``ServiceNotAllowed``
  redirect; raises :class:`FlowServiceDisabled` because no client-side
  retry can fix admin policy.

Recovery
--------
If the browser crashes on launch with ``TargetClosedError`` + Chrome exit
``0x80000003`` (``STATUS_BREAKPOINT``), the profile directory is corrupt.
Memory ``feedback_profile_full_reset.md`` prescribes full delete + re-run —
do NOT try the cache-preserve-cookies bisect.
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.async_api import Browser, BrowserContext, Error, Page, async_playwright

from flow.login import (
    handle_login_redirect,
    is_flow_app_authenticated,
    is_flow_app_url,
    is_login_page,
    is_service_blocked,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("warm_profile")

WARM_URL = "https://labs.google/fx/tools/flow"
VIEWPORT = {"width": 1280, "height": 800}
LAUNCH_ARGS = ["--no-first-run", "--no-default-browser-check"]
LOGIN_TIMEOUT_SEC = 120
COOKIE_FLUSH_SEC = 3
CDP_CONNECT_TIMEOUT_SEC = 15
# Flow's anonymous redirect to accounts.google.com and the signed-in app
# shell usually resolve quickly. Keep the full resolver at 120s so slow
# auth redirects have room, but only treat `labs.google/fx` as ready after
# explicit app-shell authentication signals.
FLOW_RESOLVE_TIMEOUT_SEC = 120
FLOW_AUTH_POLL_TIMEOUT_SEC = 30
_POLL_INTERVAL_SEC = 0.5
_FLOW_CTA_SELECTORS = (
    "main button:has-text('Create with Flow')",
    "main [role='button']:has-text('Create with Flow')",
    "main a:has-text('Create with Flow'):not([href^='#'])",
    "main button:has-text('Get started')",
    "main [role='button']:has-text('Get started')",
    "main a:has-text('Get started'):not([href^='#'])",
    "button:has-text('Create with Flow')",
    "[role='button']:has-text('Create with Flow')",
    "a:has-text('Create with Flow'):not([href^='#'])",
    "button:has-text('Get started')",
    "[role='button']:has-text('Get started')",
    "a:has-text('Get started'):not([href^='#'])",
)

CHROME_CANDIDATES = (
    lambda: os.environ.get("FLOW_WARM_CHROME_PATH"),
    lambda: os.environ.get("CHROME_PATH"),
    # Windows install locations
    lambda: r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    lambda: r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    lambda: str(Path.home() / "AppData/Local/Google/Chrome/Application/chrome.exe"),
    # Linux install locations (Debian/Ubuntu google-chrome-stable, Chromium)
    lambda: "/usr/bin/google-chrome",
    lambda: "/usr/bin/google-chrome-stable",
    lambda: "/usr/bin/chromium-browser",
    lambda: "/usr/bin/chromium",
)


class FlowServiceDisabled(RuntimeError):
    """Raised when Workspace returns ServiceNotAllowed for Flow.

    The account is signed in to Google but the org admin has disabled
    Flow (or all consumer labs apps). No client-side retry can fix
    this — the profile must be retired or the admin must re-enable
    access. Surfaces the offending URL so the caller can record the
    application= query param for diagnostics.
    """

    def __init__(self, profile_name: str, url: str):
        self.profile_name = profile_name
        self.url = url
        super().__init__(
            f"Flow service disabled for profile {profile_name!r}: {url}"
        )


async def _resolve_flow_landing(page, timeout_sec: float) -> str:
    """Wait for the Flow goto to resolve to a known terminal URL.

    Returns ``"flow"`` when Flow exposes an authenticated app signal, and
    ``"signin"`` when it matches a Google sign-in URL. A bare
    ``labs.google/fx`` URL is not terminal because the anonymous marketing
    landing shares that URL; the resolver clicks the Flow CTA and loops
    until sign-in, service-blocked, or app-authenticated state appears.

    Raises :class:`FlowServiceDisabled` when the URL is the Workspace
    ``ServiceNotAllowed`` redirect — that state means the account is
    signed in but admin-disabled for the requested service, and warm
    cannot recover. Raises :class:`TimeoutError` on any other landing
    so unknown URLs fail loud rather than silently succeed (the
    2026-04-20 false-positive class; preserved from the Gmail-entry
    revision).
    """
    deadline = asyncio.get_event_loop().time() + timeout_sec
    while asyncio.get_event_loop().time() < deadline:
        url = page.url
        # Order matters: ServiceNotAllowed and accounts.google.com both
        # live under *.google.com and neither matches `labs.google/fx`,
        # but check the service-blocked redirect first because it is
        # the strongest signal — we want the dedicated exception even
        # if a future Google change also flips `is_login_page`.
        if is_service_blocked(url):
            raise FlowServiceDisabled(profile_name="", url=url)
        if is_login_page(url):
            return "signin"
        if await is_flow_app_authenticated(page):
            return "flow"
        if is_flow_app_url(url):
            auth_poll_deadline = min(
                asyncio.get_event_loop().time() + FLOW_AUTH_POLL_TIMEOUT_SEC,
                deadline,
            )
            while asyncio.get_event_loop().time() < auth_poll_deadline:
                url = page.url
                if is_service_blocked(url):
                    raise FlowServiceDisabled(profile_name="", url=url)
                if is_login_page(url):
                    return "signin"
                if await is_flow_app_authenticated(page):
                    return "flow"
                if not is_flow_app_url(url):
                    break
                await asyncio.sleep(_POLL_INTERVAL_SEC)
            if await _click_flow_cta(page):
                continue
        await asyncio.sleep(_POLL_INTERVAL_SEC)
    if is_service_blocked(page.url):
        raise FlowServiceDisabled(profile_name="", url=page.url)
    if is_login_page(page.url):
        return "signin"
    if await is_flow_app_authenticated(page):
        return "flow"
    raise TimeoutError("Flow landing did not resolve to signed-in/login/blocked")


async def _click_flow_cta(page) -> bool:
    for selector in _FLOW_CTA_SELECTORS:
        try:
            await page.locator(selector).first.click(timeout=1000)
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=5000)
            except Exception:
                pass
            return True
        except Exception as exc:
            log.debug("Flow CTA selector not clickable (%s): %s", selector, exc)
    return False


def _find_chrome_executable() -> str:
    for candidate_factory in CHROME_CANDIDATES:
        candidate = candidate_factory()
        if candidate and Path(candidate).exists():
            return candidate
    raise FileNotFoundError(
        "Google Chrome / Chromium not found. "
        "Set FLOW_WARM_CHROME_PATH to the browser executable path."
    )


def _reserve_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        return sock.getsockname()[1]


def _launch_real_chrome(profile_dir: Path, cdp_port: int) -> subprocess.Popen:
    chrome_path = _find_chrome_executable()
    cmd = [
        chrome_path,
        f"--user-data-dir={profile_dir}",
        f"--remote-debugging-port={cdp_port}",
        "--new-window",
        *LAUNCH_ARGS,
        WARM_URL,
    ]
    log.info("Launching real Chrome via CDP: %s", chrome_path)
    return subprocess.Popen(cmd)


async def _connect_chrome_over_cdp(
    playwright,
    cdp_port: int,
) -> tuple[Browser, BrowserContext, Page]:
    endpoint = f"http://127.0.0.1:{cdp_port}"
    deadline = asyncio.get_event_loop().time() + CDP_CONNECT_TIMEOUT_SEC
    last_error: Exception | None = None
    while asyncio.get_event_loop().time() < deadline:
        try:
            browser = await playwright.chromium.connect_over_cdp(endpoint)
            context = browser.contexts[0]
            page = context.pages[0] if context.pages else await context.new_page()
            await page.set_viewport_size(VIEWPORT)
            return browser, context, page
        except Error as exc:
            last_error = exc
            await asyncio.sleep(0.5)
    raise TimeoutError(
        f"Timed out connecting to Chrome CDP at {endpoint}: {last_error}"
    )


async def _close_browser_connection(browser: Browser) -> None:
    try:
        await browser.close()
    except Exception as exc:
        log.warning("CDP disconnect failed: %s", exc)


def _stop_chrome_process(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def _profile_base_dir() -> Path:
    return Path(
        os.environ.get("CHROME_USER_DATA_DIR", "chrome-profiles")
    ).expanduser().resolve()


async def warm(profile: str) -> int:
    profile_dir = (_profile_base_dir() / profile).resolve()
    profile_dir.mkdir(parents=True, exist_ok=True)
    log.info("Profile dir: %s", profile_dir)

    async with async_playwright() as p:
        cdp_port = _reserve_local_port()
        chrome_proc = _launch_real_chrome(profile_dir, cdp_port)
        browser: Browser | None = None

        try:
            browser, _ctx, page = await _connect_chrome_over_cdp(p, cdp_port)
            log.info("Connected to Chrome CDP on port %d", cdp_port)

            try:
                state = await _resolve_flow_landing(page, FLOW_RESOLVE_TIMEOUT_SEC)
            except FlowServiceDisabled as exc:
                # Re-raise with the profile name attached — the resolver
                # has no access to it.
                raise FlowServiceDisabled(profile_name=profile, url=exc.url) from None

            if state == "signin":
                log.info("Sign-in required — driving auto-login for %s", profile)
                await handle_login_redirect(
                    page, timeout=LOGIN_TIMEOUT_SEC, profile_name=profile
                )
                # After auto-login Google bounces back through the
                # original `continue=` target (Flow). Re-resolve once so
                # a still-anonymous landing (login flow silently aborted)
                # fails loud instead of exiting 0 with no cookies.
                try:
                    post_state = await _resolve_flow_landing(
                        page, FLOW_RESOLVE_TIMEOUT_SEC
                    )
                except FlowServiceDisabled as exc:
                    raise FlowServiceDisabled(
                        profile_name=profile, url=exc.url
                    ) from None
                if post_state != "flow":
                    raise RuntimeError(
                        f"Auto-login completed but Flow did not load "
                        f"(state={post_state}, url={page.url})"
                    )
                log.info("Auto-login succeeded — Flow reached at %s", page.url[:80])
            else:
                log.info("Already signed in — Flow reached at %s", page.url[:80])

            await asyncio.sleep(COOKIE_FLUSH_SEC)
        finally:
            if browser is not None:
                await _close_browser_connection(browser)
            _stop_chrome_process(chrome_proc)

    log.info("Done. Cookies persisted to %s", profile_dir)
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python scripts/warm_profile.py <profile>", file=sys.stderr)
        sys.exit(2)
    try:
        sys.exit(asyncio.run(warm(sys.argv[1])))
    except FlowServiceDisabled as exc:
        log.error("%s", exc)
        # Distinct exit code so callers (worker preflight, livetest) can
        # branch on "profile is dead, do not retry" without parsing the
        # log line.
        sys.exit(3)
