"""Remove Object — Level 2 operation.

Navigates to edit URL, clicks Remove, draws bbox (REQUIRED),
submits, waits, downloads. No prompt needed.
"""

import asyncio
import logging

from flow.submit import submit_with_confirmation
from flow.operations._base import (
    navigate_to_edit,
    wait_for_video_loaded,
    click_action_button,
    count_visible_cards,
    finalize_operation,
)

logger = logging.getLogger(__name__)

# Remove button texts (EN + VI)
REMOVE_BUTTONS = ["Remove", "Xoá"]

# Remove icon fallback
REMOVE_ICON_SELECTOR = "button:has(span:has-text('ink_eraser'))"


async def remove_object(
    client,
    job: dict,
    bbox: dict | None = None,
) -> dict:
    """Execute remove-object operation.

    Steps:
    1. Navigate to edit URL
    2. Wait for video to load
    3. Click "Remove" button
    4. Draw bbox on video canvas (REQUIRED — selects what to remove)
    5. Submit and confirm (no prompt needed)
    6. Wait + Download + Return metadata

    Args:
        client: FlowClient instance
        job: Job dict with edit_url/project_url/media_id
        bbox: {x, y, w, h} normalized 0-1 — REQUIRED for remove

    Returns: Result dict
    """
    page = client.page

    # Step 1: Navigate
    edit_url_val, project_id, locale = await navigate_to_edit(client, job)

    # Step 2: Wait for video
    await wait_for_video_loaded(page)

    # Step 3: Click Remove button
    clicked = await click_action_button(page, REMOVE_BUTTONS)
    if not clicked:
        try:
            icon_btn = page.locator(REMOVE_ICON_SELECTOR).first
            if await icon_btn.is_visible(timeout=2000):
                await icon_btn.click(timeout=3000)
                clicked = True
                logger.info("Clicked Remove via icon fallback")
                await asyncio.sleep(0.5)
        except Exception:
            pass

    if not clicked:
        raise RuntimeError("Failed to find Remove button")

    # Step 4: Draw bbox (REQUIRED for remove)
    if not bbox:
        # Default: center region if no bbox provided
        bbox = {"x": 0.25, "y": 0.25, "w": 0.5, "h": 0.5}
        logger.warning("No bbox provided for remove — using center default")

    await _draw_bbox(page, bbox)

    # Step 5: Submit (no prompt for remove)
    before_cards = await count_visible_cards(page)
    client.clear_captures()

    confirmed = await submit_with_confirmation(
        client,
        before_card_count=before_cards,
    )
    if not confirmed:
        raise RuntimeError("Remove submit not confirmed")

    # Step 6: Wait + Download + Return
    return await finalize_operation(
        client, job,
        job_type="remove-object",
        project_id=project_id,
        locale=locale,
        download_prefix="rm",
    )


async def _draw_bbox(page, bbox: dict):
    """Draw a bounding box on the video canvas by mouse drag.

    bbox: {x, y, w, h} — normalized 0-1 coordinates.
    """
    try:
        video_rect = await page.evaluate("""() => {
            const video = document.querySelector('video');
            if (!video) return null;
            const rect = video.getBoundingClientRect();
            return {left: rect.left, top: rect.top, width: rect.width, height: rect.height};
        }""")

        if not video_rect:
            logger.warning("No video element found for bbox drawing")
            return

        vl = video_rect["left"]
        vt = video_rect["top"]
        vw = video_rect["width"]
        vh = video_rect["height"]

        x = bbox.get("x", 0.25)
        y = bbox.get("y", 0.25)
        w = bbox.get("w", 0.5)
        h = bbox.get("h", 0.5)

        start_x = vl + x * vw
        start_y = vt + y * vh
        end_x = vl + (x + w) * vw
        end_y = vt + (y + h) * vh

        await page.mouse.move(start_x, start_y)
        await page.mouse.down()
        await asyncio.sleep(0.1)
        steps = 5
        for i in range(1, steps + 1):
            px = start_x + (end_x - start_x) * i / steps
            py = start_y + (end_y - start_y) * i / steps
            await page.mouse.move(px, py)
            await asyncio.sleep(0.05)
        await page.mouse.up()

        logger.info("Drew remove bbox: x=%.2f y=%.2f w=%.2f h=%.2f", x, y, w, h)
        await asyncio.sleep(0.5)

    except Exception as e:
        logger.warning("Failed to draw bbox: %s", e)
