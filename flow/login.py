"""Google login handling — Playwright-based auto-login.

When the browser hits a Google login redirect, this module handles it
directly inside the same Playwright Chrome session:
  1. Email entry → Next
  2. Password entry → Next
  3. 2FA/TOTP → Next
  4. Consent/Continue → back to Flow

Credentials are read from profiles_ultra.txt.
Falls back to AIgglog.py subprocess if Playwright login fails.
"""

import asyncio
import logging
import os
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

_LOGIN_PATTERNS = [
    "accounts.google.com",
    "signin",
    "accountchooser",
    "oauth",
    "consent",
]

# Gmail serves authenticated users at `mail.google.com/mail/u/<N>/...`;
# the pre-redirect `mail.google.com/` and the anonymous redirect target
# `accounts.google.com/v3/signin/...` both lack this token, so a single
# substring match cleanly separates the three landing states.
_GMAIL_INBOX_URL_TOKEN = "mail.google.com/mail/u/"

LOGIN_TIMEOUT = 90

# Path to profiles_ultra.txt for credentials
PROFILE_LIST_FILE = os.environ.get(
    "FLOW_PROFILE_LIST_FILE",
    str(Path("D:/AI/AI-Engine3-Project/profiles_ultra.txt")),
)

# AIgglog.py fallback
AIGGLOG_PATH = os.environ.get(
    "AIGGLOG_PATH",
    str(Path("D:/AI/AI-Engine3-Project/AIgglog.py")),
)


class NeedAutoLogin(RuntimeError):
    """Raised when login cannot be resolved at all."""
    def __init__(self, profile_name: str):
        self.profile_name = profile_name
        super().__init__(f"Auto-login needed for profile: {profile_name}")


def is_login_page(url: str) -> bool:
    url_lower = url.lower()
    return any(pat in url_lower for pat in _LOGIN_PATTERNS)


def is_gmail_inbox(url: str) -> bool:
    """True when the URL indicates an authenticated Gmail session.

    Used as the positive "logged-in" signal for Gmail-entry warm flows,
    paired with :func:`is_login_page` as the negative signal. Checking
    only for the absence of a sign-in URL is unsafe — Gmail's
    anonymous redirect may briefly pass through `mail.google.com/` or
    `workspace.google.com/gmail/about/`, neither of which matches the
    sign-in patterns but neither means logged-in either.
    """
    return _GMAIL_INBOX_URL_TOKEN in url.lower()


# ======================================================================
# Credential loading
# ======================================================================

def _load_credentials(profile_name: str) -> dict | None:
    """Load credentials from profiles_ultra.txt for a given profile.

    Format: profile_path|email|password|2fa_secret|recovery_email

    Returns dict with keys: email, password, totp_secret, recovery
    """
    profile_list = Path(PROFILE_LIST_FILE)
    if not profile_list.exists():
        logger.error("profiles_ultra.txt not found: %s", profile_list)
        return None

    try:
        for line in profile_list.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("|")
            if len(parts) < 3:
                continue

            # Match by profile directory name or email
            prof_path = parts[0].strip()
            email = parts[1].strip()
            password = parts[2].strip()
            totp_secret = parts[3].strip() if len(parts) > 3 else ""
            recovery = parts[4].strip() if len(parts) > 4 else ""

            # Match by profile name in path or email prefix
            prof_dir_name = Path(prof_path).name
            if prof_dir_name == profile_name or email.split("@")[0] == profile_name:
                return {
                    "email": email,
                    "password": password,
                    "totp_secret": totp_secret,
                    "recovery": recovery,
                }
    except Exception as e:
        logger.error("Failed to read profiles_ultra.txt: %s", e)

    logger.error("No credentials found for profile: %s", profile_name)
    return None


def _generate_totp(secret: str) -> str:
    """Generate a TOTP code from the secret."""
    try:
        import pyotp
        totp = pyotp.TOTP(secret)
        return totp.now()
    except Exception as e:
        logger.error("TOTP generation failed: %s", e)
        return ""


# ======================================================================
# Main login handler
# ======================================================================

