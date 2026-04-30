"""reCAPTCHA detection -- identify and handle Google's bot protection."""

import asyncio
import logging
from typing import Literal

logger = logging.getLogger(__name__)

RecaptchaKind = Literal["v3_invisible", "v2_visible"]


async def detect_recaptcha(page) -> bool:
    """Check if a reCAPTCHA challenge is currently visible on the page.

    Detection signals:
    1. iframe with src containing "recaptcha" or "captcha"
    2. Elements with text "I'm not a robot" / "Toi khong phai la robot"
    3. Elements with class containing "recaptcha"
    4. Google "unusual traffic" / "luu luong bat thuong" message
    """
    try:
        result = await page.evaluate("""() => {
            // Check iframes — only count as challenge if large enough
            // (reCAPTCHA v3 badge iframes are tiny ~0x0 or ~32x32)
            const iframes = document.querySelectorAll('iframe');
            for (const iframe of iframes) {
                const src = (iframe.src || '').toLowerCase();
                if (src.includes('recaptcha') || src.includes('captcha') || src.includes('hcaptcha')) {
                    const rect = iframe.getBoundingClientRect();
                    // Only a real challenge if iframe is visible and reasonably sized
                    if (rect.width > 100 && rect.height > 100) {
                        return {detected: true, type: 'iframe_challenge', src: src.substring(0, 100), w: rect.width, h: rect.height};
                    }
                    // Small iframe = just a badge, not blocking
                }
            }

            // Check body text for captcha/bot signals
            const bodyText = document.body.innerText || '';
            // Only match text that indicates an ACTIVE challenge, not passive mentions
            const captchaPatterns = [
                /i'm not a robot/i,
                /unusual traffic from your computer/i,
                /verify you('re| are) (not a bot|a human|human)/i,
            ];

            for (const pattern of captchaPatterns) {
                if (pattern.test(bodyText)) {
                    return {detected: true, type: 'text', pattern: pattern.source};
                }
            }

            // Check for recaptcha elements — only if they are visible and large
            // (hidden/invisible reCAPTCHA v3 badges don't count as challenges)
            const recaptchaEls = document.querySelectorAll('[class*="recaptcha"], [id*="recaptcha"], .g-recaptcha');
            for (const el of recaptchaEls) {
                const rect = el.getBoundingClientRect();
                const style = getComputedStyle(el);
                if (style.display !== 'none' && style.visibility !== 'hidden'
                    && rect.width > 100 && rect.height > 60) {
                    return {detected: true, type: 'element_challenge', w: rect.width, h: rect.height};
                }
            }

            return {detected: false};
        }""")

        if result.get("detected"):
            logger.warning("reCAPTCHA detected: type=%s", result.get("type"))
            return True

        return False
    except Exception:
        return False


def _classify_recaptcha_url(url: str) -> RecaptchaKind | None:
    url_lower = url.lower()
    if "recaptcha" not in url_lower:
        return None
    if "recaptcha/api2/anchor" in url_lower:
        return "v2_visible"
    if any(
        token in url_lower
        for token in (
            "recaptcha/enterprise/clr",
            "recaptcha/enterprise/reload",
            "recaptcha/enterprise/webworker",
            "recaptcha/enterprise/token",
        )
    ):
        return "v3_invisible"
    return "v3_invisible"


def _nearest_adjacent_recaptcha_call(calls: list[dict], blocked_call: dict) -> dict | None:
    blocked_ts = blocked_call.get("ts")
    if not isinstance(blocked_ts, (int, float)):
        return None

    nearest: dict | None = None
    nearest_delta: float | None = None
    for call in calls:
        url = str(call.get("url", ""))
        if _classify_recaptcha_url(url) is None:
            continue
        call_ts = call.get("ts")
        if not isinstance(call_ts, (int, float)):
            continue
        delta = abs(float(blocked_ts) - float(call_ts))
        if delta > 30:
            continue
        if nearest_delta is None or delta < nearest_delta:
            nearest = call
            nearest_delta = delta
    return nearest


def _find_recaptcha_signal(calls: list[dict]) -> tuple[RecaptchaKind, dict] | None:
    recent_calls = calls[-50:]
    for call in reversed(recent_calls):
        url = str(call.get("url", ""))
        status = int(call.get("status", 0) or 0)
        kind = _classify_recaptcha_url(url)
        if kind is not None:
            logger.warning("reCAPTCHA network signal: kind=%s url=%s", kind, url[:120])
            return kind, call

        if status in (403, 429):
            adjacent_call = _nearest_adjacent_recaptcha_call(recent_calls, call)
            if adjacent_call is not None:
                blocked_url = str(call.get("url", ""))
                logger.warning(
                    "reCAPTCHA-adjacent block: HTTP %d on %s",
                    status,
                    blocked_url[:120],
                )
                return "v3_invisible", adjacent_call

        body = call.get("body", "")
        if isinstance(body, str) and ("captcha" in body.lower() or "bot" in body.lower()):
            logger.warning("Bot block signal: captcha text in response body")
            return "v3_invisible", call

    return None


def first_recaptcha_call(client) -> dict | None:
    """Return the latest call dict that triggered network reCAPTCHA detection."""
    calls = getattr(client, "_calls", [])
    signal = _find_recaptcha_signal(calls)
    if signal is None:
        return None
    return signal[1]


async def detect_recaptcha_in_network(client) -> RecaptchaKind | None:
    """Classify recent network responses that indicate reCAPTCHA/bot blocking."""
    calls = getattr(client, "_calls", [])
    signal = _find_recaptcha_signal(calls)
    if signal is None:
        return None
    return signal[0]


async def handle_recaptcha(client) -> bool:
    """Attempt to handle a reCAPTCHA situation.

    Current strategy: wait and hope it resolves (user may need to
    manually solve it). In production, this could trigger a notification
    to the operator.

    Returns True if the captcha seems resolved, False if still blocked.
    """
    page = client.page
    logger.warning("reCAPTCHA handling: waiting for manual resolution...")

    # Wait up to 120 seconds, checking every 10s if captcha is gone
    for i in range(12):
        await asyncio.sleep(10)

        if not await detect_recaptcha(page):
            logger.info("reCAPTCHA resolved after %ds", (i + 1) * 10)
            return True

        logger.info("reCAPTCHA still present (%ds elapsed)...", (i + 1) * 10)

    logger.error("reCAPTCHA not resolved after 120s -- aborting")
    return False


class RecaptchaError(Exception):
    """Raised when reCAPTCHA blocks an operation."""

    def __init__(self, kind: RecaptchaKind, url: str = ""):
        self.kind = kind
        self.url = url
        message = f"reCAPTCHA detected ({kind})"
        if url:
            message = f"{message}: {url}"
        super().__init__(message)
