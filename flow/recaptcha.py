"""reCAPTCHA detection -- identify and handle Google's bot protection."""

import asyncio
import logging

logger = logging.getLogger(__name__)


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


async def detect_recaptcha_in_network(client) -> bool:
    """Check if recent network responses indicate reCAPTCHA/bot blocking.

    Signals:
    - HTTP 403 on generation/operations endpoints
    - HTTP 429 (rate limited)
    - Response bodies containing "captcha" or "bot"
    """
    calls = getattr(client, "_calls", [])

    for call in reversed(calls[-50:]):
        url = str(call.get("url", "")).lower()
        status = call.get("status", 0)

        # 403/429 on critical endpoints
        if status in (403, 429) and ("operations/" in url or "generate" in url or "trpc" in url):
            logger.warning("Bot block signal: HTTP %d on %s", status, url[:80])
            return True

        # Response body contains captcha indicators
        body = call.get("body", "")
        if isinstance(body, str) and ("captcha" in body.lower() or "bot" in body.lower()):
            logger.warning("Bot block signal: captcha text in response body")
            return True

    return False


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
    pass
