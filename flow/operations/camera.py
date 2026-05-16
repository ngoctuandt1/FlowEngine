"""Camera Move — Level 2 operation.

Navigates to edit URL, clicks Camera, selects a camera preset
(motion or position), submits, waits, downloads.

Camera mode is DIFFERENT from other operations:
- Replaces the composer entirely with a visual preset grid
- No text prompt, no model selector
- User picks a motion preset (e.g. "Dolly in") or position (e.g. "Center")
"""

import asyncio
import logging
import os
import time
from pathlib import Path

from flow.failure_capture import message_with_failure_capture
from flow.navigation import edit_url as build_edit_url, project_url as build_project_url
from flow.submit import submit_with_confirmation
from flow.operations._base import (
    navigate_to_edit,
    wait_for_video_loaded,
    click_action_button,
    count_visible_cards,
    finalize_operation,
)
from flow.operations._l1_status_poll import (
    poll_status_via_api,
    download_via_url,
)

try:  # Wave 1 reverse-API module; keep UI path import-safe.
    from flow.operations.camera_api import (
        install_camera_request_capture,
        get_camera_request_template,
        replay_camera_via_api,
    )
except Exception as exc:  # pragma: no cover - guarded fallback
    install_camera_request_capture = None
    get_camera_request_template = None
    replay_camera_via_api = None
    _CAMERA_API_IMPORT_ERROR = exc
else:
    _CAMERA_API_IMPORT_ERROR = None

logger = logging.getLogger(__name__)

# Camera button (same in EN + VI)
CAMERA_BUTTONS = ["Camera"]
CAMERA_ICON_SELECTOR = "button:has(span:has-text('videocam'))"

# Known camera presets (DOM generic text, EN)
CAMERA_MOTION_PRESETS = [
    "Dolly in", "Dolly out", "Orbit left", "Orbit right",
    "Orbit up", "Orbit low", "Dolly in zoom out", "Dolly out zoom in",
]

CAMERA_POSITION_PRESETS = [
    "Center", "Left", "Right", "High", "Low", "Closer", "Further",
]

ALL_PRESETS = CAMERA_MOTION_PRESETS + CAMERA_POSITION_PRESETS


def _reverse_camera_enabled() -> bool:
    return os.getenv("FLOW_CAMERA_VIA_REVERSE", "0") == "1"


def _install_camera_capture_if_enabled(client) -> bool:
    if not _reverse_camera_enabled():
        return False
    if install_camera_request_capture is None:
        logger.info(
            "FLOW_CAMERA_VIA_REVERSE=1 but camera_api unavailable; "
            "continuing UI path (%s)",
            _CAMERA_API_IMPORT_ERROR,
        )
        return False
    try:
        install_camera_request_capture(client)
    except Exception as exc:
        logger.info(
            "Camera request capture install failed; continuing UI path: %s",
            exc,
        )
        return False
    return True


def _current_camera_template(client):
    if get_camera_request_template is None:
        return None
    try:
        return get_camera_request_template(client)
    except Exception as exc:
        logger.info(
            "Camera request template unavailable; continuing UI path: %s",
            exc,
        )
        return None


def _job_is_l3_plus(job: dict) -> bool:
    try:
        return int(job.get("job_level") or 1) >= 3
    except (TypeError, ValueError):
        return False


def _extract_replay_media_ids(replay_result) -> list[str]:
    if isinstance(replay_result, str) and replay_result:
        return [replay_result]
    if not isinstance(replay_result, dict):
        return []
    media_ids = replay_result.get("media_ids") or []
    media_id = replay_result.get("media_id")
    if media_id:
        media_ids = [media_id, *media_ids]
    unique_media_ids = []
    for media_id_value in media_ids:
        if media_id_value and media_id_value not in unique_media_ids:
            unique_media_ids.append(str(media_id_value))
    return unique_media_ids


def _record_camera_replay_media_id(client, media_id: str) -> None:
    recorder = getattr(client, "_record_media_id", None)
    if callable(recorder):
        recorder(media_id, source="camera_replay", url="camera-replay")
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
                "source": "camera_replay",
                "url": "camera-replay",
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


