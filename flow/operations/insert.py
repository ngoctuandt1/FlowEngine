"""Insert Object -- Level 2 operation.

Navigates to edit URL, clicks Insert, draws bbox, types prompt,
submits, waits, downloads.
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

# Insert button texts (EN + VI)
INSERT_BUTTONS = ["Insert", "Chen"]

# Insert icon fallback
INSERT_ICON_SELECTOR = "button:has(span:has-text('add_box'))"


async def insert_object(
    client,
    job: dict,
    prompt: str = "",
    bbox: dict | None = None,
) -> dict:
    """Execute insert-object operation.

    Steps:
    1. Navigate to edit URL
    2. Wait for video to load
    3. Click "Insert" button
    4. Draw bbox on video canvas (if provided)
    5. Type description prompt (optional)
    6. Submit and confirm
    7. Wait + Download + Return metadata

    Args:
        client: FlowClient instance
        job: Job dict with edit_url/project_url/media_id
        prompt: Object description
        bbox: {x, y, w, h} normalized 0-1 (optional)

    Returns: Result dict
    """
    page = client.page

    # Step 1: Navigate
    edit_url_val, project_id, locale = await navigate_to_edit(client, job)

    # Step 2: Wait for video
    await wait_for_video_loaded(page)

    # Step 3: Click Insert button
    clicked = await click_action_button(page, INSERT_BUTTONS)
    if not clicked:
        try:
            icon_btn = page.locator(INSERT_ICON_SELECTOR).first
            if await icon_btn.is_visible(timeout=2000):
                await icon_btn.click(timeout=3000)
                clicked = True
                logger.info("Clicked Insert via icon fallback")
                await asyncio.sleep(0.5)
        except Exception:
            pass

    if not clicked:
        raise RuntimeError("Failed to find Insert button")

    # Step 4: Draw bbox (optional)
    if bbox:
        await _draw_bbox(page, bbox)

    # Step 5: Type prompt (optional)
    if prompt:
        await _type_insert_prompt(page, prompt)

    # Step 6: Submit
    before_cards = await count_visible_cards(page)
    client.clear_captures()

    confirmed = await submit_with_confirmation(
        client,
        before_card_count=before_cards,
        prompt_text=prompt,
    )
    if not confirmed:
        raise RuntimeError("Insert submit not confirmed")

    # Step 7: Wait + Download + Return
    return await finalize_operation(
        client, job,
        job_type="insert-object",
        project_id=project_id,
        locale=locale,
        download_prefix="ins",
    )


async def _draw_bbox(page, bbox: dict):
    """Draw a bounding box on the video canvas by mouse drag.

    bbox: {x, y, w, h} -- normalized 0-1 coordinates.
    The actual pixel coordinates are computed from the video element's dimensions.
    """
    try:
        # Find the video element to get its bounding rect
        video_rect = await page.evaluate("""() => {
            const video = document.querySelector('video');
            if (!video) return null;
            const rect = video.getBoundingClientRect();
            return {left: rect.left, top: rect.top, width: rect.width, height: rect.height};
        }""")

        if not video_rect:
            logger.warning("No video element found for bbox drawing")
            return

        # Convert normalized coords to pixel coords
        vl = video_rect["left"]
        vt = video_rect["top"]
        vw = video_rect["width"]
        vh = video_rect["height"]

        x = bbox.get("x", 0.3)
        y = bbox.get("y", 0.3)
        w = bbox.get("w", 0.4)
        h = bbox.get("h", 0.4)

        start_x = vl + x * vw
        start_y = vt + y * vh
        end_x = vl + (x + w) * vw
        end_y = vt + (y + h) * vh

        # Mouse drag to draw bbox
        await page.mouse.move(start_x, start_y)
        await page.mouse.down()
        await asyncio.sleep(0.1)
        # Move in small steps for more realistic drag
        steps = 5
        for i in range(1, steps + 1):
            px = start_x + (end_x - start_x) * i / steps
            py = start_y + (end_y - start_y) * i / steps
            await page.mouse.move(px, py)
            await asyncio.sleep(0.05)
        await page.mouse.up()

        logger.info("Drew bbox: x=%.2f y=%.2f w=%.2f h=%.2f", x, y, w, h)
        await asyncio.sleep(0.5)

    except Exception as e:
        logger.warning("Failed to draw bbox: %s", e)


async def _type_insert_prompt(page, prompt: str):
    """Type into the insert prompt field.

    Placeholder: "Describe what you'd like to add" (EN) / "Mo ta noi dung..." (VI)
    """
    SELECTORS = [
        "[role='textbox']",
        "textarea",
        "[contenteditable='true']",
        "[placeholder*='add' i]",
        "[placeholder*='describe' i]",
        "[placeholder*='mo ta' i]",
    ]

    for sel in SELECTORS:
        try:
            el = page.locator(sel).first
            if await el.is_visible(timeout=2000):
                await el.click(timeout=2000)
                await asyncio.sleep(0.3)
                await page.keyboard.press("Control+a")
                await asyncio.sleep(0.1)
                await page.keyboard.type(prompt, delay=20)
                logger.info("Insert prompt typed via: %s", sel)
                return
        except Exception:
            continue

    logger.warning("Could not find insert prompt editor -- proceeding without prompt")
