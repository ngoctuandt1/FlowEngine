"""Text-to-image - Level 1 operation.

Deferred: Video Ingredients refs and Voice refs stay out of scope here.

Authoritative selectors from the 2026-04-20 probe/docs:
- Output tab: `[role='tab']:has(i:text-is('image'))`
- Model dropdown chip: `button[aria-haspopup='menu']:has(i:text-is('arrow_drop_down'))`
  filtered by text containing `Nano Banana` / `Imagen` (the live text can start with the banana emoji).
- Image aspect ids: `LANDSCAPE`, `LANDSCAPE_4_3`, `SQUARE`, `PORTRAIT_3_4`, `PORTRAIT`
"""

import asyncio
import logging
import os
import re
from pathlib import Path

from playwright.async_api import Locator

from flow.download import download_video
from flow.login import handle_login_redirect, is_login_page
from flow.model_selector import _close_model_panel
from flow.navigation import extract_project_id, flow_url
from flow.submit import submit_with_confirmation
from flow.wait import wait_for_completion
from flow.operations._base import resolve_final_media_id
from flow.operations._l1_status_poll import download_via_url
from flow.operations.frames_to_video import (
    COMPOSER_MENU_SELECTORS,
    _click_new_project,
    _close_composer_menu,
    _open_composer_menu,
    _resolve_image_input_path,
)
from flow.operations.generate import (
    _count_visible_cards,
    _dismiss_overlays,
    _type_prompt,
    _wait_for_composer,
)

logger = logging.getLogger(__name__)

try:  # guarded so production falls back cleanly if sibling revAPI module is absent.
    from flow.operations.image_api import (
        get_image_request_template,
        install_image_request_capture,
        replay_image_generate,
    )
except Exception as exc:  # pragma: no cover - exercised by monkeypatched fallback
    get_image_request_template = None
    install_image_request_capture = None
    replay_image_generate = None
    _IMAGE_API_IMPORT_ERROR = exc
else:
    _IMAGE_API_IMPORT_ERROR = None

IMAGE_MODEL_MAP = {
    "nano-banana-pro": "Nano Banana Pro",
    "nano-banana-2": "Nano Banana 2",
    "imagen-4": "Imagen 4",
}

IMAGE_MODEL_ALIASES = {
    "nanobanana": "nano-banana-pro",
    "nanobananapro": "nano-banana-pro",
    "nanobanana2": "nano-banana-2",
    "imagen4": "imagen-4",
}

IMAGE_RATIO_IDS = {
    "16:9": "LANDSCAPE",
    "4:3": "LANDSCAPE_4_3",
    "1:1": "SQUARE",
    "3:4": "PORTRAIT_3_4",
    "9:16": "PORTRAIT",
}


def _reverse_t2i_enabled() -> bool:
    return os.getenv("FLOW_T2I_VIA_REVERSE", "0") == "1"


def _install_image_capture_if_enabled(client) -> bool:
    if not _reverse_t2i_enabled():
        return False
    if install_image_request_capture is None:
        logger.info(
            "FLOW_T2I_VIA_REVERSE=1 but image_api unavailable; "
            "continuing UI path (%s)",
            _IMAGE_API_IMPORT_ERROR,
        )
        return False
    try:
        install_image_request_capture(client)
    except Exception as exc:
        logger.info(
            "Image request capture install failed; continuing UI path: %s",
            exc,
        )
        return False
    return True


def _current_image_template(client):
    if get_image_request_template is None:
        return None
    try:
        return get_image_request_template(client)
    except Exception as exc:
        logger.info(
            "Image request template unavailable; continuing UI path: %s",
            exc,
        )
        return None


def _extract_image_replay_media_ids(replay_result) -> list[str]:
    if isinstance(replay_result, str) and replay_result:
        return [replay_result]
    if isinstance(replay_result, list):
        return [str(item) for item in replay_result if item]
    if not isinstance(replay_result, dict):
        return []
    media_ids = replay_result.get("media_ids") or replay_result.get("media_names") or []
    media_id = replay_result.get("media_id") or replay_result.get("media_name")
    if media_id:
        media_ids = [media_id, *media_ids]
    unique_media_ids = []
    for media_id_value in media_ids:
        if media_id_value and media_id_value not in unique_media_ids:
            unique_media_ids.append(str(media_id_value))
    return unique_media_ids


