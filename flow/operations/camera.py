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
    """Click a camera preset by name.

    Presets are shown as generic elements (thumbnails with text labels).
    Try multiple selector strategies.
    """
    # Strategy 1: generic element with exact text
    selectors = [
        f"[role='button']:has-text('{direction}')",
        f"button:has-text('{direction}')",
        # generic elements (Google uses custom elements)
        f"*:has-text('{direction}'):not(body):not(html):not(div)",
    ]

    for sel in selectors:
        try:
            el = page.locator(sel).first
            if await el.is_visible(timeout=2000):
                await el.click(timeout=3000)
                logger.info("Clicked camera preset: %s via %s", direction, sel)
                await asyncio.sleep(0.5)
                return True
        except Exception:
            continue

    # Strategy 2: Use getByText for more flexible matching
    try:
        el = page.get_by_text(direction, exact=False).first
        if await el.is_visible(timeout=2000):
            await el.click(timeout=3000)
            logger.info("Clicked camera preset via getByText: %s", direction)
            await asyncio.sleep(0.5)
            return True
    except Exception:
        pass

    # Strategy 3: Case-insensitive search in all visible text
    try:
        el = page.locator("*:visible").filter(
            has_text=re.compile(re.escape(direction), re.IGNORECASE)
        ).last  # last = most nested/specific element
        if await el.is_visible(timeout=2000):
            await el.click(timeout=3000)
            logger.info("Clicked camera preset via regex: %s", direction)
            await asyncio.sleep(0.5)
            return True
    except Exception:
        pass

    logger.error("Could not find camera preset: %s", direction)
    return False
