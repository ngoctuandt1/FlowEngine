"""Ingredients-to-video - Level 1 operation with multiple image references.

Live-probed 2026-04-20 on the current Flow UI:
- New projects open in Image mode by default (`crop_square` chip).
- Switching the composer menu to `Video` -> `Ingredients` works via exact
  `[role='tab']:text-is(...)` selectors.
- The ingredients `+` button opens a compact menu with `Upload image`, not a
  full asset-picker tab panel. Uploading through the native file chooser adds
  media tiles to the left rail; those tiles are the reliable pre-submit proof
  that the references are attached.
"""

import asyncio
import logging
from pathlib import Path

from flow.download import download_video
from flow.login import handle_login_redirect, is_login_page
from flow.model_selector import DEFAULT_MODEL, select_model
from flow.navigation import extract_media_id, extract_project_id, flow_url
from flow.submit import submit_with_confirmation
from flow.wait import wait_for_completion
from flow.operations.frames_to_video import (
    _click_new_project,
    _close_composer_menu,
)
from flow.operations.generate import (
    _count_visible_cards,
    _dismiss_overlays,
    _set_aspect_ratio,
    _set_output_count,
    _type_prompt,
    _wait_for_composer,
)

logger = logging.getLogger(__name__)

INGREDIENT_PLUS_SELECTOR = "button:not([title*='Add Media']):has(i:text-is('add'))"
COMPOSER_MENU_SELECTORS = [
    "button[aria-haspopup='menu']:has(i:text-is('crop_9_16'))",
    "button[aria-haspopup='menu']:has(i:text-is('crop_16_9'))",
    "button[aria-haspopup='menu']:has(i:text-is('crop_square'))",
    'button[aria-haspopup="menu"]:has-text("crop_9_16")',
    'button[aria-haspopup="menu"]:has-text("crop_16_9")',
    'button[aria-haspopup="menu"]:has-text("crop_square")',
]


