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
import unicodedata
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
    finalize_l2_reverse_api_after_accept,
    l2_reverse_api_enabled,
    l2_reverse_api_template_has_auth,
    run_l2_reverse_api_first,
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

CAMERA_PRESET_ALIASES = {
    "Dolly in": (
        "Dolly in", "Dolly In", "DOLLY_IN", "DOLLY IN",
        "Push in", "Push In", "PUSH_IN", "Zoom in", "Zoom In", "ZOOM_IN",
        "Tiến vào", "Tien vao", "Tiến gần", "Tien gan", "Thu gần", "Thu gan",
    ),
    "Dolly out": (
        "Dolly out", "Dolly Out", "DOLLY_OUT", "DOLLY OUT",
        "Pull out", "Pull Out", "PULL_OUT", "Zoom out", "Zoom Out", "ZOOM_OUT",
        "Lùi ra", "Lui ra", "Lùi lại", "Lui lai", "Thu xa",
    ),
    "Orbit left": (
        "Orbit left", "Orbit Left", "ORBIT_LEFT", "ORBIT LEFT",
        "Pan left", "Pan Left", "PAN_LEFT", "PAN LEFT",
        "Tilt left", "Tilt Left", "TILT_LEFT", "TILT LEFT",
        "Xoay trái", "Xoay trai", "Quỹ đạo trái", "Quy dao trai",
        "Sang trái", "Sang trai", "Qua trái", "Qua trai",
    ),
    "Orbit right": (
        "Orbit right", "Orbit Right", "ORBIT_RIGHT", "ORBIT RIGHT",
        "Pan right", "Pan Right", "PAN_RIGHT", "PAN RIGHT",
        "Tilt right", "Tilt Right", "TILT_RIGHT", "TILT RIGHT",
        "Xoay phải", "Xoay phai", "Quỹ đạo phải", "Quy dao phai",
        "Sang phải", "Sang phai", "Qua phải", "Qua phai",
    ),
    "Orbit up": (
        "Orbit up", "Orbit Up", "ORBIT_UP", "ORBIT UP",
        "Pan up", "Pan Up", "PAN_UP", "PAN UP",
        "Tilt up", "Tilt Up", "TILT_UP", "TILT UP",
        "Xoay lên", "Xoay len", "Quỹ đạo lên", "Quy dao len", "Lên", "Len",
    ),
    "Orbit low": (
        "Orbit low", "Orbit Low", "ORBIT_LOW", "ORBIT LOW",
        "Orbit down", "Orbit Down", "ORBIT_DOWN", "ORBIT DOWN",
        "Pan down", "Pan Down", "PAN_DOWN", "PAN DOWN",
        "Tilt down", "Tilt Down", "TILT_DOWN", "TILT DOWN",
        "Xoay xuống", "Xoay xuong", "Quỹ đạo xuống", "Quy dao xuong",
        "Xuống", "Xuong",
    ),
    "Dolly in zoom out": (
        "Dolly in zoom out", "Dolly In Zoom Out", "DOLLY_IN_ZOOM_OUT",
        "Push in zoom out", "Push In Zoom Out", "PUSH_IN_ZOOM_OUT",
        "Tiến vào thu xa", "Tien vao thu xa",
    ),
    "Dolly out zoom in": (
        "Dolly out zoom in", "Dolly Out Zoom In", "DOLLY_OUT_ZOOM_IN",
        "Pull out zoom in", "Pull Out Zoom In", "PULL_OUT_ZOOM_IN",
        "Lùi ra thu gần", "Lui ra thu gan",
    ),
    "Center": ("Center", "CENTER", "Giữa", "Giua", "Trung tâm", "Trung tam"),
    "Left": ("Left", "LEFT", "Trái", "Trai", "Bên trái", "Ben trai"),
    "Right": ("Right", "RIGHT", "Phải", "Phai", "Bên phải", "Ben phai"),
    "High": ("High", "HIGH", "Cao", "Ở trên", "O tren"),
    "Low": ("Low", "LOW", "Thấp", "Thap", "Ở dưới", "O duoi"),
    "Closer": ("Closer", "CLOSER", "Gần hơn", "Gan hon", "Gần", "Gan"),
    "Further": ("Further", "FURTHER", "Xa hơn", "Xa hon", "Xa"),
}

