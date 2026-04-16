"""Google login handling — detect and resolve OAuth redirects.

When a Chrome profile's session expires, navigating to Flow will
redirect to accounts.google.com.  This module detects that redirect
and attempts to resume the session by clicking through the account
chooser and consent screens.
"""

import asyncio
import logging
import re

logger = logging.getLogger(__name__)

# URL patterns indicating we're on a login page
_LOGIN_PATTERNS = [
    "accounts.google.com",
    "signin",
    "accountchooser",
    "oauth",
    "consent",
]

# Max time to wait for login to complete (seconds)
LOGIN_TIMEOUT = 120


def is_login_page(url: str) -> bool:
    """Check if the current URL is a Google login/auth page."""
    url_lower = url.lower()
    return any(pat in url_lower for pat in _LOGIN_PATTERNS)


async def handle_login_redirect(page, timeout: int = LOGIN_TIMEOUT) -> bool:
    """Handle Google login redirect — click account, consent, wait for Flow.

    Strategy:
    1. If on account chooser → click the first listed account
    2. If on consent page → click 'Allow' / 'Continue'
    3. Wait until URL returns to labs.google/fx

    Returns True if successfully returned to Flow, False if timed out.
    """
    current = page.url
    if not is_login_page(current):
        return True  # Not on login page

    logger.warning("Login redirect detected: %s", current[:120])

    # Try to click account in account chooser
    await _try_click_account(page)

    # Wait for Flow URL with periodic checks for consent/continue buttons
    deadline = asyncio.get_event_loop().time() + timeout
    check_interval = 3.0

    while asyncio.get_event_loop().time() < deadline:
        current = page.url

        # Success — back on Flow
        if "labs.google" in current and "tools/flow" in current:
            logger.info("Login resolved — back on Flow: %s", current[:100])
            await asyncio.sleep(2)  # Let page settle
            return True

        # Still on Google auth — try clicking buttons
        await _try_click_consent(page)
        await _try_click_account(page)
        await _try_click_continue(page)

        await asyncio.sleep(check_interval)

    logger.error("Login not resolved after %ds — manual intervention needed", timeout)
    return False


async def _try_click_account(page):
    """Try to click a Google account in the account chooser."""
    selectors = [
        # Account chooser list items (email shown as data-identifier or text)
        "[data-identifier]",
        # Account list items
        "li[data-authuser]",
        # divs with email
        "div[data-email]",
        # Any clickable element that looks like an email
        "[role='link']",
    ]

    for sel in selectors:
        try:
            el = page.locator(sel).first
            if await el.is_visible(timeout=1000):
                text = await el.inner_text()
                await el.click(timeout=3000)
                logger.info("Clicked account: %s via %s", text[:40].strip(), sel)
                await asyncio.sleep(2)
                return True
        except Exception:
            continue

    return False


async def _try_click_consent(page):
    """Try to click Allow/Approve on OAuth consent page."""
    selectors = [
        "button:has-text('Allow')",
        "button:has-text('Cho phép')",
        "button:has-text('Continue')",
        "button:has-text('Tiếp tục')",
        "#submit_approve_access",
        "button[id*='allow']",
    ]

    for sel in selectors:
        try:
            btn = page.locator(sel).first
            if await btn.is_visible(timeout=500):
                await btn.click(timeout=3000)
                logger.info("Clicked consent: %s", sel)
                await asyncio.sleep(2)
                return True
        except Exception:
            continue

    return False


async def _try_click_continue(page):
    """Try to click various Continue/Next/Sign-in buttons."""
    selectors = [
        "button:has-text('Next')",
        "button:has-text('Tiếp')",
        "button:has-text('Sign in')",
        "button:has-text('Đăng nhập')",
        "#identifierNext",
        "#passwordNext",
        "input[type='submit']",
    ]

    for sel in selectors:
        try:
            btn = page.locator(sel).first
            if await btn.is_visible(timeout=500):
                await btn.click(timeout=3000)
                logger.info("Clicked continue: %s", sel)
                await asyncio.sleep(2)
                return True
        except Exception:
            continue

    return False
