"""reCAPTCHA detection -- identify and handle Google's bot protection."""

import asyncio
import logging
from collections.abc import Callable
from typing import Literal

logger = logging.getLogger(__name__)

RecaptchaKind = Literal["v3_invisible", "v2_visible"]

_RECAPTCHA_V3_TOKENS = (
    "recaptcha/enterprise/clr",
    "recaptcha/enterprise/reload",
    "recaptcha/enterprise/webworker",
    "recaptcha/enterprise/token",
)
_BLOCKED_ENDPOINT_TOKENS = (
    "operations/",
    "generate",
    "trpc",
    "batchasync",
    "aisandbox-pa",
)


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


def _is_recaptcha_url(url: str) -> bool:
    return "recaptcha" in url.lower()


def _find_first_matching_call(
    calls: list[dict],
    predicate: Callable[[dict], bool],
) -> dict | None:
    for call in calls:
        if predicate(call):
            return call
    return None


def _is_blocked_generation_call(call: dict) -> bool:
    url_lower = str(call.get("url", "")).lower()
    status = int(call.get("status", 0) or 0)
    if status not in (403, 429):
        return False
    return any(token in url_lower for token in _BLOCKED_ENDPOINT_TOKENS)


def _nearest_recaptcha_call(calls: list[dict], blocked_call: dict) -> dict | None:
    blocked_ts = blocked_call.get("ts")
    if not isinstance(blocked_ts, (int, float)):
        return None

    nearest_call: dict | None = None
    nearest_delta: float | None = None
    for call in calls:
        url = str(call.get("url", ""))
        if not _is_recaptcha_url(url):
            continue

        call_ts = call.get("ts")
        if not isinstance(call_ts, (int, float)):
            continue

        delta = abs(float(blocked_ts) - float(call_ts))
        if delta > 30:
            continue
        if nearest_delta is None or delta < nearest_delta:
            nearest_call = call
            nearest_delta = delta

    return nearest_call


def _find_recaptcha_signal(calls: list[dict]) -> tuple[RecaptchaKind, dict] | None:
    recent_calls = calls[-50:]

    direct_signal_checks = (
        (
            "v3_invisible",
            lambda call: any(
                token in str(call.get("url", "")).lower()
                for token in _RECAPTCHA_V3_TOKENS
            ),
        ),
        (
            "v2_visible",
            lambda call: "recaptcha/api2/anchor" in str(call.get("url", "")).lower(),
        ),
        (
            "v3_invisible",
            lambda call: int(call.get("status", 0) or 0) in (403, 429)
            and _is_recaptcha_url(str(call.get("url", ""))),
        ),
    )
    for kind, predicate in direct_signal_checks:
        call = _find_first_matching_call(recent_calls, predicate)
        if call is None:
            continue

        url = str(call.get("url", ""))
        logger.warning("reCAPTCHA network signal: kind=%s url=%s", kind, url[:120])
        return kind, call

    for call in recent_calls:
        if not _is_blocked_generation_call(call):
            continue

        recaptcha_call = _nearest_recaptcha_call(recent_calls, call)
        if recaptcha_call is None:
            continue

        logger.warning(
            "reCAPTCHA-adjacent block: HTTP %s on %s",
            call.get("status", 0),
            str(call.get("url", ""))[:120],
        )
        return "v3_invisible", recaptcha_call

    return None


def first_recaptcha_call(client) -> dict | None:
    """Return the reCAPTCHA-related call that triggered classification."""
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

    def __init__(self, kind: RecaptchaKind | str, url: str | None = None):
        self.url = url

        if kind in ("v3_invisible", "v2_visible"):
            self.kind = kind
            message = f"reCAPTCHA detected ({kind})"
            if url:
                message = f"{message}: {url}"
        else:
            self.kind = "v3_invisible"
            message = str(kind)

        super().__init__(message)
