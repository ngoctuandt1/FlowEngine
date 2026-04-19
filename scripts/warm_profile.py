"""Open a profile in visible Chrome and auto-login to Google via Gmail.

Usage
-----
    python scripts/warm_profile.py ngoctuandt20

Opens a Playwright-controlled Chrome window against ``chrome-profiles/{profile}``,
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
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.async_api import async_playwright

from flow.login import handle_login_redirect, is_login_page

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


async def warm(profile: str) -> int:
    profile_dir = (Path("chrome-profiles") / profile).resolve()
    profile_dir.mkdir(parents=True, exist_ok=True)
    log.info("Profile dir: %s", profile_dir)

    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            headless=False,
            args=LAUNCH_ARGS,
            viewport=VIEWPORT,
        )
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()

        log.info("Navigating to %s…", WARM_URL)
        await page.goto(WARM_URL, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(2)

        if is_login_page(page.url):
            log.info("Login redirect detected — driving auto-login for %s", profile)
            await handle_login_redirect(
                page, timeout=LOGIN_TIMEOUT_SEC, profile_name=profile
            )
        else:
            log.info("Already signed in — no login flow needed.")

        await asyncio.sleep(COOKIE_FLUSH_SEC)
        await ctx.close()

    log.info("Done. Cookies persisted to %s", profile_dir)
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python scripts/warm_profile.py <profile>", file=sys.stderr)
        sys.exit(2)
    sys.exit(asyncio.run(warm(sys.argv[1])))
