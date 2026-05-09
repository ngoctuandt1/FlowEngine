"""Frames-to-video - Level 1 operation.

Authoritative selectors from the 2026-04-20 live probe:
- Composer chip: supports video-aspect icons (`crop_9_16`, `crop_16_9`) and
  image-mode icons (`crop_landscape`, `crop_square`, `crop_portrait`) because
  Flow persists the last-used composer mode per account.
- Output/sub-mode tabs: `[role='tab']:text-is('Video')`, `[role='tab']:text-is('Frames')`
"""

import asyncio
import logging
from pathlib import Path

from flow.download import download_video
from flow.login import handle_login_redirect, is_login_page
from flow.model_selector import DEFAULT_MODEL, select_model
from flow.navigation import extract_project_id, flow_url
from flow.submit import submit_with_confirmation
from flow.wait import wait_for_completion
from flow.operations._base import resolve_final_media_id
from flow.operations.generate import (
    NEW_PROJECT_SELECTORS,
    _count_visible_cards,
    _dismiss_overlays,
    _set_aspect_ratio,
    _set_output_count,
    _type_prompt,
    _wait_for_composer,
)

logger = logging.getLogger(__name__)

COMPOSER_MENU_SELECTORS = [
    # Video-aspect chip icons.
    "button[aria-haspopup='menu']:has(i:text-is('crop_9_16'))",
    "button[aria-haspopup='menu']:has(i:text-is('crop_16_9'))",
    # Image-mode chip icons.
    "button[aria-haspopup='menu']:has(i:text-is('crop_landscape'))",
    "button[aria-haspopup='menu']:has(i:text-is('crop_square'))",
    "button[aria-haspopup='menu']:has(i:text-is('crop_portrait'))",
]


async def frames_to_video(
    client,
    prompt: str,
    start_image_path: str,
    end_image_path: str | None = None,
    model: str = DEFAULT_MODEL,
    aspect_ratio: str = "16:9",
    free_mode: bool = True,
) -> dict:
    """Create a new video project from a start frame and optional end frame."""
    if not start_image_path:
        raise RuntimeError("frames-to-video requires start_image_path")
    start_image_path = _resolve_image_input_path(start_image_path, label="Start")
    if end_image_path:
        end_image_path = _resolve_image_input_path(end_image_path, label="End")

    page = client.page
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
    await select_model(page, model=model, free_mode=free_mode, profile=client.profile_name)
    await _set_aspect_ratio(page, aspect_ratio)
    await _set_output_count(page, 1)
    await _ensure_frames_mode(page)
    await _close_composer_menu(page)
    await _verify_frames_mode(page)
    await _upload_frame(page, "Start", start_image_path)
    if end_image_path:
        await _upload_frame(page, "End", end_image_path)

    await _type_prompt(page, prompt)

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

    result = await wait_for_completion(client, job_type="frames-to-video")
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
        prefix="f2v",
        metadata={
            "job_type": "frames-to-video",
            "prompt": prompt,
            "media_id": media_id or "",
            "project_url": project_url,
            "profile": client.profile_name or "",
        },
    )
    if not output_files:
        raise RuntimeError("frames-to-video: no output file captured")

    return {
        "project_url": project_url,
        "media_id": media_id,
        "edit_url": edit_url_val or current_url,
        "output_files": output_files,
        "generation_id": client._gen_id,
        "profile": client.profile_name,
    }


async def _click_new_project(page) -> None:
    for sel in NEW_PROJECT_SELECTORS:
        try:
            btn = page.locator(sel).first
            if await btn.is_visible(timeout=2000):
                await btn.click(timeout=5000)
                logger.info("Clicked new project via: %s", sel)
                return
        except Exception:
            continue
    raise RuntimeError("Failed to find '+ New project' button on Flow homepage")


async def _verify_frames_mode(page, timeout_sec: float = 5.0) -> None:
    """Confirm Frames mode is active by checking Start/End upload slots are visible."""
    deadline = asyncio.get_event_loop().time() + timeout_sec
    while asyncio.get_event_loop().time() < deadline:
        try:
            visible = await page.evaluate(
                """() => {
                    const labels = Array.from(document.querySelectorAll('*')).filter((el) => {
                        const t = (el.textContent || '').trim();
                        return (t === 'Start' || t === 'End') && el.children.length === 0;
                    });
                    return labels.length > 0;
                }"""
            )
            if visible:
                logger.info("Frames mode verified: Start/End upload slots present")
                return
        except Exception:
            pass
        await asyncio.sleep(0.4)
    logger.warning("_verify_frames_mode: Start/End slots not found after %.1fs — proceeding anyway", timeout_sec)


async def _ensure_frames_mode(page) -> None:
    await _open_composer_menu(page)
    await _click_tab(page, "Video")
    await _click_tab(page, "Frames")


