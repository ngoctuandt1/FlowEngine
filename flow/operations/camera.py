"""Camera Move — Level 2 operation.

Navigates to edit URL, clicks Camera, selects a camera preset
(motion or position), submits, waits, downloads.

Camera mode is DIFFERENT from other operations:
- Replaces the composer entirely with a visual preset grid
- No text prompt, no model selector
- User picks a motion preset (e.g. "Dolly in") or position (e.g. "Center")
"""

import asyncio
import logging
import re

from flow.submit import submit_with_confirmation
from flow.operations._base import (
    navigate_to_edit,
    wait_for_video_loaded,
    click_action_button,
    count_visible_cards,
    finalize_operation,
)

logger = logging.getLogger(__name__)

# Camera button (same in EN + VI)
CAMERA_BUTTONS = ["Camera"]
CAMERA_ICON_SELECTOR = "button:has(span:has-text('videocam'))"

# Known camera presets (DOM generic text, EN)
CAMERA_MOTION_PRESETS = [
    "Dolly in", "Dolly out", "Orbit left", "Orbit right",
    "Orbit up", "Orbit low", "Dolly in zoom out", "Dolly out zoom in",
]

CAMERA_POSITION_PRESETS = [
    "Center", "Left", "Right", "High", "Low", "Closer", "Further",
]

ALL_PRESETS = CAMERA_MOTION_PRESETS + CAMERA_POSITION_PRESETS


async def camera_move(
    client,
    job: dict,
    direction: str = "Dolly in",
) -> dict:
    """Execute camera-move operation.

    Steps:
    1. Navigate to edit URL
    2. Wait for video to load
    3. Click "Camera" button (replaces composer with preset grid)
    4. Select the correct tab (Camera motion vs Camera position)
    5. Click the preset thumbnail
    6. Submit
    7. Wait + Download + Return metadata

    Args:
        client: FlowClient instance
        job: Job dict with edit_url/project_url/media_id
        direction: Camera preset name (e.g. "Dolly in", "Center", "Orbit left")

    Returns: Result dict
    """
    page = client.page

    # Step 1: Navigate
    edit_url_val, project_id, locale = await navigate_to_edit(client, job)

    # Step 2: Wait for video
    await wait_for_video_loaded(page)

    # Step 3: Click Camera button
    clicked = await click_action_button(page, CAMERA_BUTTONS)
    if not clicked:
        try:
            icon_btn = page.locator(CAMERA_ICON_SELECTOR).first
            if await icon_btn.is_visible(timeout=2000):
                await icon_btn.click(timeout=3000)
                clicked = True
                logger.info("Clicked Camera via icon fallback")
                await asyncio.sleep(0.5)
        except Exception:
            pass

    if not clicked:
        raise RuntimeError("Failed to find Camera button")

    await asyncio.sleep(1)  # Wait for preset grid to render

    # Step 4: Select correct tab
    is_position = direction in CAMERA_POSITION_PRESETS
    tab_name = "Camera position" if is_position else "Camera motion"

    try:
        tab = page.locator(f"[role='tab']:has-text('{tab_name}')").first
        if await tab.is_visible(timeout=2000):
            await tab.click(timeout=3000)
            logger.info("Switched to tab: %s", tab_name)
            await asyncio.sleep(0.5)
    except Exception:
        # Tab might already be active or not exist — proceed
        logger.warning("Could not switch to tab %s — trying preset anyway", tab_name)

    # Step 5: Click preset thumbnail
    preset_clicked = await _click_preset(page, direction)
    if not preset_clicked:
        raise RuntimeError(f"Failed to find camera preset: {direction}")

    # Step 6: Submit
    before_cards = await count_visible_cards(page)
    client.clear_captures()

    confirmed = await submit_with_confirmation(
        client,
        before_card_count=before_cards,
    )
    if not confirmed:
        raise RuntimeError("Camera submit not confirmed")

    # Step 7: Wait + Download + Return
    return await finalize_operation(
        client, job,
        job_type="camera-move",
        project_id=project_id,
        locale=locale,
        download_prefix="cam",
    )