async def _finalize_camera_replay_result(
    client,
    job: dict,
    *,
    project_id: str,
    locale: str,
    replay_media_id: str,
    download_prefix: str = "cam",
) -> dict:
    _record_camera_replay_media_id(client, replay_media_id)

    logger.info(
        "Camera replay finalize: polling status API for media_id=%s",
        replay_media_id[:20],
    )
    poll_result = await poll_status_via_api(
        client,
        gen_ids=[replay_media_id],
        project_id=project_id or None,
        hard_timeout_sec=600.0,
    )

    slot = poll_result.get(replay_media_id) if isinstance(poll_result, dict) else None
    if not isinstance(slot, dict):
        message = (
            f"camera-move replay: status API returned no slot for "
            f"media_id={replay_media_id}"
        )
        message = await message_with_failure_capture(
            client,
            "camera_replay_status_no_entry",
            message,
        )
        raise RuntimeError(message)

    status = slot.get("status")
    if status == "failed":
        error = slot.get("error") or "unknown"
        message = (
            f"camera-move replay: status API reports failed for "
            f"media_id={replay_media_id}: {error}"
        )
        message = await message_with_failure_capture(
            client,
            "camera_replay_status_failed",
            message,
        )
        raise RuntimeError(message)

    if status != "completed":
        message = (
            f"camera-move replay: status API did not reach completed "
            f"(status={status}) for media_id={replay_media_id}"
        )
        message = await message_with_failure_capture(
            client,
            "camera_replay_status_timeout",
            message,
        )
        raise RuntimeError(message)

    media_url = slot.get("media_url")
    if not isinstance(media_url, str) or not media_url:
        message = (
            f"camera-move replay: status completed but no media URL "
            f"available for media_id={replay_media_id}"
        )
        message = await message_with_failure_capture(
            client,
            "camera_replay_no_media_url",
            message,
        )
        raise RuntimeError(message)

    download_dir = _replay_download_dir(client)
    try:
        download_dir.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        logger.debug(
            "Camera replay finalize: download dir mkdir failed (%s) for %s; "
            "continuing -- download_via_url will surface the real error",
            exc,
            download_dir,
        )
    out_path = download_dir / (
        f"{download_prefix}_replay_{replay_media_id[:20]}_{int(time.time())}.mp4"
    )

    logger.info(
        "Camera replay finalize: downloading via direct URL "
        "(media_id=%s -> %s)",
        replay_media_id[:20],
        out_path.name,
    )
    saved_path = await download_via_url(
        client,
        url=media_url,
        out_path=str(out_path),
    )
    if not saved_path:
        message = (
            f"camera-move replay: direct-URL download returned empty path "
            f"for media_id={replay_media_id}"
        )
        message = await message_with_failure_capture(
            client,
            "camera_replay_download_failed",
            message,
        )
        raise RuntimeError(message)

    proj_url = job.get("project_url") or (
        build_project_url(project_id, locale) if project_id else ""
    )
    edit_url_val = (
        build_edit_url(project_id, replay_media_id, locale)
        if project_id
        else getattr(client.page, "url", "")
    )
    return {
        "project_url": proj_url,
        "media_id": replay_media_id,
        "edit_url": edit_url_val,
        "output_files": [saved_path],
        "generation_id": getattr(client, "_gen_id", None),
        "profile": getattr(client, "profile_name", ""),
    }


async def camera_move(
    client,
    job: dict,
    direction: str = "Dolly in",
) -> dict:
    """Execute camera-move operation.

    Steps:
    1. Navigate to edit URL
    2. Wait for video to load
    3. Click "Camera" button (replaces composer with preset grid)
    4. Select the correct tab (Camera motion vs Camera position)
    5. Click the preset thumbnail
    6. Submit
    7. Wait + Download + Return metadata

    Args:
        client: FlowClient instance
        job: Job dict with edit_url/project_url/media_id
        direction: Camera preset name (e.g. "Dolly in", "Center", "Orbit left")

    Returns: Result dict
    """
    page = client.page
    capture_ready = _install_camera_capture_if_enabled(client)

    # Step 1: Navigate
    edit_url_val, project_id, locale = await navigate_to_edit(client, job)

    # Step 2: Wait for video
    await wait_for_video_loaded(page)

    if capture_ready and _job_is_l3_plus(job):
        template = _current_camera_template(client)
        if template is not None and replay_camera_via_api is not None:
            try:
                client.clear_captures()
                replay_result = await replay_camera_via_api(
                    client,
                    parent_media_id=job["media_id"],
                    direction=direction,
                )
                replay_media_ids = _extract_replay_media_ids(replay_result)
                if not replay_media_ids:
                    raise RuntimeError(
                        "Camera reverse-API replay returned no media_id"
                    )
                replay_media_id = replay_media_ids[0]
                replay_count = getattr(client, "_camera_replay_count", 0) + 1
                setattr(client, "_camera_replay_count", replay_count)
                logger.info(
                    "Camera replay submit accepted via reverse API "
                    "(count=%d media_ids=%s) -- finalizing via status API + direct URL download",
                    replay_count,
                    replay_media_ids,
                )
                return await _finalize_camera_replay_result(
                    client,
                    job,
                    project_id=project_id,
                    locale=locale,
                    replay_media_id=replay_media_id,
                )
            except RuntimeError as exc:
                logger.warning(
                    "Camera reverse-API replay failed; falling back to UI path: %s",
                    exc,
                )

    # Step 3: Click Camera button
    clicked = await click_action_button(page, CAMERA_BUTTONS, client=client)
    if not clicked:
        try:
            icon_btn = page.locator(CAMERA_ICON_SELECTOR).first
            if await icon_btn.is_visible(timeout=2000):
                await icon_btn.click(timeout=3000)
                clicked = True
                logger.info("Clicked Camera via icon fallback")
                await asyncio.sleep(0.5)
        except Exception:
            pass

    if not clicked:
        message = "Failed to find Camera button"
        message = await message_with_failure_capture(
            client,
            "camera_button_not_found",
            message,
        )
        raise RuntimeError(message)

    await asyncio.sleep(1)  # Wait for preset grid to render

    # Step 4: Select correct tab
    is_position = direction in CAMERA_POSITION_PRESETS
    tab_name = "Camera position" if is_position else "Camera motion"

    try:
        tab = page.locator(f"[role='tab']:has-text('{tab_name}')").first
        if await tab.is_visible(timeout=2000):
            await tab.click(timeout=3000)
            logger.info("Switched to tab: %s", tab_name)
            await asyncio.sleep(0.5)
    except Exception:
        # Tab might already be active or not exist — proceed
        logger.warning("Could not switch to tab %s — trying preset anyway", tab_name)

    # Step 5: Click preset thumbnail
    preset_clicked = await _click_preset(page, direction)
    if not preset_clicked:
        message = f"Failed to find camera preset: {direction}"
        message = await message_with_failure_capture(
            client,
            "camera_preset_not_found",
            message,
        )
        raise RuntimeError(message)

    # Step 6: Submit
    before_cards = await count_visible_cards(page)
    client.clear_captures()

    confirmed = await submit_with_confirmation(
        client,
        before_card_count=before_cards,
        failure_kind="camera_submit_not_confirmed",
    )
    if not confirmed:
        message = "Camera submit not confirmed"
        message = await message_with_failure_capture(
            client,
            "camera_submit_not_confirmed",
            message,
        )
        raise RuntimeError(message)

    # Step 7: Wait + Download + Return
    return await finalize_operation(
        client, job,
        job_type="camera-move",
        project_id=project_id,
        locale=locale,
        download_prefix="cam",
    )