async def handle_login_redirect(
    page,
    timeout: int = LOGIN_TIMEOUT,
    profile_name: str = "",
) -> bool:
    """Handle Google login redirect — full Playwright-based auto-login.

    Handles: account chooser, email, password, 2FA/TOTP, consent.
    All done INSIDE the same Playwright Chrome session so the session
    persists without needing to restart Chrome.

    Returns True if back on Flow.
    Raises NeedAutoLogin only as last resort.
    """
    current = page.url
    if not is_login_page(current):
        return True

    logger.warning("Login redirect detected: %s", current[:120])

    # Try account chooser first (simplest case)
    await _try_click_account(page)
    await asyncio.sleep(2)
    if _is_on_flow(page.url):
        logger.info("Login resolved by account chooser")
        return True

    # Need full login — load credentials
    creds = _load_credentials(profile_name)
    if not creds:
        logger.error("No credentials for %s — cannot auto-login", profile_name)
        raise NeedAutoLogin(profile_name)

    logger.info("Starting Playwright auto-login for %s", creds["email"])

    deadline = asyncio.get_event_loop().time() + timeout
    step = "detect"
    stuck_key = None
    stuck_count = 0

    while asyncio.get_event_loop().time() < deadline:
        current = page.url

        # Success check: either `continue=` target (Flow or Gmail inbox)
        # is terminal. Entry via mail.google.com lands on the inbox, not
        # on Flow — accepting both keeps warm-profile and worker callers
        # on one code path.
        if _is_on_flow(current) or is_gmail_inbox(current):
            logger.info("Login complete (url=%s)", current[:80])
            await asyncio.sleep(2)
            return True

        # Detect current step
        if await _is_email_page(page):
            step = "email"
        elif await _is_password_page(page):
            step = "password"
        elif await _is_totp_page(page):
            step = "totp"
        elif await _is_challenge_selection(page):
            step = "challenge_select"
        elif "consent" in current.lower() or "approval" in current.lower():
            step = "consent"
        elif "myaccount.google.com" in current.lower():
            # After login, Google may land on myaccount — navigate to Flow
            step = "navigate_flow"

        # Stuck detection: same (step, url) 3x in a row → reload to dismiss
        # transient Google overlays like <div class="dKGsO" jsname="OQ2Y6">
        # that intercept pointer events (feedback_login_stuck_reload.md).
        key = (step, current[:120])
        if key == stuck_key:
            stuck_count += 1
        else:
            stuck_key = key
            stuck_count = 1
        if stuck_count >= 3 and step in ("email", "password", "totp", "challenge_select"):
            logger.warning("Login stuck on %s for %d iterations — reloading URL", step, stuck_count)
            try:
                await page.reload(wait_until="domcontentloaded", timeout=15000)
                await asyncio.sleep(2)
            except Exception as e:
                logger.warning("Reload failed: %s", e)
            stuck_count = 0
            stuck_key = None
            continue

        logger.info("Login step: %s (url=%s)", step, current[:80])

        # Execute step
        if step == "email":
            await _handle_email_step(page, creds["email"])
        elif step == "password":
            await _handle_password_step(page, creds["password"])
        elif step == "totp":
            if creds["totp_secret"]:
                await _handle_totp_step(page, creds["totp_secret"])
            else:
                logger.error("2FA required but no TOTP secret")
                raise NeedAutoLogin(profile_name)
        elif step == "challenge_select":
            await _handle_challenge_selection(page)
        elif step == "consent":
            await _try_click_consent(page)
        elif step == "navigate_flow":
            logger.info("On myaccount — navigating to Flow")
            await page.goto(
                "https://labs.google/fx/tools/flow",
                wait_until="domcontentloaded",
                timeout=15000,
            )
            await asyncio.sleep(3)
            continue

        await asyncio.sleep(3)

    logger.error("Login not resolved after %ds", timeout)
    raise NeedAutoLogin(profile_name)


# ======================================================================
# Step handlers
# ======================================================================

async def _handle_email_step(page, email: str):
    """Enter email and click Next."""
    try:
        email_input = page.locator("input[type='email'], input#identifierId").first
        await email_input.click(timeout=2000)
        await asyncio.sleep(0.3)
        await page.keyboard.press("Control+a")
        await page.keyboard.type(email, delay=30)
        await asyncio.sleep(0.5)

        # Click Next
        next_btn = page.locator("#identifierNext button, #identifierNext").first
        await next_btn.click(timeout=3000)
        logger.info("Email entered: %s", email)
        await asyncio.sleep(3)
    except Exception as e:
        logger.warning("Email step failed: %s", e)


async def _handle_password_step(page, password: str):
    """Enter password and click Next."""
    try:
        pwd_input = page.locator("input[type='password']").first
        await pwd_input.click(timeout=2000)
        await asyncio.sleep(0.3)
        await page.keyboard.type(password, delay=30)
        await asyncio.sleep(0.5)

        # Click Next
        next_btn = page.locator("#passwordNext button, #passwordNext").first
        await next_btn.click(timeout=3000)
        logger.info("Password entered")
        await asyncio.sleep(3)
    except Exception as e:
        logger.warning("Password step failed: %s", e)


async def _handle_totp_step(page, totp_secret: str):
    """Enter TOTP code and click Next."""
    code = _generate_totp(totp_secret)
    if not code:
        return

    try:
        totp_input = page.locator("#totpPin, input[name='totpPin'], input[type='tel']").first
        await totp_input.click(timeout=2000)
        await asyncio.sleep(0.3)
        await page.keyboard.type(code, delay=30)
        await asyncio.sleep(0.5)

        # Click Next
        next_btn = page.locator("#totpNext button, #totpNext").first
        await next_btn.click(timeout=3000)
        logger.info("TOTP code entered")
        await asyncio.sleep(3)
    except Exception as e:
        logger.warning("TOTP step failed: %s", e)


