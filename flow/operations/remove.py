"""Remove Object — Level 2 operation.

Navigates to edit URL, clicks Remove, draws bbox (REQUIRED),
submits, waits, downloads. No prompt needed.
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
    draw_bbox_on_video,
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
    from flow.operations.remove_api import (
        install_remove_request_capture,
        get_remove_request_template,
        replay_remove_via_api,
    )
except Exception as exc:  # pragma: no cover - guarded fallback
    install_remove_request_capture = None
    get_remove_request_template = None
    replay_remove_via_api = None
    _REMOVE_API_IMPORT_ERROR = exc
else:
    _REMOVE_API_IMPORT_ERROR = None

logger = logging.getLogger(__name__)

# Remove button texts (EN + VI)
REMOVE_BUTTONS = ["Remove", "Xoá"]

# Remove icon fallback
REMOVE_ICON_SELECTOR = "button:has(span:has-text('ink_eraser'))"


def _reverse_remove_enabled() -> bool:
    return l2_reverse_api_enabled("FLOW_REMOVE_VIA_REVERSE")


def _install_remove_capture_if_enabled(client) -> bool:
    if not _reverse_remove_enabled():
        return False
    if install_remove_request_capture is None:
        logger.info(
            "FLOW_REMOVE_VIA_REVERSE=1 but remove_api unavailable; "
            "continuing UI path (%s)",
            _REMOVE_API_IMPORT_ERROR,
        )
        return False
    try:
        install_remove_request_capture(client)
    except Exception as exc:
        logger.info(
            "Remove request capture install failed; continuing UI path: %s",
            exc,
        )
        return False
    return True


def _current_remove_template(client):
    if get_remove_request_template is None:
        return None
    try:
        return get_remove_request_template(client)
    except Exception as exc:
        logger.info(
            "Remove request template unavailable; continuing UI path: %s",
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


def _record_remove_replay_media_id(client, media_id: str) -> None:
    recorder = getattr(client, "_record_media_id", None)
    if callable(recorder):
        recorder(media_id, source="remove_replay", url="remove-replay")
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
                "source": "remove_replay",
                "url": "remove-replay",
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


async def _finalize_remove_replay_result(
    client,
    job: dict,
    *,
    project_id: str,
    locale: str,
    replay_media_id: str,
    download_prefix: str = "rm",
) -> dict:
    _record_remove_replay_media_id(client, replay_media_id)

    logger.info(
        "Remove replay finalize: polling status API for media_id=%s",
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
            f"remove-object replay: status API returned no slot for "
            f"media_id={replay_media_id}"
        )
        message = await message_with_failure_capture(
            client,
            "remove_replay_status_no_entry",
            message,
        )
        raise RuntimeError(message)

    status = slot.get("status")
    if status == "failed":
        error = slot.get("error") or "unknown"
        message = (
            f"remove-object replay: status API reports failed for "
            f"media_id={replay_media_id}: {error}"
        )
        message = await message_with_failure_capture(
            client,
            "remove_replay_status_failed",
            message,
        )
        raise RuntimeError(message)

    if status != "completed":
        message = (
            f"remove-object replay: status API did not reach completed "
            f"(status={status}) for media_id={replay_media_id}"
        )
        message = await message_with_failure_capture(
            client,
            "remove_replay_status_timeout",
            message,
        )
        raise RuntimeError(message)

    media_url = slot.get("media_url")
    if not isinstance(media_url, str) or not media_url:
        message = (
            f"remove-object replay: status completed but no media URL "
            f"available for media_id={replay_media_id}"
        )
        message = await message_with_failure_capture(
            client,
            "remove_replay_no_media_url",
            message,
        )
        raise RuntimeError(message)

    download_dir = _replay_download_dir(client)
    try:
        download_dir.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        logger.debug(
            "Remove replay finalize: download dir mkdir failed (%s) for %s; "
            "continuing -- download_via_url will surface the real error",
            exc,
            download_dir,
        )
    out_path = download_dir / (
        f"{download_prefix}_replay_{replay_media_id[:20]}_{int(time.time())}.mp4"
    )

    logger.info(
        "Remove replay finalize: downloading via direct URL "
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
            f"remove-object replay: direct-URL download returned empty path "
            f"for media_id={replay_media_id}"
        )
        message = await message_with_failure_capture(
            client,
            "remove_replay_download_failed",
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


async def remove_object(
    client,
    job: dict,
    bbox: dict | None = None,
) -> dict:
    """Execute remove-object operation.

    Steps:
    1. Navigate to edit URL
    2. Wait for video to load
    3. Click "Remove" button
    4. Draw bbox on video canvas (REQUIRED — selects what to remove)
    5. Submit and confirm (no prompt needed)
    6. Wait + Download + Return metadata

    Args:
        client: FlowClient instance
        job: Job dict with edit_url/project_url/media_id
        bbox: {x, y, w, h} normalized 0-1 — REQUIRED for remove

    Returns: Result dict
    """
    page = client.page
    bbox = bbox or job.get("bbox") or {}
    reverse_enabled = _reverse_remove_enabled()
    if reverse_enabled:
        _install_remove_capture_if_enabled(client)

    # Step 1: Navigate (skip toolbar check for revapi — DOM buttons not needed)
    edit_url_val, project_id, locale = await navigate_to_edit(
        client, job, skip_toolbar_check=reverse_enabled
    )

    # Step 2: Wait for video
    await wait_for_video_loaded(page)

    template = _current_remove_template(client) if reverse_enabled else None
    if not reverse_enabled:
        reverse_unavailable_reason = "operation reverse API disabled"
    elif replay_remove_via_api is None:
        reverse_unavailable_reason = f"remove_api unavailable: {_REMOVE_API_IMPORT_ERROR}"
    elif template is None:
        reverse_unavailable_reason = "captured remove template unavailable"
    elif not l2_reverse_api_template_has_auth(template):
        reverse_unavailable_reason = "captured remove template missing authorization header"
    else:
        reverse_unavailable_reason = ""
    reverse_outcome = await run_l2_reverse_api_first(
        operation="remove-object",
        log=logger,
        available=(
            reverse_enabled
            and template is not None
            and replay_remove_via_api is not None
            and l2_reverse_api_template_has_auth(template)
        ),
        unavailable_reason=reverse_unavailable_reason,
        metadata={
            "project_id": project_id,
            "parent_media_id": job.get("media_id"),
            "template_url": template.get("url") if isinstance(template, dict) else None,
        },
        timeout_sec=660.0,
        call=lambda: _run_remove_reverse_api(
            client,
            job,
            bbox=bbox,
            project_id=project_id,
            locale=locale,
        ),
    )
    if reverse_outcome.succeeded:
        return reverse_outcome.result
    if reverse_outcome.status == "recoverable_error":
        logger.warning(
            "Remove reverse-API replay failed; falling back to UI path: %s",
            reverse_outcome.error,
        )

    # Step 3: Click Remove button
    clicked = await click_action_button(page, REMOVE_BUTTONS, client=client)
    if not clicked:
        try:
            icon_btn = page.locator(REMOVE_ICON_SELECTOR).first
            if await icon_btn.is_visible(timeout=2000):
                await icon_btn.click(timeout=3000)
                clicked = True
                logger.info("Clicked Remove via icon fallback")
                await asyncio.sleep(0.5)
        except Exception:
            pass

    if not clicked:
        message = "Failed to find Remove button"
        message = await message_with_failure_capture(
            client,
            "remove_button_not_found",
            message,
        )
        raise RuntimeError(message)

    # Step 4: Draw bbox (REQUIRED for remove)
    if not bbox:
        # Default: center region if no bbox provided
        bbox = {"x": 0.25, "y": 0.25, "w": 0.5, "h": 0.5}
        logger.warning("No bbox provided for remove; using center default")

    drew = await draw_bbox_on_video(page, bbox)
    if not drew:
        logger.warning(
            "Bbox drawing failed or unverified; Flow may fall back to default region"
        )

    # Step 5: Submit (no prompt for remove)
    before_cards = await count_visible_cards(page)
    client.clear_captures()

    confirmed = await submit_with_confirmation(
        client,
        before_card_count=before_cards,
        failure_kind="remove_submit_not_confirmed",
    )
    if not confirmed:
        message = "Remove submit not confirmed"
        message = await message_with_failure_capture(
            client,
            "remove_submit_not_confirmed",
            message,
        )
        raise RuntimeError(message)

    # Step 6: Wait + Download + Return
    return await finalize_operation(
        client, job,
        job_type="remove-object",
        project_id=project_id,
        locale=locale,
        download_prefix="rm",
    )


async def _run_remove_reverse_api(
    client,
    job: dict,
    *,
    bbox: dict,
    project_id: str,
    locale: str,
) -> dict:
    if replay_remove_via_api is None:
        raise RuntimeError("remove_api unavailable")
    client.clear_captures()
    replay_result = await replay_remove_via_api(
        client,
        parent_media_id=job["media_id"],
        bbox=bbox,
    )
    replay_media_ids = _extract_replay_media_ids(replay_result)
    if not replay_media_ids:
        raise RuntimeError("Remove reverse-API replay returned no media_id")
    replay_media_id = replay_media_ids[0]
    replay_count = getattr(client, "_remove_replay_count", 0) + 1
    setattr(client, "_remove_replay_count", replay_count)
    logger.info(
        "Remove replay submit accepted via reverse API "
        "(count=%d media_ids=%s) -- finalizing via status API + direct URL download",
        replay_count,
        replay_media_ids,
    )
    return await finalize_l2_reverse_api_after_accept(
        client,
        operation="remove-object",
        media_id=replay_media_id,
        finalize_call=lambda: _finalize_remove_replay_result(
            client,
            job,
            project_id=project_id,
            locale=locale,
            replay_media_id=replay_media_id,
        ),
    )
