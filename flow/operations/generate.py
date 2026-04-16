"""Text-to-Video generation — Level 1 operation."""

import asyncio
import logging

from flow.navigation import flow_url, extract_project_id, extract_media_id
from flow.model_selector import select_model, DEFAULT_MODEL
from flow.submit import submit_with_confirmation
from flow.wait import wait_for_completion
from flow.download import download_video

logger = logging.getLogger(__name__)


async def text_to_video(
    client,
    prompt: str,
    model: str = DEFAULT_MODEL,
    aspect_ratio: str = "16:9",
    free_mode: bool = True,
) -> dict:
    """Execute text-to-video generation.

    Steps:
    1. Navigate to Flow homepage
    2. Click "+ New project" to create fresh project
    3. Select model (LP for free)
    4. Set aspect ratio (if UI supports it)
    5. Type prompt in composer
    6. Submit and confirm
    7. Wait for generation to complete
    8. Download result video
    9. Extract and return all metadata

    Returns:
        {
            "project_url": str,
            "media_id": str | None,
            "edit_url": str | None,
            "output_files": list[str],
            "generation_id": str | None,
            "profile": str,
        }
    """
    page = client.page
    locale = ""  # Will detect from URL

    # === Step 1: Navigate to Flow homepage ===
    logger.info("Step 1: Navigate to Flow homepage")
    homepage = flow_url(locale)
    await page.goto(homepage, wait_until="domcontentloaded", timeout=30000)
    await asyncio.sleep(2)  # Let page settle

    # Detect locale from redirected URL
    current = page.url
    if "/vi/" in current:
        locale = "vi"
    logger.info(f"On Flow homepage: {current}, locale={locale or 'en'}")

    # === Step 2: Click "+ New project" ===
    logger.info("Step 2: Create new project")
    new_project_clicked = False

    # Try multiple selectors for the new project button
    NEW_PROJECT_SELECTORS = [
        "button:has-text('New project')",
        "button:has-text('Du\u0323 a\u0301n mo\u0301i')",
        "a:has-text('New project')",
        "a:has-text('Du\u0323 a\u0301n mo\u0301i')",
        "[role='button']:has-text('New project')",
        "[role='button']:has-text('Du\u0323 a\u0301n mo\u0301i')",
    ]

    for sel in NEW_PROJECT_SELECTORS:
        try:
            btn = page.locator(sel).first
            if await btn.is_visible(timeout=3000):
                await btn.click(timeout=5000)
                new_project_clicked = True
                logger.info(f"Clicked new project via: {sel}")
                break
        except Exception:
            continue

    if not new_project_clicked:
        raise RuntimeError("Failed to find '+ New project' button on Flow homepage")

    # Wait for project editor to load
    await page.wait_for_url("**/project/**", timeout=15000)
    await asyncio.sleep(2)

    project_url_full = page.url
    project_id = extract_project_id(project_url_full)
    logger.info(f"New project created: {project_url_full}")

    # === Step 3: Select model ===
    logger.info(f"Step 3: Select model ({model})")
    await select_model(page, model=model, free_mode=free_mode)

    # === Step 4: Aspect ratio ===
    # The aspect ratio is typically set in the model options panel
    # For now, we set it during model selection or skip if not critical
    logger.info(f"Step 4: Aspect ratio = {aspect_ratio}")
    await _set_aspect_ratio(page, aspect_ratio)

    # === Step 5: Type prompt ===
    logger.info(f"Step 5: Type prompt ({len(prompt)} chars)")
    await _type_prompt(page, prompt)

    # === Step 6: Count baseline cards, clear captures, submit ===
    logger.info("Step 6: Submit generation")
    before_cards = await _count_visible_cards(page)
    client.clear_captures()

    confirmed = await submit_with_confirmation(
        client,
        before_card_count=before_cards,
        timeout_sec=15.0,
        prompt_text=prompt,
    )

    if not confirmed:
        raise RuntimeError("Submit not confirmed — generation may not have started")

    logger.info("Submit confirmed, waiting for generation...")

    # === Step 7: Wait for completion ===
    logger.info("Step 7: Wait for completion")
    result = await wait_for_completion(client, job_type="text-to-video")

    if not result.get("done"):
        error = result.get("error", "unknown")
        raise RuntimeError(f"Generation failed: {error}")

    logger.info("Generation complete!")

    # === Step 8: Extract metadata ===
    current_url = page.url
    media_id = extract_media_id(current_url)
    if not media_id and result.get("media_ids"):
        media_id = result["media_ids"][0]

    # Build edit_url
    edit_url_val = None
    if media_id and project_id:
        base = flow_url(locale)
        edit_url_val = f"{base}/project/{project_id}/edit/{media_id}"

    # === Step 9: Download ===
    logger.info("Step 8: Download video")
    output_files = await download_video(
        client,
        media_ids=result.get("media_ids", [media_id] if media_id else []),
        prefix="t2v",
    )

    # Build project_url (without /edit/ part)
    proj_url = None
    if project_id:
        proj_url = f"{flow_url(locale)}/project/{project_id}"

    return {
        "project_url": proj_url or project_url_full,
        "media_id": media_id,
        "edit_url": edit_url_val or current_url,
        "output_files": output_files,
        "generation_id": client._gen_id,
        "profile": client.profile_name,
    }


async def _type_prompt(page, prompt: str):
    """Type prompt into the Flow composer textarea.

    The composer can be:
    - A contenteditable div
    - A textarea
    - A [role='textbox'] element

    Placeholder text: "What do you want to create?" (EN) or "Ban muon tao gi?" (VI)
    """
    PROMPT_SELECTORS = [
        "[role='textbox']",
        "textarea",
        "[contenteditable='true']",
        "[data-testid*='prompt']",
        "[data-testid*='composer']",
        "[aria-label*='create' i]",
        "[aria-label*='prompt' i]",
        "[placeholder*='create' i]",
        "[placeholder*='want' i]",
        "[placeholder*='mu\u1ed1n' i]",
    ]

    for sel in PROMPT_SELECTORS:
        try:
            el = page.locator(sel).first
            if await el.is_visible(timeout=2000):
                await el.click(timeout=2000)
                await asyncio.sleep(0.3)
                # Clear existing text
                await page.keyboard.press("Control+a")
                await asyncio.sleep(0.1)
                # Type the prompt
                await page.keyboard.type(prompt, delay=20)
                logger.info(f"Prompt typed via: {sel}")
                return
        except Exception:
            continue

    raise RuntimeError("Failed to find prompt editor")


async def _set_aspect_ratio(page, ratio: str):
    """Set aspect ratio in the model options panel.

    Common ratios: "16:9", "9:16", "1:1"
    """
    if not ratio or ratio == "16:9":
        return  # 16:9 is often default

    try:
        # Look for ratio button/selector
        ratio_btn = page.locator(
            f"button:has-text('{ratio}'), [role='button']:has-text('{ratio}')"
        ).first
        if await ratio_btn.is_visible(timeout=2000):
            await ratio_btn.click(timeout=2000)
            logger.info(f"Aspect ratio set to {ratio}")
            await asyncio.sleep(0.5)
    except Exception:
        logger.warning(f"Could not set aspect ratio {ratio} — using default")


async def _count_visible_cards(page) -> int:
    """Count visible media cards."""
    try:
        return await page.evaluate("""() => {
            const videos = document.querySelectorAll('video');
            const tiles = document.querySelectorAll('[data-tile-id]');
            return Math.max(videos.length, tiles.length);
        }""")
    except Exception:
        return 0
