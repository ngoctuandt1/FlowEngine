"""Open a profile in visible Chrome so the user can sign into Google manually.

Usage
-----
    python scripts/warm_profile.py ngoctuandt20

Opens a Playwright-controlled Chrome window against ``chrome-profiles/{profile}``
and navigates to Gmail. The user signs in interactively (email + password +
2FA), then closes the window. Cookies + IndexedDB state persist to the
profile directory; subsequent FlowEngine worker launches clone that profile
and inherit the Google session.

This script does NOT automate the sign-in flow. An earlier revision navigated
to ``accounts.google.com/ServiceLogin?service=googlefx`` and invoked
``flow.login.handle_login_redirect`` with credentials read from
``profiles_ultra.txt``; neither the URL nor the credentials source was
authorized by the user. Manual sign-in at Gmail is the only approved flow
(Run 19 session report §5).

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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("warm_profile")

WARM_URL = "https://mail.google.com"
VIEWPORT = {"width": 1280, "height": 800}
LAUNCH_ARGS = ["--no-first-run", "--no-default-browser-check"]


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

        log.info("Navigating to %s for manual sign-in…", WARM_URL)
        await page.goto(WARM_URL, wait_until="domcontentloaded", timeout=30000)

        log.info("Sign in, then close the Chrome window when done.")
        await ctx.wait_for_event("close", timeout=0)

    log.info("Window closed. Cookies persisted to %s", profile_dir)
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python scripts/warm_profile.py <profile>", file=sys.stderr)
        sys.exit(2)
    sys.exit(asyncio.run(warm(sys.argv[1])))