CAMERA_PRESET_ICON_ALIASES = {
    "Dolly in": (
        "zoom_in", "add", "arrow_downward", "south", "open_in_full",
        "keyboard_double_arrow_down",
    ),
    "Dolly out": (
        "zoom_out", "remove", "arrow_upward", "north", "close_fullscreen",
        "keyboard_double_arrow_up",
    ),
    "Orbit left": (
        "arrow_back", "arrow_left", "arrow_left_alt", "keyboard_arrow_left",
        "keyboard_double_arrow_left", "chevron_left", "west", "rotate_left",
    ),
    "Orbit right": (
        "arrow_forward", "arrow_right", "arrow_right_alt", "keyboard_arrow_right",
        "keyboard_double_arrow_right", "chevron_right", "east", "rotate_right",
        "directions_car",
    ),
    "Orbit up": (
        "arrow_upward", "arrow_up", "keyboard_arrow_up", "north", "expand_less",
    ),
    "Orbit low": (
        "arrow_downward", "arrow_down", "keyboard_arrow_down", "south", "expand_more",
    ),
    "Dolly in zoom out": ("zoom_out_map", "travel_explore"),
    "Dolly out zoom in": ("center_focus_strong", "center_focus_weak"),
    "Center": ("filter_center_focus", "center_focus_strong", "my_location"),
    "Left": ("keyboard_arrow_left", "arrow_back", "west"),
    "Right": ("keyboard_arrow_right", "arrow_forward", "east"),
    "High": ("keyboard_arrow_up", "arrow_upward", "north"),
    "Low": ("keyboard_arrow_down", "arrow_downward", "south"),
    "Closer": ("zoom_in", "add"),
    "Further": ("zoom_out", "remove"),
}

CAMERA_TAB_NAMES = {
    "motion": ("Camera motion", "Chuyển động camera", "Chuyen dong camera"),
    "position": ("Camera position", "Vị trí camera", "Vi tri camera"),
}

BUTTON_SCAN_SELECTOR = "button, [role='button']"


def _normalize_preset_key(value: object) -> str:
    text = "" if value is None else str(value)
    decomposed = unicodedata.normalize("NFKD", text)
    without_marks = "".join(
        character for character in decomposed
        if not unicodedata.combining(character)
    )
    normalized_characters = []
    for character in without_marks.lower():
        if character.isalnum():
            normalized_characters.append(character)
        else:
            normalized_characters.append(" ")
    return " ".join("".join(normalized_characters).split())


def _build_alias_lookup() -> dict[str, str]:
    lookup: dict[str, str] = {}
    for canonical, aliases in CAMERA_PRESET_ALIASES.items():
        lookup[_normalize_preset_key(canonical)] = canonical
        for alias in aliases:
            alias_key = _normalize_preset_key(alias)
            if alias_key:
                lookup[alias_key] = canonical
    return lookup


CAMERA_PRESET_ALIAS_LOOKUP = _build_alias_lookup()


def _canonical_preset_for_direction(direction: str) -> str:
    direction_text = (direction or "").strip()
    direction_key = _normalize_preset_key(direction_text)
    return CAMERA_PRESET_ALIAS_LOOKUP.get(direction_key, direction_text)


def _append_unique_preset_candidate(candidates: list[str], value: object) -> None:
    if not isinstance(value, str):
        return
    candidate = value.strip()
    if not candidate:
        return
    candidate_key = _normalize_preset_key(candidate)
    if not candidate_key:
        return
    existing_keys = {_normalize_preset_key(existing) for existing in candidates}
    if candidate_key not in existing_keys:
        candidates.append(candidate)


def _preset_label_candidates(direction: str) -> list[str]:
    canonical = _canonical_preset_for_direction(direction)
    aliases = CAMERA_PRESET_ALIASES.get(canonical, (canonical,))
    candidates: list[str] = []
    _append_unique_preset_candidate(candidates, direction)
    _append_unique_preset_candidate(candidates, canonical)
    for alias in aliases:
        _append_unique_preset_candidate(candidates, alias)
        if "_" in alias:
            _append_unique_preset_candidate(candidates, alias.replace("_", " "))
        if " " in alias:
            _append_unique_preset_candidate(candidates, alias.replace(" ", "_"))
    return candidates


def _direction_is_position(direction: str) -> bool:
    return _canonical_preset_for_direction(direction) in CAMERA_POSITION_PRESETS


