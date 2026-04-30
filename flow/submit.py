"""Submit pipeline -- click generate button, confirm submission accepted.

Submit button identity (verified live on /project/ and /edit/ views, 2026-04-19):
  <button ...>
    <i ...>arrow_forward</i>    ← icon text EXACT, locale-independent
    Tạo | Create                  ← localized label
  </button>

The ONLY stable structural signal is the child ``<i>`` whose textContent is
exactly ``arrow_forward`` (Material Icon ligature). Do NOT rely on:
  * aria-label (empty on live DOM)
  * data-testid (none)
  * button innerText (locale-dependent, contains both icon text + label)
  * fuzzy ``:has-text`` substring matches (can match the Camera mode
    switcher's innerText "videocam\\nCamera" — see B26 Session Report)

On /edit/ there are TWO buttons with ``<i>arrow_forward</i>``: one is
disabled (visibility-hidden decorative), one is the real submit. The
enabled + visible check picks the right one.
"""

import asyncio
import logging

from flow.failure_capture import capture_failure_nonblocking

logger = logging.getLogger(__name__)

# Exact-text selector: button that contains a child ``<i>`` whose text is
# EXACTLY "arrow_forward". Playwright's ``:text-is`` engine does the exact
# match (trimmed), so no fuzzy overlap with other Material Icons. Verified
# unique on both /project/ (L1 generate) and /edit/ (L2 extend/insert/
# remove/camera) composers.
SUBMIT_SELECTORS = [
    "button:has(i:text-is('arrow_forward'))",
]


async def click_submit(page, timeout_ms: int = 3000, scope: str | None = None) -> bool:
    """Find and click the submit / generate button (exact-text match).

    Finds buttons via ``button:has(i:text-is('arrow_forward'))`` — exact
    match on the icon's textContent. On /edit/ there are 2 such buttons;
    disabled one is skipped by ``is_enabled`` check.

    Args:
        page:        Playwright Page.
        timeout_ms:  Click timeout (default 3000 ms).
        scope:       Optional CSS selector prepended to scope the search
                     (e.g. ``[data-scroll-state='START']`` for a panel).

    Returns True if a click (or Ctrl+Enter fallback) was performed.
    """
    for base_selector in SUBMIT_SELECTORS:
        selector = f"{scope} {base_selector}" if scope else base_selector
        try:
            locator = page.locator(selector)
            count = await locator.count()
            logger.debug("Submit selector %s: count=%d", selector, count)
            for i in range(count):
                btn = locator.nth(i)
                try:
                    vis = await btn.is_visible(timeout=500)
                    ena = await btn.is_enabled(timeout=300) if vis else False
                    logger.debug("  btn[%d]: vis=%s ena=%s", i, vis, ena)
                    if not vis or not ena:
                        continue
                    await btn.click(timeout=timeout_ms, force=True)
                    try:
                        label = (await btn.inner_text()).strip()[:30]
                    except Exception:
                        label = ""
                    logger.info("Submit clicked via: %s [%d] label=%s", selector, i, label)
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
    scope: str | None = None,
    failure_kind: str = "submit_not_confirmed",
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

    clicked = await click_submit(page, scope=scope)
    if not clicked:
        logger.error("Failed to find/click submit button (scope=%s)", scope)
        await capture_failure_nonblocking(
            client,
            failure_kind,
            extra={"reason": "click_submit_failed", "scope": scope or ""},
        )
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
    await capture_failure_nonblocking(
        client,
        failure_kind,
        extra={
            "reason": "submit_not_confirmed",
            "scope": scope or "",
            "timeout_sec": timeout_sec,
        },
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