def _record_image_replay_media_id(client, media_id: str) -> None:
    image_names = getattr(client, "_image_names", None)
    if isinstance(image_names, list) and media_id not in image_names:
        image_names.append(media_id)
    recorder = getattr(client, "_record_media_id", None)
    if callable(recorder):
        try:
            recorder(media_id, source="t2i_replay", url="t2i-replay")
        except Exception:
            pass


def _replay_download_dir(client) -> Path:
    client_dir = getattr(client, "download_dir", None)
    if isinstance(client_dir, (str, os.PathLike)):
        try:
            return Path(client_dir)
        except TypeError:
            pass
    env_dir = os.environ.get("FLOW_DOWNLOAD_DIR")
    if env_dir:
        return Path(env_dir)
    return Path("downloads")


def _safe_filename_token(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "")).strip("_") or "media"


async def _finalize_image_replay_result(
    client,
    *,
    media_id: str,
    project_id: str | None,
    locale: str,
    project_url_full: str,
) -> dict:
    _record_image_replay_media_id(client, media_id)
    media_url = (
        "https://labs.google/fx/api/trpc/"
        f"media.getMediaUrlRedirect?name={media_id}"
    )
    download_dir = _replay_download_dir(client)
    try:
        download_dir.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        logger.debug(
            "Image replay finalize: download dir mkdir failed (%s) for %s; "
            "continuing -- download_via_url will surface the real error",
            exc,
            download_dir,
        )
    out_path = download_dir / f"t2i_replay_{_safe_filename_token(media_id)}.png"
    saved_path = await download_via_url(
        client,
        url=media_url,
        out_path=str(out_path),
    )
    if not saved_path:
        raise RuntimeError(
            f"text-to-image replay direct download returned empty path for media_id={media_id}"
        )

    project_url = f"{flow_url(locale)}/project/{project_id}" if project_id else project_url_full
    edit_url_val = (
        f"{flow_url(locale)}/project/{project_id}/edit/{media_id}"
        if project_id
        else getattr(client.page, "url", project_url_full)
    )
    return {
        "project_url": project_url,
        "media_id": media_id,
        "edit_url": edit_url_val,
        "output_files": [saved_path],
        "generation_id": getattr(client, "_gen_id", None),
        "profile": client.profile_name,
    }


async def _try_image_replay_from_template(
    client,
    *,
    prompt: str,
    project_id: str | None,
    locale: str,
    project_url_full: str,
) -> dict | None:
    if not _reverse_t2i_enabled() or replay_image_generate is None:
        return None
    template = _current_image_template(client)
    if template is None:
        return None
    try:
        client.clear_captures()
        replay_result = await replay_image_generate(client, prompt, count=1)
        replay_media_ids = _extract_image_replay_media_ids(replay_result)
        if not replay_media_ids:
            raise RuntimeError("Image reverse replay returned no media_id")
        logger.info(
            "Image reverse replay accepted (media_ids=%s) -- downloading via direct URL",
            replay_media_ids,
        )
        return await _finalize_image_replay_result(
            client,
            media_id=replay_media_ids[0],
            project_id=project_id,
            locale=locale,
            project_url_full=project_url_full,
        )
    except RuntimeError as exc:
        logger.warning(
            "Image reverse-API replay failed; falling back to UI path: %s",
            exc,
        )
        return None


async def text_to_image(
    client,
    prompt: str,
    ref_image_path: str | None = None,
    model: str = "nano-banana-pro",
    aspect_ratio: str = "16:9",
) -> dict:
    """Create a new image project from text with an optional reference image."""
    if ref_image_path:
        ref_image_path = _resolve_image_input_path(ref_image_path, label="Reference")

    page = client.page
    capture_ready = _install_image_capture_if_enabled(client)
    locale = ""
    homepage = flow_url(locale)

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
    await _switch_to_image_output(page)
    await _close_composer_menu(page)

    if ref_image_path:
        await _upload_reference_image(page, ref_image_path)

    await _type_prompt(page, prompt)
    await _select_image_model(page, model)
    await _set_image_aspect_ratio(page, aspect_ratio)
    await _set_image_output_count(page, 1)

    if capture_ready:
        replay_result = await _try_image_replay_from_template(
            client,
            prompt=prompt,
            project_id=project_id,
            locale=locale,
            project_url_full=project_url_full,
        )
        if replay_result is not None:
            return replay_result

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

    result = await wait_for_completion(client, job_type="text-to-image")
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
        prefix="t2i",
        quality="original",
        media_kind="image",
        metadata={
            "job_type": "text-to-image",
            "prompt": prompt,
            "media_id": media_id or "",
            "project_url": project_url,
            "profile": client.profile_name or "",
        },
    )
    if not output_files:
        raise RuntimeError("text-to-image: no output file captured")

    return {
        "project_url": project_url,
        "media_id": media_id,
        "edit_url": edit_url_val or current_url,
        "output_files": output_files,
        "generation_id": client._gen_id,
        "profile": client.profile_name,
    }


