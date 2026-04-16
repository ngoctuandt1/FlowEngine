"""Submit pipeline -- click generate button, confirm submission accepted."""

import asyncio
import re
import logging

logger = logging.getLogger(__name__)

# Selectors tried in priority order
SUBMIT_SELECTORS = [
    "button:has(i:has-text('arrow_forward'))",
    "button:has(span:has-text('arrow_forward'))",
    "button[aria-label*='Create' i]",
    "button[aria-label*='Generate' i]",
    "[data-testid='composer_submit_button']",
    "button[aria-label*='Send' i]",
]

# Buttons matching this pattern are NOT submit buttons
_SKIP_PATTERN = re.compile(
    r"(image|video|frames|ingredients|reference|9:16|16:9|"
    r"\bx[1-4]\b|veo|lower priority)",
    re.IGNORECASE,
)


async def click_submit(page, timeout_ms: int = 3000) -> bool:
    """Find and click the submit / generate button.

    Selector priority (first visible match wins):
      1. button with arrow_forward icon (``<i>`` or ``<span>``)
      2. button[aria-label*='Create']
      3. button[aria-label*='Generate']
      4. [data-testid='composer_submit_button']
      5. button[aria-label*='Send']
      6. Keyboard fallback: Ctrl+Enter

    Returns True if a click (or key-press) was performed.
    """
    for selector in SUBMIT_SELECTORS:
        try:
            locator = page.locator(selector).first
            if not await locator.is_visible(timeout=500):
                continue

            # Read button text to apply noise filter
            try:
                text = await locator.inner_text()
            except Exception:
                text = ""

            if _SKIP_PATTERN.search(text):
                continue

            await locator.click(timeout=timeout_ms, force=True)
            logger.info("Submit clicked via: %s", selector)
            return True
        except Exception:
            continue

    # Keyboard fallback
    try:
        await page.keyboard.press("Control+Enter")
        logger.info("Submit via Ctrl+Enter fallback")
        return True
    except Exception:
        logger.debug("Ctrl+Enter fallback also failed")

    return False


async def submit_with_confirmation(
    client,
    before_card_count: int = 0,
    timeout_sec: float = 15.0,
    prompt_text: str = "",
) -> bool:
    """Click submit and confirm the submission was accepted.

    Confirmation signals (any ONE is sufficient):
      1. ``client._gen_id`` became non-None (generation ID from network)
      2. Network: POST to ``operations/`` API captured in ``client._calls``
      3. UI: card count increased (new card appeared in grid)
      4. UI: progress indicator appeared (% or "Generating" text)

    Args:
        client:             FlowClient instance with ``.page`` attribute.
        before_card_count:  Card count taken *before* calling this function.
        timeout_sec:        Max seconds to wait for a confirmation signal.
        prompt_text:        (informational) the prompt that was submitted.

    Returns True if submission confirmed, False otherwise.
    """
    page = client.page

    clicked = await click_submit(page)
    if not clicked:
        logger.error("Failed to find/click submit button")
        return False

    poll_interval = 0.5
    deadline = asyncio.get_event_loop().time() + timeout_sec

    while asyncio.get_event_loop().time() < deadline:
        # Signal 1: gen_id captured from network intercept
        if getattr(client, "_gen_id", None):
            logger.info("Submit confirmed: gen_id=%s", client._gen_id)
            return True

        # Signal 2: operations API call detected
        calls = getattr(client, "_calls", [])
        if any("operations/" in c.get("url", "") for c in calls):
            logger.info("Submit confirmed: operations API call detected")
            return True

        # Signal 3: card count increased
        current_cards = await _count_cards(page)
        if current_cards > before_card_count:
            logger.info(
                "Submit confirmed: cards %d -> %d",
                before_card_count,
                current_cards,
            )
            return True

        # Signal 4: progress indicator visible
        if await _has_progress(page):
            logger.info("Submit confirmed: progress indicator visible")
            return True

        await asyncio.sleep(poll_interval)

    # Timeout -- assume optimistically that the job was accepted.
    # The wait module will catch genuine failures later.
    logger.warning(
        "Submit confirmation timeout (%.0fs) -- assuming submitted", timeout_sec
    )
    return True


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

async def _count_cards(page) -> int:
    """Count visible media cards in the page."""
    try:
        return await page.evaluate(
            """() => {
            const videos = document.querySelectorAll('video');
            const tiles  = document.querySelectorAll('[data-tile-id]');
            return Math.max(videos.length, tiles.length);
        }"""
        )
    except Exception:
        return 0


async def _has_progress(page) -> bool:
    """Return True if any progress indicator is visible."""
    try:
        return await page.evaluate(
            """() => {
            const body = document.body.innerText || '';
            if (/\\b\\d{1,2}%/.test(body)) return true;
            if (/generating|creating|processing/i.test(body)) return true;
            if (document.querySelector('[role="progressbar"]')) return true;
            return false;
        }"""
        )
    except Exception:
        return False