async def _handle_challenge_selection(page):
    """On challenge selection page, pick 'Google Authenticator' option."""
    try:
        # Look for "authenticator" or TOTP option
        for sel in [
            "[data-challengetype='6']",  # TOTP challenge type
            "div:has-text('Authenticator')",
            "div:has-text('authenticator')",
            "div:has-text('verification code')",
        ]:
            try:
                btn = page.locator(sel).first
                if await btn.is_visible(timeout=1000):
                    await btn.click(timeout=2000)
                    logger.info("Selected authenticator challenge via: %s", sel)
                    await asyncio.sleep(2)
                    return
            except Exception:
                continue
    except Exception as e:
        logger.warning("Challenge selection failed: %s", e)


# ======================================================================
# Detection helpers
# ======================================================================

def _is_on_flow(url: str) -> bool:
    u = url.lower()
    return "labs.google" in u and ("tools/flow" in u or "/fx" in u)


async def _is_password_page(page) -> bool:
    """Detect Google password entry page."""
    try:
        pwd = page.locator("input[type='password']")
        if await pwd.count() > 0 and await pwd.first.is_visible(timeout=500):
            return True
    except Exception:
        pass
    return False


async def _is_email_page(page) -> bool:
    """Detect Google email/identifier entry page."""
    try:
        url = page.url.lower()
        if "signin/identifier" in url or "servicelogin" in url:
            email_input = page.locator("input[type='email'], input#identifierId")
            if await email_input.count() > 0 and await email_input.first.is_visible(timeout=500):
                return True
    except Exception:
        pass
    return False


async def _is_totp_page(page) -> bool:
    """Detect Google TOTP/2FA entry page."""
    try:
        totp_input = page.locator("#totpPin, input[name='totpPin']")
        if await totp_input.count() > 0 and await totp_input.first.is_visible(timeout=500):
            return True
    except Exception:
        pass
    # Also check for "Enter code" text on challenge pages
    try:
        url = page.url.lower()
        if "challenge/totp" in url:
            return True
    except Exception:
        pass
    return False


async def _is_challenge_selection(page) -> bool:
    """Detect Google challenge selection page (choose 2FA method)."""
    try:
        url = page.url.lower()
        if "challenge/selection" in url:
            return True
    except Exception:
        pass
    return False


# ======================================================================
# Account chooser / consent helpers
# ======================================================================

async def _try_click_account(page) -> bool:
    """Click an account in the Google account chooser."""
    for sel in ["[data-identifier]", "div[data-email]", "li[data-authuser]"]:
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


async def _try_click_consent(page) -> bool:
    """Click consent/allow buttons on Google OAuth consent screen."""
    for sel in ["button:has-text('Allow')", "button:has-text('Cho phép')",
                "button:has-text('Continue')", "button:has-text('Tiếp tục')",
                "#submit_approve_access", "button[id*='allow']"]:
        try:
            btn = page.locator(sel).first
            if await btn.is_visible(timeout=500):
                await btn.click(timeout=3000)
                logger.info("Clicked consent: %s", sel)
                return True
        except Exception:
            continue
    return False


# ======================================================================
# AIgglog fallback (subprocess) — kept for edge cases
# ======================================================================

def run_aigglog_sync(profile_name: str) -> bool:
    """Run AIgglog.py as subprocess (blocking). Fallback when Playwright login fails."""
    aigglog = Path(AIGGLOG_PATH)
    if not aigglog.exists():
        logger.error("AIgglog.py not found: %s", aigglog)
        return False

    profile_list = Path(PROFILE_LIST_FILE)
    if not profile_list.exists():
        logger.error("profiles_ultra.txt not found: %s", profile_list)
        return False

    env = os.environ.copy()
    env["GGLOG_PROFILE_HINT"] = profile_name
    env["FLOW_PROFILE_LIST_FILE"] = str(profile_list)
    env["GGLOG_MAX_ACCOUNTS"] = "1"
    env["GGLOG_STOP_AFTER_FIRST_SUCCESS"] = "1"
    env["GGLOG_REQUIRE_HINT_MATCH"] = "0"
    env["AIGGLOG_OPEN_FLOW_AFTER_LOGIN"] = "1"
    env["GGLOG_BROWSER"] = "chrome"

    logger.info("Running AIgglog.py: hint=%s", profile_name)

    try:
        proc = subprocess.run(
            ["python", str(aigglog)],
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(aigglog.parent),
        )

        if proc.stdout:
            for line in proc.stdout.strip().split("\n")[-10:]:
                logger.info("[AIgglog] %s", line.strip())
        if proc.stderr:
            for line in proc.stderr.strip().split("\n")[-5:]:
                logger.warning("[AIgglog:err] %s", line.strip())

        if proc.returncode == 0:
            logger.info("AIgglog login OK (exit=0)")
            return True
        else:
            logger.error("AIgglog FAILED (exit=%d)", proc.returncode)
            return False
    except subprocess.TimeoutExpired:
        logger.error("AIgglog timed out (120s)")
        return False
    except Exception:
        logger.error("AIgglog error", exc_info=True)
        return False
