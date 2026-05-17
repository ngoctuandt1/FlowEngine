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
    _set_aspect_ratio,
    _set_output_count,
    _type_prompt,
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

    output_files = await download_video(
        client,
        media_ids=captured_media_ids or ([media_id] if media_id else []),
        prefix="f2v",
    )
    if not output_files:
        raise RuntimeError("frames-to-video: no output file captured")

    project_url = f"{flow_url(locale)}/project/{project_id}" if project_id else project_url_full
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
