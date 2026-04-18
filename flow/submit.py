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
            # Check ALL matching buttons, not just .first — skip disabled ones
            locator = page.locator(selector)
            count = await locator.count()
            logger.debug("Submit selector %s: count=%d", selector, count)
            for i in range(count):
                btn = locator.nth(i)
                try:
                    vis = await btn.is_visible(timeout=500)
                    ena = await btn.is_enabled(timeout=300) if vis else False
                    try:
                        text = await btn.inner_text()
                    except Exception:
                        text = ""
                    skip = bool(_SKIP_PATTERN.search(text)) if text else False
                    logger.debug(
                        "  btn[%d]: vis=%s ena=%s skip=%s text=%s",
                        i, vis, ena, skip, text.strip()[:30],
                    )
                    if not vis or not ena or skip:
                        continue
                    await btn.click(timeout=timeout_ms, force=True)
                    logger.info("Submit clicked via: %s [%d] text=%s", selector, i, text.strip()[:30])
                    return True
                except Exception as e:
                    logger.debug("  btn[%d] error: %s", i, e)
                    continue
        except Exception as e:
            logger.debug("Submit selector %s error: %s", selector, e)
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

    # Snapshot call count BEFORE submit so we only check NEW calls
    calls_before = len(getattr(client, "_calls", []))
    gen_id_before = getattr(client, "_gen_id", None)

    clicked = await click_submit(page)
    if not clicked:
        logger.error("Failed to find/click submit button")
        return False

    poll_interval = 0.5
    deadline = asyncio.get_event_loop().time() + timeout_sec

    while asyncio.get_event_loop().time() < deadline:
        # Signal 1: gen_id captured from network intercept (NEW since submit)
        gen_id = getattr(client, "_gen_id", None)
        if gen_id and gen_id != gen_id_before:
            logger.info("Submit confirmed: gen_id=%s", gen_id)
            return True

        # Signal 2: NEW operations API call detected (after submit)
        calls = getattr(client, "_calls", [])
        new_calls = calls[calls_before:]
        ops_calls = [c for c in new_calls if "operations/" in c.get("url", "")]
        if ops_calls:
            logger.info(
                "Submit confirmed: operations API call detected (%d new calls)",
                len(ops_calls),
            )
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

    # Timeout -- submit NOT confirmed. Log diagnostic info.
    calls = getattr(client, "_calls", [])
    new_calls = calls[calls_before:]
    logger.error(
        "Submit NOT confirmed after %.0fs — "
        "new_api_calls=%d, gen_id=%s, cards=%d, url=%s",
        timeout_sec,
        len(new_calls),
        getattr(client, "_gen_id", None),
        await _count_cards(page),
        page.url[:100],
    )
    return False


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
