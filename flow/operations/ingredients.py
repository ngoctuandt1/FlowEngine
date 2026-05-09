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
import os

from flow.download import download_video
from flow.login import handle_login_redirect, is_login_page
from flow.model_selector import DEFAULT_MODEL, select_model
from flow.navigation import extract_project_id, flow_url
from flow.submit import submit_with_confirmation
from flow.wait import wait_for_completion
from flow.operations._base import resolve_final_media_id
from flow.operations.frames_to_video import (
    COMPOSER_MENU_SELECTORS,
    _click_new_project,
    _close_composer_menu,
    _extract_replay_media_ids,
    _finalize_l1_replay_result,
    _project_id_from_template_or_page,
    _resolve_image_input_path,
)
from flow.operations.generate import (
    _count_visible_cards,
    _dismiss_overlays,
    _set_aspect_ratio,
    _set_output_count,
    _type_prompt,
    _wait_for_composer,
)

try:  # Wave-1 reverse API helper; guarded so default UI path is independent.
    from flow.operations.ingredients_api import (
        get_i2v_request_template,
        install_i2v_request_capture,
        replay_i2v_via_inflate,
    )
except Exception as exc:  # pragma: no cover - guarded fallback
    get_i2v_request_template = None
    install_i2v_request_capture = None
    replay_i2v_via_inflate = None
    _I2V_API_IMPORT_ERROR = exc
else:
    _I2V_API_IMPORT_ERROR = None

logger = logging.getLogger(__name__)

INGREDIENT_PLUS_SELECTOR = "button:not([title*='Add Media']):has(i:text-is('add'))"


def _reverse_i2v_enabled() -> bool:
    return os.getenv("FLOW_I2V_VIA_REVERSE", "0") == "1"


def _install_i2v_capture_if_enabled(client) -> bool:
    if not _reverse_i2v_enabled():
        return False
    if install_i2v_request_capture is None:
        logger.info(
            "FLOW_I2V_VIA_REVERSE=1 but ingredients_api unavailable; continuing UI path (%s)",
            _I2V_API_IMPORT_ERROR,
        )
        return False
    try:
        install_i2v_request_capture(client)
    except Exception as exc:
        logger.info("I2V request capture install failed; continuing UI path: %s", exc)
        return False
    return True


