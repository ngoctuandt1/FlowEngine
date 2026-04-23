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
    draw_bbox_on_video,
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
    """Execute full insert-object operation (submit + download).

    Back-compat wrapper around ``submit_insert_object`` + ``download_insert_object``.
    """
    ctx = await submit_insert_object(client, job, prompt=prompt, bbox=bbox)
    return await download_insert_object(client, job, ctx)


async def submit_insert_object(
    client,
    job: dict,
    prompt: str = "",
    bbox: dict | None = None,
) -> dict:
    """Navigate → open Insert → draw bbox → submit. Does NOT wait for completion.

    Steps:
    1. Navigate to edit URL
    2. Wait for video to load
    3. Click "Insert" button
    4. Draw bbox on video canvas (if provided)
    5. Type description prompt (optional)
    6. Submit and confirm

    Returns ``{project_id, locale}`` for ``download_insert_object``.
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
        drew = await draw_bbox_on_video(page, bbox)
        if not drew:
            logger.warning(
                "Bbox drawing failed or unverified — Flow may fall back to default region"
            )

    # Step 5: Type prompt (optional)
    if prompt:
        await _type_insert_prompt(page, prompt)

    # Step 6: Submit
    before_cards = await count_visible_cards(page)
    # Snapshot media-event cursor instead of clearing — see issue #38.
    capture_start = client.capture_cursor()

    confirmed = await submit_with_confirmation(
        client,
        before_card_count=before_cards,
        prompt_text=prompt,
    )
    if not confirmed:
        raise RuntimeError("Insert submit not confirmed")

    return {
        "project_id": project_id,
        "locale": locale,
        "capture_start": capture_start,
    }


async def download_insert_object(client, job: dict, submit_ctx: dict) -> dict:
    """Wait for the just-submitted insert-object generation and download the output."""
    return await finalize_operation(
        client, job,
        job_type="insert-object",
        project_id=submit_ctx["project_id"],
        locale=submit_ctx["locale"],
        download_prefix="ins",
        capture_start=submit_ctx.get("capture_start"),
    )


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