async def _composer_menu_is_open(page) -> bool:
    open_selectors = (
        "button[aria-haspopup='menu'][data-state='open']",
        "[role='menu'][data-state='open']",
    )
    for sel in open_selectors:
        try:
            if await page.locator(sel).first.is_visible(timeout=500):
                return True
        except Exception:
            continue
    return False


async def _open_composer_menu(page) -> None:
    # The composer chip is a toggle. Re-clicking it while the menu is already
    # open can close the menu and break re-entrant callers that still expect the
    # Image/Video tabs to be visible.
    if await _composer_menu_is_open(page):
        return

    for sel in COMPOSER_MENU_SELECTORS:
        try:
            chip = page.locator(sel).first
            if await chip.is_visible(timeout=2000):
                await chip.click(timeout=3000)
                await asyncio.sleep(0.5)
                return
        except Exception:
            continue
    visible_menu_buttons = await _collect_visible_menu_button_texts(page)
    logger.warning(
        "_open_composer_menu: no composer chip matched %d selectors; visible menu buttons=%s",
        len(COMPOSER_MENU_SELECTORS),
        visible_menu_buttons,
    )
    raise RuntimeError(
        f"Could not open composer chip (tried {len(COMPOSER_MENU_SELECTORS)} icon variants). "
        "Flow may have introduced a new chip icon - run the probe to update."
    )


async def _collect_visible_menu_button_texts(page) -> list[str]:
    return await page.evaluate(
        """() => {
            const visible = (el) => {
                const style = getComputedStyle(el);
                const rect = el.getBoundingClientRect();
                return style.display !== 'none'
                    && style.visibility !== 'hidden'
                    && rect.width > 0
                    && rect.height > 0;
            };

            return Array.from(document.querySelectorAll("button[aria-haspopup='menu']"))
                .filter((el) => visible(el))
                .map((el) => (el.innerText || el.textContent || '').trim())
                .filter((text) => text.length > 0);
        }"""
    )


async def _click_tab(page, label: str) -> None:
    tab = page.locator(f"[role='tab']:text-is('{label}')").first
    if not await tab.is_visible(timeout=3000):
        raise RuntimeError(f"Composer tab not found: {label}")
    if await tab.get_attribute("data-state") != "active":
        await tab.click(timeout=3000)
        await asyncio.sleep(0.3)


async def _close_composer_menu(page) -> None:
    editors = page.locator("[data-slate-editor='true']")
    count = await editors.count()
    if count > 0:
        try:
            await editors.last.click(timeout=2000)
            await asyncio.sleep(0.3)
            return
        except Exception:
            logger.debug("Editor click did not dismiss composer menu; falling back to dead-zone click")
    await page.mouse.click(10, 10)
    await asyncio.sleep(0.3)


async def _upload_frame(page, label: str, image_path: str) -> None:
    target_attr = f"flow-{label.lower()}-upload"
    found = await page.evaluate(
        """(args) => {
            const label = args.label;
            const targetAttr = args.targetAttr;
            for (const input of document.querySelectorAll('input[type="file"]')) {
                input.removeAttribute('data-upload-target');
            }

            const visible = (el) => {
                const s = getComputedStyle(el);
                const r = el.getBoundingClientRect();
                return s.display !== 'none' && s.visibility !== 'hidden'
                    && r.width > 0 && r.height > 0;
            };

            const candidates = Array.from(document.querySelectorAll('*')).filter((el) => {
                if (!visible(el)) return false;
                return (el.textContent || '').trim() === label;
            });

            for (const node of candidates) {
                let cur = node;
                for (let depth = 0; cur && depth < 6; depth += 1, cur = cur.parentElement) {
                    const input = cur.querySelector?.('input[type="file"]');
                    if (input) {
                        input.setAttribute('data-upload-target', targetAttr);
                        return true;
                    }
                }
            }
            return false;
        }""",
        {"label": label, "targetAttr": target_attr},
    )
    if not found:
        raise RuntimeError(f"Could not locate file input for {label} frame")

    input = page.locator(f"input[type='file'][data-upload-target='{target_attr}']").first
    await input.set_input_files(image_path, timeout=10000)
    await asyncio.sleep(1)


def _resolve_image_input_path(path_value: str, *, label: str) -> str:
    """Resolve and validate a local image path for Flow uploads."""
    if not path_value:
        raise RuntimeError(f"{label} image path is required")

    candidate = Path(path_value).expanduser()
    try:
        resolved = candidate.resolve(strict=True)
    except FileNotFoundError:
        raise RuntimeError(f"{label} image not found: {path_value}") from None
    except OSError as exc:
        raise RuntimeError(f"Invalid {label.lower()} image path: {path_value} ({exc})") from exc

    if not resolved.is_file():
        raise RuntimeError(f"{label} image path is not a file: {path_value}")
    return str(resolved)