def _current_i2v_template(client):
    if get_i2v_request_template is None:
        return None
    try:
        return get_i2v_request_template(client)
    except Exception as exc:
        logger.info("I2V request template unavailable; continuing UI path: %s", exc)
        return None


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
    ingredient_image_paths = [
        _resolve_image_input_path(path, label=f"Ingredient #{idx}")
        for idx, path in enumerate(ingredient_image_paths, start=1)
    ]

    page = client.page
    capture_ready = _install_i2v_capture_if_enabled(client)
    locale = ""
    homepage = flow_url(locale)

    if capture_ready and replay_i2v_via_inflate is not None:
        template = _current_i2v_template(client)
        if template is not None:
            try:
                if "/vi/" in str(getattr(page, "url", "")):
                    locale = "vi"
                replay_project_id = _project_id_from_template_or_page(template, getattr(page, "url", ""))
                client.clear_captures()
                replay_result = await replay_i2v_via_inflate(
                    client,
                    prompt,
                    ingredient_image_paths,
                )
                replay_media_ids = _extract_replay_media_ids(replay_result)
                if not replay_media_ids:
                    raise RuntimeError("I2V reverse-API replay returned no media_id")
                replay_media_id = replay_media_ids[0]
                replay_count = getattr(client, "_i2v_replay_count", 0) + 1
                setattr(client, "_i2v_replay_count", replay_count)
                logger.info(
                    "I2V replay submit accepted via reverse API "
                    "(count=%d media_ids=%s) -- finalizing via status API + direct URL download",
                    replay_count,
                    replay_media_ids,
                )
                return await _finalize_l1_replay_result(
                    client,
                    project_id=replay_project_id,
                    locale=locale,
                    replay_media_id=replay_media_id,
                    operation_label="ingredients-to-video",
                    replay_source="i2v_replay",
                    failure_prefix="i2v_replay",
                    download_prefix="ingredients",
                )
            except RuntimeError as exc:
                logger.warning(
                    "I2V reverse-API replay failed; falling back to UI path: %s",
                    exc,
                )

    await page.goto(homepage, wait_until="domcontentloaded", timeout=30000)
    try:
        await page.wait_for_selector(
            "text=/New project|Dự án mới|Tạo dự án/",
            state="attached",
            timeout=4000,
        )
    except Exception:
        pass  # marketing landing or slow load — recovery logic below handles it

    current = page.url
    if is_login_page(current):
        login_ok = await handle_login_redirect(
            page, timeout=60, profile_name=client.profile_name, client=client,
        )
        if not login_ok:
            raise RuntimeError("Google login required - profile session expired.")
        await page.goto(homepage, wait_until="domcontentloaded", timeout=30000)
        try:
            await page.wait_for_selector(
                "text=/New project|Dự án mới|Tạo dự án/",
                state="attached",
                timeout=4000,
            )
        except Exception:
            pass  # marketing landing or slow load — recovery logic below handles it
        current = page.url

    if "/vi/" in current:
        locale = "vi"

    await _dismiss_overlays(page)
    await _click_new_project(page)

    try:
        await page.wait_for_url("**/project/**", timeout=20000)
    except Exception:
        await asyncio.sleep(1)

    current = page.url
    if is_login_page(current):
        login_ok = await handle_login_redirect(
            page, timeout=90, profile_name=client.profile_name, client=client,
        )
        if not login_ok:
            raise RuntimeError("Google login required - profile session expired.")
        await page.goto(homepage, wait_until="domcontentloaded", timeout=30000)
        try:
            await page.wait_for_selector(
                "text=/New project|Dự án mới|Tạo dự án/",
                state="attached",
                timeout=4000,
            )
        except Exception:
            pass  # marketing landing or slow load — recovery logic below handles it
        await _click_new_project(page)
        try:
            await page.wait_for_url("**/project/**", timeout=20000)
        except Exception:
            await asyncio.sleep(1)

    project_url_full = page.url
    project_id = extract_project_id(project_url_full)

    await _wait_for_composer(page)
    await _ensure_ingredients_mode(page)
    await _close_composer_menu(page)

    for expected_count, image_path in enumerate(ingredient_image_paths, start=1):
        await _upload_ingredient_with_retry(page, image_path, expected_count)

    await _ensure_uploaded_ingredient_count(page, expected=len(ingredient_image_paths))
    await _type_prompt(page, prompt)
    await select_model(page, model=model, free_mode=free_mode, profile=client.profile_name)
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
    captured_media_ids = result.get("media_ids") or []
    fallback_media_id = captured_media_ids[0] if captured_media_ids else None
    media_id = await resolve_final_media_id(page, fallback=fallback_media_id)

    edit_url_val = None
    if media_id and project_id:
        edit_url_val = f"{flow_url(locale)}/project/{project_id}/edit/{media_id}"

    project_url = f"{flow_url(locale)}/project/{project_id}" if project_id else project_url_full
    output_files = await download_video(
        client,
        media_ids=captured_media_ids or ([media_id] if media_id else []),
        prefix="ingredients",
        metadata={
            "job_type": "ingredients-to-video",
            "prompt": prompt,
            "media_id": media_id or "",
            "project_url": project_url,
            "profile": client.profile_name or "",
        },
    )
    if not output_files:
        raise RuntimeError("ingredients-to-video: no output file captured")

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
    await asyncio.sleep(0.15)


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
                await asyncio.sleep(0.15)
                return
        except Exception:
            continue
    try:
        fallback = page.locator("button[aria-haspopup='menu']").filter(has_text="x1").last
        if await fallback.is_visible(timeout=2000):
            await fallback.click(timeout=3000)
            await asyncio.sleep(0.15)
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
    visible_count = await _count_uploaded_ingredients(page)
    if visible_count >= expected:
        return visible_count

    try:
        await page.wait_for_function(
            f"() => document.querySelectorAll('[data-testid*=\"ingredient\"], .ingredient-item, [aria-label*=\"ingredient\"]').length >= {expected}",
            timeout=min(int(timeout_sec * 1000), 10000),
        )
    except Exception:
        await asyncio.sleep(2)  # fallback
    return await _count_uploaded_ingredients(page)


async def _count_uploaded_ingredients(page) -> int:
    return await page.evaluate(
        """() => {
            return Array.from(document.querySelectorAll("img[alt='Generated image']")).filter((img) => {
                const rect = img.getBoundingClientRect();
                return rect.width >= 100 && rect.height >= 100;
            }).length;
        }"""
    )
