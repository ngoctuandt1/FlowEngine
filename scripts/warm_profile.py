"""Open a profile in visible Chrome and auto-login to Google via Gmail.

Usage
-----
    python scripts/warm_profile.py ngoctuandt20

Opens a real Chrome window against
``${CHROME_USER_DATA_DIR:-chrome-profiles}/{profile}`` via a local CDP port,
navigates to Gmail, and drives the Google sign-in flow via
``flow.login.handle_login_redirect`` (credentials read from
``profiles_ultra.txt``). Cookies + IndexedDB state persist to the profile
directory; subsequent FlowEngine worker launches clone that profile and
inherit the Google session.

The earlier auto-login revision that hard-coded
``accounts.google.com/ServiceLogin?service=googlefx`` was rejected by the
user (2026-04-20). ``mail.google.com`` is the approved entry URL — Gmail
redirects anonymous sessions into the Google login flow, and
``handle_login_redirect`` takes over once that redirect lands.

Landing is resolved via :func:`_resolve_gmail_landing`, which polls the
page URL until it matches either an authenticated Gmail path
(``mail.google.com/mail/u/<N>/...``) or a Google sign-in URL. If Gmail
routes the anonymous session to ``workspace.google.com/.../gmail/``
instead of straight to ``accounts.google.com`` (happens under some
locales / residual cookies), the resolver clicks the marketing page's
top-nav Sign-in anchor to bridge back into the sign-in flow. Any
other landing raises :class:`TimeoutError` — the earlier "fixed
2-second sleep + sign-in-URL check, else assume signed in" logic
mis-labelled profiles whose initial URL was still on the Workspace
marketing page, writing zero auth cookies while logging "Already
signed in" (user-reported 2026-04-20).

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

from flow.login import handle_login_redirect, is_gmail_inbox, is_login_page

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("warm_profile")

WARM_URL = "https://mail.google.com"
VIEWPORT = {"width": 1280, "height": 800}
LAUNCH_ARGS = ["--no-first-run", "--no-default-browser-check"]
LOGIN_TIMEOUT_SEC = 120
COOKIE_FLUSH_SEC = 3
CDP_CONNECT_TIMEOUT_SEC = 15
# Gmail's server-side redirect from `mail.google.com/` to the inbox
# (signed-in), accounts.google.com/v3/signin (anonymous → direct), or
# workspace.google.com/…/gmail/ (anonymous → marketing, locale-dependent)
# completes in a couple seconds on a warm connection. 30s is generous
# enough for slow networks and short enough to fail loud on unexpected
# landings.
GMAIL_RESOLVE_TIMEOUT_SEC = 30
_POLL_INTERVAL_SEC = 0.5
_WORKSPACE_GMAIL_TOKEN = "workspace.google.com"
# The Workspace marketing page's top-nav "Sign in" anchor uniquely
# points at AccountChooser/signinchooser; SignUp and business-signup
# anchors on the same page do not match. Text-based fallback covers
# a future href shift.
_WORKSPACE_SIGNIN_SELECTORS = (
    "a[href*='AccountChooser/signinchooser']",
    "a[href*='accounts.google.com']:has-text('Sign in')",
)
CHROME_CANDIDATES = (
    lambda: os.environ.get("FLOW_WARM_CHROME_PATH"),
    lambda: os.environ.get("CHROME_PATH"),
    lambda: r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    lambda: r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    lambda: str(Path.home() / "AppData/Local/Google/Chrome/Application/chrome.exe"),
)


def _is_workspace_gmail_marketing(url: str) -> bool:
    u = url.lower()
    return _WORKSPACE_GMAIL_TOKEN in u and "gmail" in u


async def _bridge_workspace_to_signin(page) -> bool:
    """Navigate `page` to the Workspace Sign-in anchor's href.

    The anchor opens in a new tab (``target="_blank"``), so a real click
    leaves the original page parked on the Workspace marketing URL and
    the resolver's URL poll never sees the sign-in landing. We read the
    href off the same anchor and goto it in-place instead — the href
    comes from Google's own marketing page, not hardcoded here.
    """
    for sel in _WORKSPACE_SIGNIN_SELECTORS:
        try:
            link = page.locator(sel).first
            if not await link.is_visible(timeout=1500):
                continue
            href = await link.get_attribute("href")
            if not href:
                continue
            log.info("Bridging via %s → %s", sel, href[:80])
            await page.goto(href, wait_until="domcontentloaded", timeout=15000)
            return True
        except Exception:
            continue
    return False


async def _resolve_gmail_landing(page, timeout_sec: float) -> str:
    """Wait for the Gmail goto to resolve to a known terminal URL.

    Returns ``"inbox"`` when the URL matches the authenticated Gmail
    path (``mail.google.com/mail/u/<N>/...``) and ``"signin"`` when it
    matches a Google sign-in URL. If the initial redirect lands on
    ``workspace.google.com/.../gmail/`` (the Workspace marketing page
    that anonymous sessions hit under some locales/cookie states), the
    resolver clicks the top-nav Sign-in anchor once and keeps polling
    — that click navigates to ``accounts.google.com/AccountChooser``,
    which the next iteration detects as ``"signin"``.

    Raises :class:`TimeoutError` on any other landing — silently
    treating an unknown URL as "signed in" is the 2026-04-20 regression
    (warm succeeded on a profile with zero auth cookies because
    ``mail.google.com/`` didn't match the sign-in patterns during the
    2-second window).
    """
    deadline = asyncio.get_event_loop().time() + timeout_sec
    bridged = False
    while asyncio.get_event_loop().time() < deadline:
        url = page.url
        if is_gmail_inbox(url):
            return "inbox"
        if is_login_page(url):
            return "signin"
        if not bridged and _is_workspace_gmail_marketing(url):
            log.info("Workspace marketing detected (%s); bridging to sign-in", url[:80])
            bridged = await _bridge_workspace_to_signin(page)
        await asyncio.sleep(_POLL_INTERVAL_SEC)
    raise TimeoutError(
        f"mail.google.com did not resolve to inbox or sign-in within "
        f"{timeout_sec:.0f}s — last URL: {page.url}"
    )


def _find_chrome_executable() -> str:
    for candidate_factory in CHROME_CANDIDATES:
        candidate = candidate_factory()
        if candidate and Path(candidate).exists():
            return candidate
    raise FileNotFoundError(
        "Google Chrome not found. Set FLOW_WARM_CHROME_PATH to chrome.exe."
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

            state = await _resolve_gmail_landing(page, GMAIL_RESOLVE_TIMEOUT_SEC)

            if state == "signin":
                log.info("Sign-in required — driving auto-login for %s", profile)
                await handle_login_redirect(
                    page, timeout=LOGIN_TIMEOUT_SEC, profile_name=profile
                )
            else:
                log.info("Already signed in (inbox at %s)", page.url[:80])

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
    sys.exit(asyncio.run(warm(sys.argv[1])))