async def ingredients_to_video(
    client,
    prompt: str,
    ingredient_image_paths: list[str],
    model: str = DEFAULT_MODEL,
    aspect_ratio: str = "16:9",
    free_mode: bool = True,
) -> dict:
    """Create a new video project from text plus ingredient image references."""
    if not ingredient_image_paths:
        raise RuntimeError("ingredients-to-video requires at least one ingredient image")
    missing = [path for path in ingredient_image_paths if not Path(path).is_file()]
    if missing:
        raise RuntimeError(f"Ingredient image not found: {missing[0]}")

    page = client.page
    locale = ""
    homepage = flow_url(locale)

    await page.goto(homepage, wait_until="domcontentloaded", timeout=30000)
    await asyncio.sleep(2)

    current = page.url
    if is_login_page(current):
        login_ok = await handle_login_redirect(
            page, timeout=60, profile_name=client.profile_name,
        )
        if not login_ok:
            raise RuntimeError("Google login required - profile session expired.")
        await page.goto(homepage, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(2)
        current = page.url

    if "/vi/" in current:
        locale = "vi"

    await asyncio.sleep(3)
    await _dismiss_overlays(page)
    await _click_new_project(page)

    try:
        await page.wait_for_url("**/project/**", timeout=20000)
    except Exception:
        await asyncio.sleep(5)

    await asyncio.sleep(3)
    current = page.url
    if is_login_page(current):
        login_ok = await handle_login_redirect(
            page, timeout=90, profile_name=client.profile_name,
        )
        if not login_ok:
            raise RuntimeError("Google login required - profile session expired.")
        await page.goto(homepage, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(3)
        await _click_new_project(page)
        try:
            await page.wait_for_url("**/project/**", timeout=20000)
        except Exception:
            await asyncio.sleep(5)
        await asyncio.sleep(3)

    project_url_full = page.url
    project_id = extract_project_id(project_url_full)

    await _wait_for_composer(page)
    await _ensure_ingredients_mode(page)
    await _close_composer_menu(page)

    for expected_count, image_path in enumerate(ingredient_image_paths, start=1):
        await _upload_ingredient_with_retry(page, image_path, expected_count)

    await _ensure_uploaded_ingredient_count(page, expected=len(ingredient_image_paths))
    await _type_prompt(page, prompt)
    await select_model(page, model=model, free_mode=free_mode)
    await _set_output_count(page, 1)
    await _set_aspect_ratio(page, aspect_ratio)

    before_cards = await _count_visible_cards(page)
    client.clear_captures()
    confirmed = await submit_with_confirmation(
        client,
        before_card_count=before_cards,
        timeout_sec=15.0,
        prompt_text=prompt,
    )
    if not confirmed:
        raise RuntimeError("Submit not confirmed - generation may not have started")

    result = await wait_for_completion(client, job_type="ingredients-to-video")
    if not result.get("done"):
        raise RuntimeError(f"Generation failed: {result.get('error', 'unknown')}")

    current_url = page.url
    media_id = extract_media_id(current_url)
    if not media_id and result.get("media_ids"):
        media_id = result["media_ids"][0]

    edit_url_val = None
    if media_id and project_id:
        edit_url_val = f"{flow_url(locale)}/project/{project_id}/edit/{media_id}"

    output_files = await download_video(
        client,
        media_ids=result.get("media_ids", [media_id] if media_id else []),
        prefix="ingredients",
    )
    if not output_files:
        raise RuntimeError("ingredients-to-video: no output file captured")

    project_url = f"{flow_url(locale)}/project/{project_id}" if project_id else project_url_full
    return {
        "project_url": project_url,
        "media_id": media_id,
        "edit_url": edit_url_val or current_url,
        "output_files": output_files,
        "generation_id": client._gen_id,
        "profile": client.profile_name,
    }


async def _ensure_ingredients_mode(page) -> None:
    await _open_ingredients_composer_menu(page)
    await _click_exact_tab(page, "Video")
    await _click_exact_tab(page, "Ingredients")


async def _click_exact_tab(page, label: str) -> None:
    tab = page.locator(f"[role='tab']:text-is('{label}')").first
    if not await tab.is_visible(timeout=3000):
        raise RuntimeError(f"Composer tab not found: {label}")
    if await tab.get_attribute("data-state") != "active":
        await tab.click(timeout=3000)
        await asyncio.sleep(0.3)


async def _upload_ingredient(page, image_path: str) -> None:
    plus_button = await _locate_ingredient_plus_button(page)
    await plus_button.click(timeout=3000)
    upload_item = page.locator("[role='menuitem']:text-is('Upload image')").first
    if not await upload_item.is_visible(timeout=3000):
        raise RuntimeError("Ingredient upload action not found after clicking the + button")

    async with page.expect_file_chooser() as chooser_info:
        await upload_item.click(timeout=3000)
    chooser = await chooser_info.value
    await chooser.set_files(image_path)
    await asyncio.sleep(0.5)


async def _upload_ingredient_with_retry(page, image_path: str, expected_count: int) -> None:
    before_count = await _count_uploaded_ingredients(page)
    await _upload_ingredient(page, image_path)
    after_count = await _wait_for_uploaded_ingredient_count(page, expected_count)
    if after_count >= expected_count and after_count > before_count:
        return

    logger.warning(
        "Ingredient chip did not appear after upload; retrying once for %s (expected=%d, before=%d, after=%d)",
        image_path,
        expected_count,
        before_count,
        after_count,
    )
    await _upload_ingredient(page, image_path)
    final_count = await _wait_for_uploaded_ingredient_count(page, expected_count)
    if final_count < expected_count:
        raise RuntimeError(
            f"Ingredient attach mismatch after retry: expected {expected_count}, found {final_count}"
        )


async def _locate_ingredient_plus_button(page):
    button = page.locator(INGREDIENT_PLUS_SELECTOR).first
    if await button.is_visible(timeout=3000):
        return button
    raise RuntimeError(f"Could not locate ingredient add button via selector: {INGREDIENT_PLUS_SELECTOR}")


async def _open_ingredients_composer_menu(page) -> None:
    for sel in COMPOSER_MENU_SELECTORS:
        try:
            chip = page.locator(sel).first
            if await chip.is_visible(timeout=2000):
                await chip.click(timeout=3000)
                await asyncio.sleep(0.5)
                return
        except Exception:
            continue
    try:
        fallback = page.locator("button[aria-haspopup='menu']").filter(has_text="x1").last
        if await fallback.is_visible(timeout=2000):
            await fallback.click(timeout=3000)
            await asyncio.sleep(0.5)
            return
    except Exception:
        pass
    raise RuntimeError("Could not open composer menu for ingredients-to-video")


async def _ensure_uploaded_ingredient_count(page, expected: int) -> None:
    visible_count = await _count_uploaded_ingredients(page)
    if visible_count >= expected:
        return
    raise RuntimeError(
        f"Ingredient attach mismatch: expected {expected}, found {visible_count}; refusing to submit"
    )


async def _wait_for_uploaded_ingredient_count(page, expected: int, timeout_sec: float = 60.0) -> int:
    deadline = asyncio.get_running_loop().time() + timeout_sec
    latest_count = 0
    while asyncio.get_running_loop().time() < deadline:
        latest_count = await _count_uploaded_ingredients(page)
        if latest_count >= expected:
            return latest_count
        await asyncio.sleep(5)
    return latest_count


async def _count_uploaded_ingredients(page) -> int:
    return await page.evaluate(
        """() => {
            return Array.from(document.querySelectorAll("img[alt='Generated image']")).filter((img) => {
                const rect = img.getBoundingClientRect();
                return rect.width >= 100 && rect.height >= 100;
            }).length;
        }"""
    )
