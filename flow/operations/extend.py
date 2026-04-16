"""Extend Video -- Level 2 operation.

Navigates to edit URL, clicks Extend, types prompt, selects model,
submits, waits, downloads.
"""

import asyncio
import logging

from flow.model_selector import select_model, DEFAULT_MODEL
from flow.submit import submit_with_confirmation
from flow.operations._base import (
    navigate_to_edit,
    wait_for_video_loaded,
    click_action_button,
    count_visible_cards,
    finalize_operation,
)

logger = logging.getLogger(__name__)

# Extend button texts (EN + VI)
EXTEND_BUTTONS = ["Extend", "Mo rong"]

# Extend icon fallback
EXTEND_ICON_SELECTOR = "button:has(span:has-text('keyboard_double_arrow_right'))"


async def extend_video(
    client,
    job: dict,
    prompt: str = "",
    model: str = DEFAULT_MODEL,
    free_mode: bool = True,
) -> dict:
    """Execute extend-video operation.

    Steps:
    1. Navigate to edit URL
    2. Wait for video to load
    3. Click "Extend" button
    4. Type extend prompt (optional -- "What happens next?")
    5. Select LP model
    6. Submit and confirm
    7. Wait + Download + Return metadata

    Args:
        client: FlowClient instance
        job: Job dict with edit_url/project_url/media_id
        prompt: Extension prompt (optional)
        model: Model to use
        free_mode: Use LP (0 credits) model

    Returns: Result dict with project_url, media_id, edit_url, output_files, etc.
    """
    page = client.page

    # Step 1: Navigate
    edit_url_val, project_id, locale = await navigate_to_edit(client, job)

    # Step 2: Wait for video
    await wait_for_video_loaded(page)

    # Step 3: Click Extend button
    clicked = await click_action_button(page, EXTEND_BUTTONS)
    if not clicked:
        # Try icon-based fallback
        try:
            icon_btn = page.locator(EXTEND_ICON_SELECTOR).first
            if await icon_btn.is_visible(timeout=2000):
                await icon_btn.click(timeout=3000)
                clicked = True
                logger.info("Clicked Extend via icon fallback")
                await asyncio.sleep(0.5)
        except Exception:
            pass

    if not clicked:
        raise RuntimeError("Failed to find Extend button")

    # Step 4: Type prompt (optional)
    if prompt:
        await _type_extend_prompt(page, prompt)

    # Step 5: Select model
    await select_model(page, model=model, free_mode=free_mode)

    # Step 6: Submit
    before_cards = await count_visible_cards(page)
    client.clear_captures()

    confirmed = await submit_with_confirmation(
        client,
        before_card_count=before_cards,
        prompt_text=prompt,
    )
    if not confirmed:
        raise RuntimeError("Extend submit not confirmed")

    # Step 7: Wait + Download + Return
    return await finalize_operation(
        client, job,
        job_type="extend-video",
        project_id=project_id,
        locale=locale,
        download_prefix="ext",
    )


async def _type_extend_prompt(page, prompt: str):
    """Type into the extend prompt field.

    Placeholder: "What happens next?" (EN) / "Tiep theo la gi?" (VI)
    """
    SELECTORS = [
        "[role='textbox']",
        "textarea",
        "[contenteditable='true']",
        "[placeholder*='next' i]",
        "[placeholder*='tiep' i]",
        "[aria-label*='prompt' i]",
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
                logger.info("Extend prompt typed via: %s", sel)
                return
        except Exception:
            continue

    logger.warning("Could not find extend prompt editor -- proceeding without prompt")