def _reverse_camera_enabled() -> bool:
    return l2_reverse_api_enabled("FLOW_CAMERA_VIA_REVERSE")


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
    reverse_enabled = _reverse_camera_enabled()
    if reverse_enabled:
        _install_camera_capture_if_enabled(client)

    # Step 1: Navigate
    edit_url_val, project_id, locale = await navigate_to_edit(client, job)

    # Step 2: Wait for video
    await wait_for_video_loaded(page)

    template = _current_camera_template(client) if reverse_enabled else None
    if not reverse_enabled:
        reverse_unavailable_reason = "operation reverse API disabled"
    elif replay_camera_via_api is None:
        reverse_unavailable_reason = f"camera_api unavailable: {_CAMERA_API_IMPORT_ERROR}"
    elif template is None:
        reverse_unavailable_reason = "captured camera template unavailable"
    elif not l2_reverse_api_template_has_auth(template):
        reverse_unavailable_reason = "captured camera template missing authorization header"
    else:
        reverse_unavailable_reason = ""
    reverse_outcome = await run_l2_reverse_api_first(
        operation="camera-move",
        log=logger,
        available=(
            reverse_enabled
            and template is not None
            and replay_camera_via_api is not None
            and l2_reverse_api_template_has_auth(template)
        ),
        unavailable_reason=reverse_unavailable_reason,
        metadata={
            "project_id": project_id,
            "parent_media_id": job.get("media_id"),
            "template_url": template.get("url") if isinstance(template, dict) else None,
            "direction": direction,
        },
        timeout_sec=660.0,
        call=lambda: _run_camera_reverse_api(
            client,
            job,
            direction=direction,
            project_id=project_id,
            locale=locale,
        ),
    )
    if reverse_outcome.succeeded:
        return reverse_outcome.result
    if reverse_outcome.status == "recoverable_error":
        logger.warning(
            "Camera reverse-API replay failed; falling back to UI path: %s",
            reverse_outcome.error,
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
    is_position = _direction_is_position(direction)
    tab_key = "position" if is_position else "motion"
    tab_names = CAMERA_TAB_NAMES[tab_key]

    tab_clicked = False
    for tab_name in tab_names:
        try:
            tab = page.locator(f"[role='tab']:has-text('{tab_name}')").first
            if await tab.is_visible(timeout=2000):
                await tab.click(timeout=3000)
                logger.info("Switched to tab: %s", tab_name)
                await asyncio.sleep(0.5)
                tab_clicked = True
                break
        except Exception:
            logger.debug("Could not switch to tab %s", tab_name)
    if not tab_clicked:
        logger.warning(
            "Could not switch to %s tab via names %s -- trying preset anyway",
            tab_key,
            tab_names,
        )

    # Step 5: Click preset thumbnail
    preset_clicked = await _click_preset(page, direction)
    if not preset_clicked:
        diagnostic = await _camera_preset_failure_diagnostics(page)
        message = f"Failed to find camera preset: {direction}. {diagnostic}"
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


async def _run_camera_reverse_api(
    client,
    job: dict,
    *,
    direction: str,
    project_id: str,
    locale: str,
) -> dict:
    if replay_camera_via_api is None:
        raise RuntimeError("camera_api unavailable")
    client.clear_captures()
    replay_result = await replay_camera_via_api(
        client,
        parent_media_id=job["media_id"],
        direction=direction,
    )
    replay_media_ids = _extract_replay_media_ids(replay_result)
    if not replay_media_ids:
        raise RuntimeError("Camera reverse-API replay returned no media_id")
    replay_media_id = replay_media_ids[0]
    replay_count = getattr(client, "_camera_replay_count", 0) + 1
    setattr(client, "_camera_replay_count", replay_count)
    logger.info(
        "Camera replay submit accepted via reverse API "
        "(count=%d media_ids=%s) -- finalizing via status API + direct URL download",
        replay_count,
        replay_media_ids,
    )
    return await finalize_l2_reverse_api_after_accept(
        client,
        operation="camera-move",
        media_id=replay_media_id,
        finalize_call=lambda: _finalize_camera_replay_result(
            client,
            job,
            project_id=project_id,
            locale=locale,
            replay_media_id=replay_media_id,
        ),
    )

async def _click_preset(page, direction: str) -> bool:
    """Click a camera preset by name and verify it becomes active.

    Primary path stays Playwright exact text. Fallback scans visible preset
    buttons and matches normalized text, aria/data fields, or icon ligatures.
    """
    candidates = _preset_label_candidates(direction)
    canonical = _canonical_preset_for_direction(direction)
    known_direction = canonical in ALL_PRESETS

    for candidate in candidates:
        try:
            el = page.get_by_text(candidate, exact=True).first
            if await el.is_visible(timeout=2000):
                await el.click(timeout=3000)
                logger.info(
                    "Clicked preset via get_by_text(exact=True): %s "
                    "(requested=%s canonical=%s)",
                    candidate,
                    direction,
                    canonical,
                )
                await asyncio.sleep(0.5)
                if await _verify_preset_selected(page, candidate):
                    return True
                logger.error(
                    "Could not click+verify camera preset: %s "
                    "(matched text=%s canonical=%s)",
                    direction,
                    candidate,
                    canonical,
                )
                return False
        except Exception as e:
            logger.debug(
                "get_by_text strategy failed for %s candidate %s: %s",
                direction,
                candidate,
                e,
            )

    if known_direction and await _click_preset_via_button_scan(
        page,
        direction=direction,
        canonical=canonical,
        candidates=candidates,
    ):
        return True

    logger.error("Could not click+verify camera preset: %s", direction)
    return False


async def _click_preset_via_button_scan(
    page,
    *,
    direction: str,
    canonical: str,
    candidates: list[str],
) -> bool:
    target_keys = [_normalize_preset_key(candidate) for candidate in candidates]
    icon_keys = [
        _normalize_preset_key(icon)
        for icon in CAMERA_PRESET_ICON_ALIASES.get(canonical, ())
    ]
    try:
        match = await page.evaluate(
            _CAMERA_PRESET_MATCH_SCRIPT,
            {
                "selector": BUTTON_SCAN_SELECTOR,
                "targetKeys": [key for key in target_keys if key],
                "iconKeys": [key for key in icon_keys if key],
            },
        )
    except Exception as exc:
        logger.debug("Camera preset button scan failed for %s: %s", direction, exc)
        return False

    if not isinstance(match, dict) or not isinstance(match.get("index"), int):
        return False

    source = str(match.get("source") or "unknown")
    matched_value = str(match.get("matchedValue") or "").strip()
    try:
        button = page.locator(BUTTON_SCAN_SELECTOR).nth(match["index"])
        await button.click(timeout=3000)
        logger.info(
            "Clicked preset via button scan: %s -> %s "
            "(source=%s value=%r index=%s)",
            direction,
            canonical,
            source,
            matched_value,
            match["index"],
        )
        await asyncio.sleep(0.5)
    except Exception as exc:
        logger.debug(
            "Camera preset button-scan click failed for %s at index %s: %s",
            direction,
            match.get("index"),
            exc,
        )
        return False

    verify_targets = [matched_value, canonical, direction]
    for verify_target in verify_targets:
        if verify_target and await _verify_preset_selected(page, verify_target):
            return True

    if source in {"aria", "data", "icon"}:
        logger.warning(
            "Clicked camera preset %s via %s=%r but color verify had no "
            "matching label; accepting fallback click",
            direction,
            source,
            matched_value,
        )
        return True

    logger.error("Could not click+verify camera preset: %s", direction)
    return False


async def _camera_preset_failure_diagnostics(page) -> str:
    try:
        buttons = await _scan_visible_preset_buttons(page)
    except Exception as exc:
        logger.warning("Camera preset diagnostic dump failed: %s", exc)
        return f"Visible preset button diagnostic unavailable: {exc}"

    formatted = _format_button_diagnostics(buttons)
    logger.error("Visible camera preset buttons: %s", formatted)
    return f"Visible camera preset buttons: {formatted}"


async def _scan_visible_preset_buttons(page) -> list[dict]:
    buttons = await page.evaluate(
        _CAMERA_PRESET_DUMP_SCRIPT,
        {"selector": BUTTON_SCAN_SELECTOR, "limit": 80},
    )
    if not isinstance(buttons, list):
        return []
    return [button for button in buttons if isinstance(button, dict)]


def _format_button_diagnostics(buttons: list[dict]) -> str:
    if not buttons:
        return "<none>"
    parts = []
    for button in buttons[:40]:
        index = button.get("index")
        text = _short_diagnostic_value(button.get("text"))
        aria = _short_diagnostic_value(button.get("ariaLabel"))
        data_direction = _short_diagnostic_value(button.get("dataDirection"))
        data_preset = _short_diagnostic_value(button.get("dataPreset"))
        icons = button.get("icons") if isinstance(button.get("icons"), list) else []
        icon_text = ",".join(
            _short_diagnostic_value(icon)
            for icon in icons[:4]
            if _short_diagnostic_value(icon)
        )
        fields = [f"#{index}"]
        if text:
            fields.append(f"text={text!r}")
        if aria:
            fields.append(f"aria={aria!r}")
        if data_direction:
            fields.append(f"data-direction={data_direction!r}")
        if data_preset:
            fields.append(f"data-preset={data_preset!r}")
        if icon_text:
            fields.append(f"icons={icon_text!r}")
        parts.append("{" + " ".join(fields) + "}")
    if len(buttons) > 40:
        parts.append(f"... +{len(buttons) - 40} more")
    return "; ".join(parts)


def _short_diagnostic_value(value: object, limit: int = 80) -> str:
    if value is None:
        return ""
    text = " ".join(str(value).split())
    if len(text) <= limit:
        return text
    return f"{text[:limit - 3]}..."


_CAMERA_PRESET_DUMP_SCRIPT = r"""
(payload) => {
    const selector = payload.selector || 'button, [role="button"]';
    const limit = payload.limit || 80;
    const isVisible = (el) => {
        const style = window.getComputedStyle(el);
        if (style.visibility === 'hidden' || style.display === 'none') return false;
        const rect = el.getBoundingClientRect();
        return rect.width > 0 && rect.height > 0;
    };
    const clean = (value) => (value || '').replace(/\s+/g, ' ').trim();
    const collectIcons = (el) => Array.from(
        el.querySelectorAll('i, span, [class*="material"], [data-icon], svg title')
    ).map((node) => clean(
        node.getAttribute('data-icon') ||
        node.getAttribute('aria-label') ||
        node.textContent ||
        node.getAttribute('class') ||
        ''
    )).filter(Boolean);
    return Array.from(document.querySelectorAll(selector))
        .map((el, index) => ({el, index}))
        .filter(({el}) => isVisible(el) && !el.disabled)
        .slice(0, limit)
        .map(({el, index}) => ({
            index,
            tag: el.tagName.toLowerCase(),
            role: el.getAttribute('role') || '',
            text: clean(el.innerText || el.textContent || ''),
            ariaLabel: clean(el.getAttribute('aria-label') || ''),
            title: clean(el.getAttribute('title') || ''),
            dataDirection: clean(el.getAttribute('data-direction') || ''),
            dataPreset: clean(el.getAttribute('data-preset') || el.getAttribute('data-camera-preset') || ''),
            dataValue: clean(el.getAttribute('data-value') || el.getAttribute('value') || el.getAttribute('name') || ''),
            dataTestId: clean(el.getAttribute('data-testid') || el.getAttribute('data-test-id') || ''),
            icons: collectIcons(el),
        }));
}
"""


_CAMERA_PRESET_MATCH_SCRIPT = r"""
(payload) => {
    const selector = payload.selector || 'button, [role="button"]';
    const targetKeys = new Set(payload.targetKeys || []);
    const iconKeys = new Set(payload.iconKeys || []);
    const normalize = (value) => (value || '')
        .normalize('NFD')
        .replace(/[\u0300-\u036f]/g, '')
        .toLowerCase()
        .replace(/[^\p{L}\p{N}]+/gu, ' ')
        .trim()
        .replace(/\s+/g, ' ');
    const clean = (value) => (value || '').replace(/\s+/g, ' ').trim();
    const isVisible = (el) => {
        const style = window.getComputedStyle(el);
        if (style.visibility === 'hidden' || style.display === 'none') return false;
        const rect = el.getBoundingClientRect();
        return rect.width > 0 && rect.height > 0;
    };
    const actionNoise = /\b(extend|insert|remove|camera|done|show history|favorite|lock|play_circle|add_box|ink_eraser)\b/i;
    const fieldsFor = (el) => ([
        ['text', clean(el.innerText || el.textContent || '')],
        ['aria', clean(el.getAttribute('aria-label') || '')],
        ['data', clean(el.getAttribute('data-direction') || '')],
        ['data', clean(el.getAttribute('data-preset') || '')],
        ['data', clean(el.getAttribute('data-camera-preset') || '')],
        ['data', clean(el.getAttribute('data-value') || '')],
        ['data', clean(el.getAttribute('value') || '')],
        ['data', clean(el.getAttribute('name') || '')],
        ['data', clean(el.getAttribute('data-testid') || '')],
        ['title', clean(el.getAttribute('title') || '')],
    ]).filter(([, value]) => value);
    const iconFieldsFor = (el) => Array.from(
        el.querySelectorAll('i, span, [class*="material"], [data-icon], svg title')
    ).map((node) => clean(
        node.getAttribute('data-icon') ||
        node.getAttribute('aria-label') ||
        node.textContent ||
        node.getAttribute('class') ||
        ''
    )).filter(Boolean);

    for (const [index, el] of Array.from(document.querySelectorAll(selector)).entries()) {
        if (!isVisible(el) || el.disabled) continue;
        for (const [source, value] of fieldsFor(el)) {
            const key = normalize(value);
            if (targetKeys.has(key)) {
                return {index, source, matchedValue: value};
            }
        }
        const text = clean(el.innerText || el.textContent || '');
        if (actionNoise.test(text)) continue;
        for (const value of iconFieldsFor(el)) {
            const key = normalize(value);
            if (iconKeys.has(key)) {
                return {index, source: 'icon', matchedValue: value};
            }
        }
    }
    return null;
}
"""


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