async def _click_preset(page, direction: str) -> bool:
    """Click a camera preset by name and verify it becomes active.

    Uses Playwright's `get_by_text(direction, exact=True)` — the only strategy
    that matches real Flow DOM. Earlier selector attempts (`[aria-label=...]`,
    `[role='button']` CSS) were removed after Tier1 live-DOM probing confirmed
    they find zero elements on production Flow: presets have no `aria-label`
    and no explicit `role="button"` attribute (they are plain `<button>` tags).

    Playwright's `exact=True` natively prevents partial matches (direction
    "Low" will not match a hypothetical "Lower" button), replacing the
    anchored-regex defense from the pre-B12 implementation.

    See `docs/FLOW_UI_REFERENCE.md` §Camera Preset Selection & Active State
    and `docs/session-reports/2026-04-17_Tier1_dom-validation.md` §7 B3 for
    the live-DOM ground truth.
    """
    try:
        el = page.get_by_text(direction, exact=True).first
        if await el.is_visible(timeout=2000):
            await el.click(timeout=3000)
            logger.info("Clicked preset via get_by_text(exact=True): %s", direction)
            await asyncio.sleep(0.5)
            if await _verify_preset_selected(page, direction):
                return True
    except Exception as e:
        logger.debug("get_by_text strategy failed for %s: %s", direction, e)

    logger.error("Could not click+verify camera preset: %s", direction)
    return False


async def _verify_preset_selected(page, direction: str) -> bool:
    """Verify the named preset is active via the computed label color.

    Flow renders preset buttons using styled-components hash-only class
    names with no stable keyword (`active` / `selected` / `pressed` all
    absent) and no `aria-pressed` / `aria-selected` attributes. The only
    semantic, release-stable selection signal is the computed `color` of
    the inner label DIV inside the preset BUTTON:

        selected   → rgb(48, 48, 48)   (dim; sum 144)
        unselected → rgb(255, 255, 255) (bright; sum 765)

    The JS below walks `<button>` elements, finds the descendant DIV whose
    text equals `direction`, reads `getComputedStyle(lbl).color`, parses
    the `rgb(r, g, b)` form, and returns true when R+G+B < 400. The
    threshold sits halfway between the two ground-truth sums (144 vs 765)
    so small anti-aliasing variance on either side cannot flip the result.

    Returns False on any failure (label not found, color fails to parse,
    `page.evaluate` raises) — caller treats this as unverified.
    """
    try:
        is_selected = await page.evaluate(
            r"""(direction) => {
                const buttons = Array.from(document.querySelectorAll('button'));
                for (const btn of buttons) {
                    const labels = btn.querySelectorAll('div');
                    for (const lbl of labels) {
                        if ((lbl.textContent || '').trim() === direction) {
                            const color = getComputedStyle(lbl).color;
                            const m = color.match(/rgb\((\d+),\s*(\d+),\s*(\d+)\)/);
                            if (!m) return false;
                            const sum = (+m[1]) + (+m[2]) + (+m[3]);
                            return sum < 400;
                        }
                    }
                }
                return false;
            }""",
            direction,
        )
        if is_selected:
            logger.info("Preset verified selected: %s", direction)
            return True
        logger.warning("Preset clicked but not verified active: %s", direction)
        return False
    except Exception as e:
        logger.warning("Preset verify failed for %s: %s", direction, e)
        return False
