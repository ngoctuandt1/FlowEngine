"""Frames-to-video - Level 1 operation.

Authoritative selectors from the 2026-04-20 live probe:
- Composer chip: supports video-aspect icons (`crop_9_16`, `crop_16_9`) and
  image-mode icons (`crop_landscape`, `crop_square`, `crop_portrait`) because
  Flow persists the last-used composer mode per account.
- Output/sub-mode tabs: `[role='tab']:text-is('Video')`, `[role='tab']:text-is('Frames')`
"""

import asyncio
import json
import logging
import os
import re
import time
from pathlib import Path

from flow.download import download_video
from flow.failure_capture import message_with_failure_capture
from flow.login import handle_login_redirect, is_login_page
from flow.model_selector import DEFAULT_MODEL, select_model
from flow.navigation import extract_project_id, flow_url
from flow.submit import submit_with_confirmation
from flow.wait import wait_for_completion
from flow.operations._base import resolve_final_media_id
from flow.operations._l1_status_poll import download_via_url, poll_status_via_api
from flow.operations.generate import (
    NEW_PROJECT_SELECTORS,
    _count_visible_cards,
    _dismiss_overlays,
    _ensure_video_composer_mode,
    _guard_l1_submit,
    _set_aspect_ratio,
    _set_output_count,
    _select_video_composer_subtab,
    _type_prompt,
    _verify_frames_upload_affordances,
    _wait_for_composer,
)

try:  # Wave-1 reverse API helper; guarded so default UI path is independent.
    from flow.operations.frames_api import (
        get_f2v_request_template,
        install_f2v_request_capture,
        replay_f2v_via_inflate,
    )
except Exception as exc:  # pragma: no cover - guarded fallback
    get_f2v_request_template = None
    install_f2v_request_capture = None
    replay_f2v_via_inflate = None
    _F2V_API_IMPORT_ERROR = exc
else:
    _F2V_API_IMPORT_ERROR = None

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


def _reverse_f2v_enabled() -> bool:
    return os.getenv("FLOW_F2V_VIA_REVERSE", "0") == "1"


def _install_f2v_capture_if_enabled(client) -> bool:
    if not _reverse_f2v_enabled():
        return False
    if install_f2v_request_capture is None:
        logger.info(
            "FLOW_F2V_VIA_REVERSE=1 but frames_api unavailable; continuing UI path (%s)",
            _F2V_API_IMPORT_ERROR,
        )
        return False
    try:
        install_f2v_request_capture(client)
    except Exception as exc:
        logger.info("F2V request capture install failed; continuing UI path: %s", exc)
        return False
    return True


def _current_f2v_template(client):
    if get_f2v_request_template is None:
        return None
    try:
        return get_f2v_request_template(client)
    except Exception as exc:
        logger.info("F2V request template unavailable; continuing UI path: %s", exc)
        return None


def _extract_replay_media_ids(replay_result) -> list[str]:
    if isinstance(replay_result, str) and replay_result:
        return [replay_result]
    if isinstance(replay_result, list):
        return [str(item) for item in replay_result if item]
    if not isinstance(replay_result, dict):
        return []
    media_ids = replay_result.get("media_ids") or []
    media_id = replay_result.get("media_id") or replay_result.get("gen_id")
    if media_id:
        media_ids = [media_id, *media_ids]
    unique_media_ids = []
    for media_id_value in media_ids:
        if media_id_value and media_id_value not in unique_media_ids:
            unique_media_ids.append(str(media_id_value))
    return unique_media_ids


def _record_replay_media_id(client, media_id: str, *, source: str) -> None:
    recorder = getattr(client, "_record_media_id", None)
    if callable(recorder):
        recorder(media_id, source=source, url=f"{source}://replay")
        return
    events = getattr(client, "_media_id_events", None)
    if isinstance(events, list) and media_id not in {
        event.get("mid") or event.get("media_id")
        for event in events
        if isinstance(event, dict)
    }:
        events.append(
            {
                "mid": media_id,
                "source": source,
                "url": f"{source}://replay",
                "ts": time.time(),
            }
        )


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