async def _switch_to_image_output(page) -> None:
    """Ensure the composer is in Image mode.

    Flow persists the last-used composer mode per account. On a fresh browser
    session the Image tab may already be rendered inline, but after a prior
    video job the new-project composer can start in Video mode and only render
    the Image tab inside the settings-chip dropdown. We therefore take the fast
    inline-tab path first, then mirror the video-mode dropdown pattern.
    """
    inline_tab = await _find_inline_image_tab(page)
    if inline_tab is not None:
        state = await inline_tab.get_attribute("data-state")
        if state != "active":
            await inline_tab.click(timeout=3000)
            await asyncio.sleep(0.3)
        logger.info("_switch_to_image_output: inline Image tab path (state=%s)", state)
        return

    logger.info("_switch_to_image_output: opening composer chip to reveal Image tab")
    await _open_composer_menu(page)
    await _ensure_image_mode(page)


async def _find_inline_image_tab(page) -> Locator | None:
    """Return the inline Image tab when it is directly rendered in the composer."""
    selectors = (
        "[role='tab']:text-is('Image')",
        "[role='tab']:has(i:text-is('image'))",
    )
    for sel in selectors:
        tab = page.locator(sel).first
        try:
            if await tab.is_visible(timeout=500):
                return tab
        except Exception as exc:
            logger.debug("_find_inline_image_tab: selector %s not ready: %s", sel, exc)
    return None


async def _ensure_image_mode(page) -> None:
    """Switch the open composer menu to the Image tab when persisted mode differs.

    The chip dropdown contains a Radix ``role=menu`` with four
    ``button[role='tab']`` entries: Image, Video, Frames, Ingredients. We
    match the Image tab by visible label or exact icon ligature because other
    groups in the same menu also use ``data-state='active'``.
    """
    menu_tabs = page.locator("[role='menu'][data-state='open'] button[role='tab']")
    await page.locator("[role='menu'][data-state='open']").wait_for(
        state="visible", timeout=3000,
    )

    count = await menu_tabs.count()
    if count == 0:
        raise RuntimeError("Composer menu opened but no mode tabs were rendered")

    image_tab = None
    observed_tabs: list[str] = []
    for index in range(count):
        tab = menu_tabs.nth(index)
        text = (await tab.inner_text()).strip()
        state = await tab.get_attribute("data-state")
        observed_tabs.append(f"{text!r}={state}")
        if re.search(r"(^|\s)image(\s|$)", text, re.IGNORECASE):
            image_tab = tab
            break
        icon = tab.locator("i:text-is('image')").first
        if await icon.count() > 0:
            image_tab = tab
            break

    if image_tab is None:
        raise RuntimeError(f"Composer Image tab not found inside menu: {observed_tabs}")

    state = await image_tab.get_attribute("data-state")
    if state != "active":
        await image_tab.click(timeout=3000)
        await asyncio.sleep(0.6)
        logger.info("_ensure_image_mode: switched Image tab via composer chip (tabs=%s)", observed_tabs)
        return

    logger.info("_ensure_image_mode: Image already active in composer chip (tabs=%s)", observed_tabs)


async def _select_image_model(page, model: str) -> None:
    normalized_model = _normalize_image_model(model)
    target_text = IMAGE_MODEL_MAP[normalized_model]
    await _open_composer_menu(page)
    await _switch_to_image_output(page)

    item = await _find_model_option(page, target_text)
    if item is None:
        await _open_image_model_dropdown(page)
        item = await _find_model_option(page, target_text)
    if item is None:
        raise RuntimeError(f"Image model option not found: {target_text}")

    await item.click(timeout=3000, force=True)
    await asyncio.sleep(0.5)
    await _close_model_panel(page, dropdown_was_opened=True)


def _normalize_image_model(model: str | None) -> str:
    """Canonicalize image model input to stable internal slugs."""
    raw = str(model or "").strip()
    if not raw:
        return "nano-banana-pro"

    lowered = raw.lower().replace("_", "-")
    if lowered in IMAGE_MODEL_MAP:
        return lowered

    compact = re.sub(r"[^a-z0-9]+", "", lowered)
    alias = IMAGE_MODEL_ALIASES.get(compact)
    if alias:
        return alias

    for slug, label in IMAGE_MODEL_MAP.items():
        if compact == re.sub(r"[^a-z0-9]+", "", label.lower()):
            return slug

    logger.warning("Unknown image model %r - falling back to nano-banana-pro", raw)
    return "nano-banana-pro"