async def _click_preset(page, direction: str) -> bool:
    """Click a camera preset by name and verify it becomes active.

    Three strategies ordered most-reliable → most-fragile. Each strategy
    clicks + calls `_verify_preset_selected` before returning True.
    If a strategy clicks "something" that matched the selector but is not
    the real preset button, the verify step rejects it and the function
    falls through to the next strategy. On full exhaustion, logs ERROR
    and returns False (caller raises RuntimeError in `camera_move`).

    Partial-text matching is explicitly avoided — direction="Low" must
    NOT match button "Lower". See `docs/FLOW_UI_REFERENCE.md`
    §Camera Preset Selection & Active State for selector rationale.
    """
    # Strategy 1: aria-label exact match (most reliable; locale-stable)
    try:
        el = page.locator(f"[aria-label='{direction}']").first
        if await el.is_visible(timeout=2000):
            await el.click(timeout=3000)
            logger.info("Clicked preset via aria-label: %s", direction)
            await asyncio.sleep(0.5)
            if await _verify_preset_selected(page, direction):
                return True
            logger.debug("Strategy 1 clicked but not verified; falling through")
    except Exception as e:
        logger.debug("Strategy 1 (aria-label) failed for %s: %s", direction, e)

    # Strategy 2: role=button filtered by anchored regex (exact-text match)
    # Using `^...$` anchors prevents partial match (e.g. "Low" → "Lower").
    try:
        exact_pattern = re.compile(f"^{re.escape(direction)}$")
        el = page.locator("[role='button']").filter(has_text=exact_pattern).first
        if await el.is_visible(timeout=2000):
            await el.click(timeout=3000)
            logger.info("Clicked preset via role=button exact: %s", direction)
            await asyncio.sleep(0.5)
            if await _verify_preset_selected(page, direction):
                return True
            logger.debug("Strategy 2 clicked but not verified; falling through")
    except Exception as e:
        logger.debug("Strategy 2 (role=button) failed for %s: %s", direction, e)

    # Strategy 3: Playwright get_by_text with exact=True (NOT partial)
    try:
        el = page.get_by_text(direction, exact=True).first
        if await el.is_visible(timeout=2000):
            await el.click(timeout=3000)
            logger.info("Clicked preset via get_by_text(exact=True): %s", direction)
            await asyncio.sleep(0.5)
            if await _verify_preset_selected(page, direction):
                return True
            logger.debug("Strategy 3 clicked but not verified; falling through")
    except Exception as e:
        logger.debug("Strategy 3 (get_by_text) failed for %s: %s", direction, e)

    logger.error("Could not click+verify camera preset: %s", direction)
    return False


async def _verify_preset_selected(page, direction: str) -> bool:
    """Verify the named preset is in an active/selected state after click.

    Checks union of common SPA conventions via a single `page.evaluate`:
    - `aria-pressed="true"` (Material / Radix toggle)
    - `aria-selected="true"` (tablist / listbox)
    - class matches `active|selected|pressed` (case-insensitive)
    - parent class matches `active|selected`

    Returns True if any signal fires, False otherwise. Returns False
    (not raises) on `page.evaluate` failure — caller treats as unverified
    and can try the next strategy.
    """
    try:
        is_selected = await page.evaluate(
            """(direction) => {
                const els = document.querySelectorAll('[aria-label], [role="button"], button');
                for (const el of els) {
                    const text = el.textContent?.trim() || '';
                    const label = el.getAttribute('aria-label') || '';
                    if (text === direction || label === direction) {
                        if (el.getAttribute('aria-pressed') === 'true') return true;
                        if (el.getAttribute('aria-selected') === 'true') return true;
                        const cls = el.className || '';
                        if (/active|selected|pressed/i.test(cls)) return true;
                        const parent = el.parentElement;
                        if (parent && /active|selected/i.test(parent.className || '')) return true;
                    }
                }
                return false;
            }""",
            direction,
        )
        if is_selected:
            logger.info("Preset verified selected: %s", direction)
            return True
        logger.warning("Preset clicked but not verified active: %s", direction)
        return False
    except Exception as e:
        logger.warning("Preset verify failed for %s: %s", direction, e)
        return False