def _project_id_from_template_or_page(template, page_url: str) -> str:
    body = _template_body(template)
    client_context = body.get("clientContext") if isinstance(body, dict) else None
    if isinstance(client_context, dict):
        project_id = client_context.get("projectId")
        if project_id:
            return str(project_id)
    return extract_project_id(page_url) or extract_project_id((template or {}).get("url", "")) or ""


def _template_body(template) -> dict:
    raw = (template or {}).get("post_data")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, bytes):
        try:
            raw = raw.decode("utf-8")
        except UnicodeDecodeError:
            return {}
    if not isinstance(raw, str):
        return {}
    try:
        parsed = json.loads(raw)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


async def _finalize_l1_replay_result(
    client,
    *,
    project_id: str,
    locale: str,
    replay_media_id: str,
    operation_label: str,
    replay_source: str,
    failure_prefix: str,
    download_prefix: str,
    status_poller=None,
    url_downloader=None,
) -> dict:
    status_poller = status_poller or poll_status_via_api
    url_downloader = url_downloader or download_via_url
    _record_replay_media_id(client, replay_media_id, source=replay_source)

    logger.info(
        "%s replay finalize: polling status API for media_id=%s",
        operation_label,
        replay_media_id[:20],
    )
    poll_result = await status_poller(
        client,
        gen_ids=[replay_media_id],
        project_id=project_id or None,
        hard_timeout_sec=900.0,
    )

    slot = poll_result.get(replay_media_id) if isinstance(poll_result, dict) else None
    if not isinstance(slot, dict):
        message = f"{operation_label} replay: status API returned no slot for media_id={replay_media_id}"
        message = await message_with_failure_capture(
            client,
            f"{failure_prefix}_status_no_entry",
            message,
        )
        raise RuntimeError(message)

    status = slot.get("status")
    if status == "failed":
        error = slot.get("error") or "unknown"
        message = f"{operation_label} replay: status API reports failed for media_id={replay_media_id}: {error}"
        message = await message_with_failure_capture(
            client,
            f"{failure_prefix}_status_failed",
            message,
        )
        raise RuntimeError(message)

    if status != "completed":
        message = f"{operation_label} replay: status API did not reach completed (status={status}) for media_id={replay_media_id}"
        message = await message_with_failure_capture(
            client,
            f"{failure_prefix}_status_timeout",
            message,
        )
        raise RuntimeError(message)

    media_url = slot.get("media_url")
    if not isinstance(media_url, str) or not media_url:
        message = f"{operation_label} replay: status completed but no media URL available for media_id={replay_media_id}"
        message = await message_with_failure_capture(
            client,
            f"{failure_prefix}_no_media_url",
            message,
        )
        raise RuntimeError(message)

    download_dir = _replay_download_dir(client)
    try:
        download_dir.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        logger.debug(
            "%s replay finalize: download dir mkdir failed (%s) for %s; continuing",
            operation_label,
            exc,
            download_dir,
        )
    out_path = download_dir / f"{download_prefix}_replay_{replay_media_id[:20]}_{int(time.time())}.mp4"

    logger.info(
        "%s replay finalize: downloading via direct URL (media_id=%s -> %s)",
        operation_label,
        replay_media_id[:20],
        out_path.name,
    )
    saved_path = await url_downloader(
        client,
        url=media_url,
        out_path=str(out_path),
    )
    if not saved_path:
        message = f"{operation_label} replay: direct-URL download returned empty path for media_id={replay_media_id}"
        message = await message_with_failure_capture(
            client,
            f"{failure_prefix}_download_failed",
            message,
        )
        raise RuntimeError(message)

    project_url = f"{flow_url(locale)}/project/{project_id}" if project_id else ""
    edit_url_val = (
        f"{flow_url(locale)}/project/{project_id}/edit/{replay_media_id}"
        if project_id
        else getattr(client.page, "url", "")
    )
    return {
        "project_url": project_url,
        "media_id": replay_media_id,
        "edit_url": edit_url_val,
        "output_files": [saved_path],
        "generation_id": client._gen_id,
        "profile": client.profile_name,
    }


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
    capture_ready = _install_f2v_capture_if_enabled(client)
    locale = ""
    homepage = flow_url(locale)

    if capture_ready and replay_f2v_via_inflate is not None:
        template = _current_f2v_template(client)
        if template is not None and end_image_path is None:
            logger.info("F2V reverse-API replay requires an end frame; continuing UI path")
        elif template is not None:
            try:
                if "/vi/" in str(getattr(page, "url", "")):
                    locale = "vi"
                replay_project_id = _project_id_from_template_or_page(template, getattr(page, "url", ""))
                client.clear_captures()
                replay_result = await replay_f2v_via_inflate(
                    client,
                    prompt,
                    start_image_path,
                    end_image_path,
                )
                replay_media_ids = _extract_replay_media_ids(replay_result)
                if not replay_media_ids:
                    raise RuntimeError("F2V reverse-API replay returned no media_id")
                replay_media_id = replay_media_ids[0]
                replay_count = getattr(client, "_f2v_replay_count", 0) + 1
                setattr(client, "_f2v_replay_count", replay_count)
                logger.info(
                    "F2V replay submit accepted via reverse API "
                    "(count=%d media_ids=%s) -- finalizing via status API + direct URL download",
                    replay_count,
                    replay_media_ids,
                )
                return await _finalize_l1_replay_result(
                    client,
                    project_id=replay_project_id,
                    locale=locale,
                    replay_media_id=replay_media_id,
                    operation_label="frames-to-video",
                    replay_source="f2v_replay",
                    failure_prefix="f2v_replay",
                    download_prefix="f2v",
                )
            except RuntimeError as exc:
                logger.warning(
                    "F2V reverse-API replay failed; falling back to UI path: %s",
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
    await _reveal_composer_if_collapsed(page)
    await _ensure_video_composer_mode(page)
    await _set_output_count(page, 1)
    await select_model(page, model=model, free_mode=free_mode, profile=client.profile_name)
    await _ensure_video_composer_mode(page)
    await _set_aspect_ratio(page, aspect_ratio)
    await _set_output_count(page, 1)
    await _ensure_frames_mode(page)
    await _close_composer_menu(page)
    await _verify_frames_mode(page)
    await _verify_frames_upload_affordances(page)
    await _upload_frame(page, "Start", start_image_path)
    if end_image_path:
        await _upload_frame(page, "End", end_image_path)

    await _type_prompt(page, prompt)

    await _guard_l1_submit(page)
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
    await _select_video_composer_subtab(page, "Frames")


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


# Tokens that identify a real composer chip (count / mode / aspect / model).
# A project-level toolbar button ("Add Media", "View Settings", overflow
# more_vert) contains none of these and must NOT be treated as the chip.
#
# Tightened to avoid false-positives on Material icon ligatures:
# - bare "image" matches the image-icon toolbar button
# - "video" matches the video_library ligature
# - "frames?" matches the filter_frames ligature
# - "portrait" / "landscape" / "square" match crop_portrait / crop_landscape / crop_square icons
# Only tokens that NEVER appear in toolbar icon text are kept:
# - output-count chips: x1, x2, 1x, 2x, etc.
# - aspect ratios: 16:9, 9:16, 1:1 (colon-separated digit pairs)
# - versioned model names: Veo, Imagen, Lyria, Omni (not generic icon words)
_COMPOSER_CHIP_TOKEN_RE = re.compile(
    r"""
    \b(?:
        x[1-4] | [1-4]x |               # output count chips: x1, x2, 1x, 2x
        \d+\s*:\s*\d+ |                  # aspect ratio: 16:9, 9:16, 1:1
        Veo | Imagen | Lyria | Omni |    # model names (title-case — icon ligatures are lowercase)
        Veo\s+\d | Imagen\s+\d           # versioned: Veo 2, Imagen 3
    )\b
    """,
    re.VERBOSE | re.IGNORECASE,
)

# "Add Media" / project-toolbar entry-point button. In the collapsed project
# view Flow hides the composer chip behind this control; clicking it reveals
# the composer (and the f2v upload affordances).
_ADD_MEDIA_BUTTON_SELECTORS = (
    "button:has(i:text-is('add')):has-text('Add Media')",
    "button:has(i:text-is('add')):has-text('Add media')",
    "[role='button']:has(i:text-is('add')):has-text('Add')",
    "button:has(i:text-is('add'))",
)


async def _composer_chip_present(page) -> bool:
    """True when a real composer chip (count/mode/aspect/model) is visible.

    Distinguishes the composer chip from project-level toolbar buttons such as
    `add` (Add Media), `settings_2` (View Settings), `filter_list`, and the
    `more_vert` overflow menus — none of which carry composer tokens.
    """
    try:
        texts = await page.evaluate(
            r"""() => {
                const visible = (el) => {
                    const s = getComputedStyle(el);
                    const r = el.getBoundingClientRect();
                    return s.display !== 'none' && s.visibility !== 'hidden'
                        && parseFloat(s.opacity || '1') > 0
                        && r.width > 0 && r.height > 0;
                };
                return Array.from(document.querySelectorAll("button[aria-haspopup='menu']"))
                    .filter(visible)
                    .map((el) => (el.innerText || el.textContent || '').trim());
            }"""
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("composer chip probe failed: %s", exc)
        return False
    return any(_COMPOSER_CHIP_TOKEN_RE.search(text or "") for text in texts)


async def _reveal_composer_if_collapsed(page) -> None:
    """Click "Add Media" to expose the composer when only the project toolbar shows.

    Newer Flow project views open in a collapsed state where the composer chip
    (and its Video/Frames tabs) is not yet mounted; the visible menu buttons are
    project-level only (`more_vert`, `filter_list`, `add`, `settings_2`). The
    composer is revealed by clicking the `add` / "Add Media" entry point. This is
    the upstream fix for the
    "Could not open composer menu for Video mode; visible menu buttons=
    [more_vert, filter_list, add, settings_2, more_vert]" failure.
    """
    if await _composer_chip_present(page):
        return

    try:
        visible_buttons = await _collect_visible_menu_button_texts(page)
    except Exception as exc:  # pragma: no cover - defensive (e.g. detached page)
        logger.debug("visible menu button probe failed during reveal: %s", exc)
        visible_buttons = []
    logger.info(
        "Composer chip absent (visible menu buttons=%s); attempting Add Media reveal",
        visible_buttons,
    )

    for selector in _ADD_MEDIA_BUTTON_SELECTORS:
        try:
            button = page.locator(selector).first
            if not await button.is_visible(timeout=1500):
                continue
            await button.click(timeout=3000)
            logger.info("Clicked Add Media entry point via: %s", selector)
            await asyncio.sleep(0.6)
            await _wait_for_composer(page, timeout_sec=8.0)
            if await _composer_chip_present(page):
                logger.info("Composer chip revealed after Add Media click")
                return
            logger.warning(
                "Add Media click via %s did not reveal composer chip; trying next selector",
                selector,
            )
        except Exception as exc:
            logger.debug("Add Media reveal attempt failed for %s: %s", selector, exc)
            continue

    # Could not reveal the chip — let _ensure_video_composer_mode emit its own
    # detailed diagnostics rather than masking the failure here.
    logger.warning(
        "Add Media reveal exhausted without exposing composer chip; "
        "downstream composer-mode step will surface diagnostics"
    )


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
        r"""() => {
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
    slot = await _locate_frame_upload_slot(page, label)
    await _click_slot_then_upload_file(page, slot, image_path, label=label)
    await _accept_upload_rights_notice(page)
    await _wait_for_frame_upload_applied(page, label)


async def _locate_frame_upload_slot(page, label: str) -> dict:
    slot = await page.evaluate(
        r"""(label) => {
            const visible = (el) => {
                const style = getComputedStyle(el);
                const rect = el.getBoundingClientRect();
                return style.display !== 'none'
                    && style.visibility !== 'hidden'
                    && Number(style.opacity || 1) !== 0
                    && rect.width > 0
                    && rect.height > 0;
            };
            const center = (el, source) => {
                const rect = el.getBoundingClientRect();
                return {
                    x: Math.round(rect.left + rect.width / 2),
                    y: Math.round(rect.top + rect.height / 2),
                    w: Math.round(rect.width),
                    h: Math.round(rect.height),
                    source,
                    text: (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 160),
                };
            };
            const exactLabels = Array.from(document.querySelectorAll('body *')).filter((el) => {
                if (!visible(el)) return false;
                const text = (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim();
                return text === label;
            });
            for (const textNode of exactLabels) {
                let current = textNode;
                for (let depth = 0; current && depth < 8; depth += 1, current = current.parentElement) {
                    const clickables = Array.from(current.querySelectorAll('button, [role="button"], label'))
                        .filter(visible);
                    if (clickables.length) {
                        clickables.sort((a, b) => {
                            const aRect = a.getBoundingClientRect();
                            const bRect = b.getBoundingClientRect();
                            return (aRect.width * aRect.height) - (bRect.width * bRect.height);
                        });
                        return center(clickables[0], 'descendant-clickable');
                    }
                    const rect = current.getBoundingClientRect();
                    if (rect.width >= 40 && rect.height >= 40 && rect.width <= 220 && rect.height <= 140) {
                        return center(current, 'ancestor-slot');
                    }
                }
            }
            return null;
        }""",
        label,
    )
    if not slot:
        raise RuntimeError(f"Could not locate upload slot for {label} frame")
    return slot


async def _click_slot_then_upload_file(page, slot: dict, image_path: str, *, label: str) -> None:
    try:
        async with page.expect_file_chooser(timeout=4000) as chooser_info:
            await page.mouse.click(slot["x"], slot["y"])
        chooser = await chooser_info.value
        await chooser.set_files(image_path)
        await asyncio.sleep(1)
        logger.info("%s frame upload used direct file chooser", label)
        return
    except Exception:
        logger.info("%s frame slot opened picker instead of direct chooser", label)

    await _click_picker_upload_media(page, image_path, label=f"{label} frame")


async def _click_picker_upload_media(page, image_path: str, *, label: str) -> None:
    selectors = [
        "button:has(i:text-is('upload')):has-text('Upload media')",
        "button:has-text('Upload media')",
        "[role='button']:has-text('Upload media')",
        "button:has(i:text-is('upload'))",
    ]
    last_error: Exception | None = None
    for selector in selectors:
        button = page.locator(selector).last
        try:
            if not await button.is_visible(timeout=3000):
                continue
            async with page.expect_file_chooser(timeout=5000) as chooser_info:
                await button.click(timeout=3000)
            chooser = await chooser_info.value
            await chooser.set_files(image_path)
            await asyncio.sleep(1)
            logger.info("%s upload used media-picker Upload media action", label)
            return
        except Exception as exc:
            last_error = exc
            continue
    raise RuntimeError(f"Could not locate Upload media action for {label}") from last_error


async def _accept_upload_rights_notice(page) -> None:
    try:
        dialog = page.locator("[role='dialog']:has-text('Notice')").last
        if not await dialog.is_visible(timeout=2500):
            return
        agree = dialog.locator("button:has-text('I agree'), [role='button']:has-text('I agree')").last
        if await agree.is_visible(timeout=1000):
            await agree.click(timeout=3000)
            await asyncio.sleep(2)
            logger.info("Accepted upload rights notice")
    except Exception as exc:
        logger.debug("Upload rights notice not accepted or absent: %s", exc)


async def _wait_for_frame_upload_applied(page, label: str, timeout_sec: float = 45.0) -> None:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        try:
            applied = await page.evaluate(
                r"""(label) => {
                    const visible = (el) => {
                        const style = getComputedStyle(el);
                        const rect = el.getBoundingClientRect();
                        return style.display !== 'none'
                            && style.visibility !== 'hidden'
                            && Number(style.opacity || 1) !== 0
                            && rect.width > 0
                            && rect.height > 0;
                    };
                    const normalize = (value) => (value || '')
                        .toLowerCase()
                        .normalize('NFD')
                        .replace(/[\u0300-\u036f]/g, '')
                        .replace(/\u0111/g, 'd')
                        .replace(/\s+/g, ' ')
                        .trim();
                    const textOf = (el) => normalize(el.innerText || el.textContent || '');
                    const requested = normalize(label);
                    const targetTerms = requested.includes('end')
                        ? ['end', 'last', 'final', 'cuoi', 'ket thuc']
                        : requested.includes('start')
                            ? ['start', 'first', 'dau', 'bat dau']
                            : [requested];
                    const oppositeTerms = requested.includes('end')
                        ? ['start', 'first', 'dau', 'bat dau']
                        : requested.includes('start')
                            ? ['end', 'last', 'final', 'cuoi', 'ket thuc']
                            : [];
                    const includesAny = (text, terms) => terms.some((term) => term && text.includes(term));
                    const labelCandidates = Array.from(document.querySelectorAll('body *'))
                        .filter(visible)
                        .map((el) => ({el, text: textOf(el)}))
                        .filter(({text}) => text && includesAny(text, targetTerms) && !includesAny(text, oppositeTerms))
                        .sort((a, b) => {
                            const aRect = a.el.getBoundingClientRect();
                            const bRect = b.el.getBoundingClientRect();
                            const lengthDelta = a.text.length - b.text.length;
                            if (lengthDelta !== 0) return lengthDelta;
                            return (aRect.width * aRect.height) - (bRect.width * bRect.height);
                        });
                    let matchedTargetLabel = false;
                    for (const {el: labelEl} of labelCandidates) {
                        matchedTargetLabel = true;
                        let current = labelEl;
                        for (let depth = 0; current && depth < 8; depth += 1, current = current.parentElement) {
                            const currentText = textOf(current);
                            if (includesAny(currentText, oppositeTerms)) break;
                            const thumbnails = Array.from(current.querySelectorAll('img')).filter(visible);
                            if (thumbnails.length > 0) {
                                return {
                                    ok: true,
                                    reason: 'target-slot-thumbnail',
                                    count: thumbnails.length,
                                    label: (labelEl.innerText || labelEl.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 80),
                                };
                            }
                        }
                    }
                    if (matchedTargetLabel) return {ok: false, reason: 'target-slot-no-thumbnail'};
                    const noticeOpen = Array.from(document.querySelectorAll('[role="dialog"]'))
                        .some((el) => visible(el) && /notice/i.test(el.innerText || el.textContent || ''));
                    if (noticeOpen) return {ok: false, reason: 'notice-open'};
                    const pickerOpen = Array.from(document.querySelectorAll('[role="dialog"], [aria-modal="true"]'))
                        .some((el) => visible(el) && /upload media|images|uploads/i.test(el.innerText || el.textContent || ''));
                    if (pickerOpen) return {ok: false, reason: 'picker-open'};
                    return {ok: false, reason: 'waiting'};
                }""",
                label,
            )
            if applied and applied.get("ok"):
                logger.info("%s frame upload attached: %s", label, applied)
                return
        except Exception:
            pass
        await _accept_upload_rights_notice(page)
        await asyncio.sleep(1)
    raise RuntimeError(f"{label} frame upload did not attach before timeout")


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