async def _find_model_option(page, target_text: str):
    selector = (
        "menuitem, [role='menuitem'], [role='option'], "
        "button, [role='button'], [role='listbox'] button"
    )
    items = page.locator(selector).filter(
        has_text=re.compile(re.escape(target_text), re.IGNORECASE)
    )
    if await items.count() == 0:
        return None
    return items.first


async def _open_image_model_dropdown(page) -> bool:
    candidates = page.locator(
        "button[aria-haspopup='menu']:has(i:text-is('arrow_drop_down'))"
    ).filter(has_text=re.compile(r"(Nano Banana|Imagen)", re.IGNORECASE))
    if await candidates.count() == 0:
        candidates = page.locator(
            "button[aria-haspopup='menu']:has(i:text-is('arrow_drop_down'))"
        )
    if await candidates.count() == 0:
        return False
    await candidates.first.click(timeout=3000)
    await asyncio.sleep(0.5)
    return True


async def _set_image_aspect_ratio(page, ratio: str) -> None:
    suffix = IMAGE_RATIO_IDS.get(ratio)
    if suffix is None:
        raise RuntimeError(f"Unsupported image aspect ratio: {ratio}")

    chip_btn = await _locate_image_chip(page)
    current_state = await chip_btn.get_attribute("data-state", timeout=2000)
    if current_state != "open":
        await chip_btn.click(timeout=3000)

    await page.locator('[role="menu"][data-state="open"]').wait_for(
        state="visible", timeout=3000,
    )

    trigger = page.locator(f'[id$="-trigger-{suffix}"]').first
    await trigger.click(timeout=3000)
    await page.wait_for_function(
        f'() => document.querySelector(\'[id$="-trigger-{suffix}"]\')?.dataset.state === "active"',
        timeout=3000,
    )
    await page.mouse.click(10, 10)
    await page.locator('[role="menu"][data-state="open"]').wait_for(
        state="hidden", timeout=2000,
    )


async def _set_image_output_count(page, count: int) -> None:
    if count < 1 or count > 4:
        raise ValueError(f"count must be 1..4, got {count}")

    chip_btn = await _locate_image_chip(page)
    current_state = await chip_btn.get_attribute("data-state", timeout=2000)
    if current_state != "open":
        await chip_btn.click(timeout=3000)

    await page.locator('[role="menu"][data-state="open"]').wait_for(
        state="visible", timeout=3000,
    )

    trigger = page.locator(f'[id$="-trigger-{count}"]').first
    await trigger.click(timeout=3000)
    await page.wait_for_function(
        f'() => document.querySelector(\'[id$="-trigger-{count}"]\')?.dataset.state === "active"',
        timeout=3000,
    )
    await page.mouse.click(10, 10)
    await page.locator('[role="menu"][data-state="open"]').wait_for(
        state="hidden", timeout=2000,
    )


async def _locate_image_chip(page):
    for sel in COMPOSER_MENU_SELECTORS:
        chip = page.locator(sel).first
        try:
            if await chip.is_visible(timeout=1000):
                return chip
        except Exception:
            continue
    raise RuntimeError("Could not locate image composer chip")


async def _upload_reference_image(page, image_path: str) -> None:
    if not await _mark_reference_input(page):
        raise RuntimeError("Could not locate reference image file input")
    input_el = page.locator("input[type='file'][data-ref-upload='true']").first
    await input_el.set_input_files(image_path, timeout=10000)
    await asyncio.sleep(1)


async def _mark_reference_input(page) -> bool:
    return await page.evaluate(
        """() => {
            for (const input of document.querySelectorAll('input[type="file"]')) {
                input.removeAttribute('data-ref-upload');
            }

            const plusButtons = Array.from(document.querySelectorAll('button, [role="button"]')).filter((el) => {
                const text = (el.textContent || '').trim();
                return text === '+' || text === 'add' || text === 'add_photo_alternate';
            });

            for (const btn of plusButtons) {
                const rect = btn.getBoundingClientRect();
                if (rect.width === 0 || rect.height === 0) continue;
                btn.click();
                break;
            }

            const inputs = Array.from(document.querySelectorAll('input[type="file"]'));
            if (inputs.length === 0) return false;
            inputs[inputs.length - 1].setAttribute('data-ref-upload', 'true');
            return true;
        }"""
    )
