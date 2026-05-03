"""L2 (extend / camera / insert / remove) batch primitives.

Phase 2 of FLOW_BATCH_DISPATCH: with one Chrome lifetime parked on a
parent L1's ``/edit/{parent_media_id}`` page, submit N L2 ops back-to-back,
parallel-poll completions, sequential download.

Per-submit isolation invariant (mirrors `_l1_batch`): every submit captures
its own gen_id from a window slice of `client._calls` /
`client._batch_responses`, never reads `client._gen_id` after the fact.

The mode panel state is unique to L2: each op (Extend/Camera/Insert/Remove)
mutates the composer differently. We do NOT trust the previously-open
panel. Each ``submit_X`` clicks its own mode button (no-op when the panel
is already on that mode), draws its bbox / picks its preset / types its
prompt, and submits. Between successive submits the prior op's progress
card lands in the project's history and Flow's composer typically defaults
back to Extend. We let that natural reset happen rather than try to wrest
the UI back to a known state — the mode click is idempotent enough.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from flow.download import download_video
from flow.failure_capture import (
    capture_failure_nonblocking,
    message_with_failure_capture,
)
from flow.media_id import looks_like_media_id, normalize_media_id
from flow.model_selector import DEFAULT_MODEL, select_model
from flow.navigation import flow_url
from flow.recaptcha import (
    RecaptchaError,
    detect_recaptcha,
    detect_recaptcha_in_network,
    first_recaptcha_call,
)
from flow.submit import submit_with_confirmation

# Reuse Phase 1's side-channel listener + window helpers — they are
# op-agnostic and Flow's submit endpoint is the same shape across L1 / L2.
from flow.operations._l1_batch import (
    _await_gen_id_in_window,
    _collect_media_ids_after,
    install_batch_response_capture,
)

# Reuse legacy ops' private UI helpers so the batch path stays in lock-step
# with the well-tuned 1-1-1 selectors. They are private by convention only.
from flow.operations._base import (
    click_action_button,
    count_visible_cards,
    draw_bbox_on_video,
    navigate_to_edit,
    wait_for_video_loaded,
)
from flow.operations.camera import (
    CAMERA_BUTTONS,
    CAMERA_ICON_SELECTOR,
    CAMERA_POSITION_PRESETS,
    _click_preset,
)
from flow.operations.extend import (
    EXTEND_BUTTONS,
    EXTEND_ICON_SELECTORS,
    _type_extend_prompt,
    _verify_extend_panel,
)
from flow.operations.insert import (
    INSERT_BUTTONS,
    INSERT_ICON_SELECTOR,
    _type_insert_prompt,
)
from flow.operations.remove import (
    REMOVE_BUTTONS,
    REMOVE_ICON_SELECTOR,
)

logger = logging.getLogger(__name__)


# Same wait budgets as L1 batch — Flow's Veo extend / camera / insert
# generations all sit under the 15-min hard cap with similar idle profiles.
L2_HARD_TIMEOUT_SEC = 900
L2_NO_SIGNAL_SEC = 300


# ---------------------------------------------------------------------------
# Submit
# ---------------------------------------------------------------------------


async def _ensure_on_edit(client, parent_job: dict) -> tuple[str, str]:
    """Navigate to parent's /edit/ once per batch.

    Returns (project_id, locale) extracted post-nav. Subsequent submits in
    the same batch reuse the page state — they only re-open the mode panel.
    """
    install_batch_response_capture(client)
    page = client.page
    if "/edit/" in page.url:
        # Already there from a previous submit in this batch.
        from flow.navigation import detect_locale, extract_project_id
        return extract_project_id(page.url) or "", detect_locale(page.url)
    _, project_id, locale = await navigate_to_edit(client, parent_job)
    await wait_for_video_loaded(page)
    return project_id, locale


async def _click_extend_mode(client) -> None:
    """Open the Extend panel; idempotent when already open."""
    page = client.page
    await asyncio.sleep(1)
    if await _verify_extend_panel(page):
        return
    clicked = await click_action_button(page, EXTEND_BUTTONS, client=client)
    if not clicked:
        for sel in EXTEND_ICON_SELECTORS:
            try:
                btn = page.locator(sel).first
                if await btn.is_visible(timeout=2000):
                    await btn.click(timeout=3000)
                    clicked = True
                    break
            except Exception:
                continue
    if not clicked:
        msg = await message_with_failure_capture(
            client, "extend_button_not_found",
            "Failed to find Extend button in batch mode",
        )
        raise RuntimeError(msg)
    await asyncio.sleep(1)
    if not await _verify_extend_panel(page):
        msg = await message_with_failure_capture(
            client, "extend_panel_not_open",
            "Extend panel did not open after click in batch mode",
        )
        raise RuntimeError(msg)


async def _click_camera_mode(client, direction: str) -> None:
    page = client.page
    clicked = await click_action_button(page, CAMERA_BUTTONS, client=client)
    if not clicked:
        try:
            icon = page.locator(CAMERA_ICON_SELECTOR).first
            if await icon.is_visible(timeout=2000):
                await icon.click(timeout=3000)
                clicked = True
        except Exception:
            pass
    if not clicked:
        msg = await message_with_failure_capture(
            client, "camera_button_not_found",
            "Failed to find Camera button in batch mode",
        )
        raise RuntimeError(msg)
    await asyncio.sleep(1)
    is_position = direction in CAMERA_POSITION_PRESETS
    tab_name = "Camera position" if is_position else "Camera motion"
    try:
        tab = page.locator(f"[role='tab']:has-text('{tab_name}')").first
        if await tab.is_visible(timeout=2000):
            await tab.click(timeout=3000)
            await asyncio.sleep(0.5)
    except Exception:
        pass
    ok = await _click_preset(page, direction)
    if not ok:
        msg = await message_with_failure_capture(
            client, "camera_preset_not_found",
            f"Failed to find camera preset: {direction}",
        )
        raise RuntimeError(msg)


async def _click_insert_mode(client) -> None:
    page = client.page
    clicked = await click_action_button(page, INSERT_BUTTONS, client=client)
    if not clicked:
        try:
            icon = page.locator(INSERT_ICON_SELECTOR).first
            if await icon.is_visible(timeout=2000):
                await icon.click(timeout=3000)
                clicked = True
        except Exception:
            pass
    if not clicked:
        msg = await message_with_failure_capture(
            client, "insert_button_not_found",
            "Failed to find Insert button in batch mode",
        )
        raise RuntimeError(msg)
    await asyncio.sleep(0.5)


async def _click_remove_mode(client) -> None:
    page = client.page
    clicked = await click_action_button(page, REMOVE_BUTTONS, client=client)
    if not clicked:
        try:
            icon = page.locator(REMOVE_ICON_SELECTOR).first
            if await icon.is_visible(timeout=2000):
                await icon.click(timeout=3000)
                clicked = True
        except Exception:
            pass
    if not clicked:
        msg = await message_with_failure_capture(
            client, "remove_button_not_found",
            "Failed to find Remove button in batch mode",
        )
        raise RuntimeError(msg)
    await asyncio.sleep(0.5)


async def _confirm_and_capture(
    client,
    *,
    failure_kind: str,
    prompt_text: str = "",
) -> dict[str, Any]:
    """Common submit-confirm + per-submit gen_id capture."""
    page = client.page
    before_cards = await count_visible_cards(page)
    # Per-submit clocks. Do NOT clear captures (batch invariant).
    calls_before = len(getattr(client, "_calls", []))
    batch_resp_before = len(getattr(client, "_batch_responses", []) or [])
    submit_ts = time.time()

    confirmed = await submit_with_confirmation(
        client,
        before_card_count=before_cards,
        prompt_text=prompt_text,
        failure_kind=failure_kind,
    )
    if not confirmed:
        msg = await message_with_failure_capture(
            client, failure_kind,
            f"{failure_kind}: generation did not start",
        )
        raise RuntimeError(msg)

    gen_id = await _await_gen_id_in_window(
        client, calls_before,
        batch_resp_before=batch_resp_before,
        timeout_sec=15.0,
    )
    if not gen_id:
        msg = await message_with_failure_capture(
            client, "batch_no_gen_id_captured",
            "Submit confirmed but no gen_id appeared in 15s",
        )
        raise RuntimeError(msg)

    return {
        "gen_id": gen_id,
        "calls_before": calls_before,
        "batch_resp_before": batch_resp_before,
        "submit_ts": submit_ts,
    }


async def submit_extend(
    client,
    parent_job: dict,
    prompt: str = "",
    *,
    panel_already_open: bool = False,
    model: str = DEFAULT_MODEL,
    free_mode: bool = True,
) -> dict[str, Any]:
    """Submit one extend op without waiting. Caller drives navigation once
    via :func:`_ensure_on_edit`; pass ``panel_already_open=True`` for the
    first submit if the page just landed on /edit/ with the Extend panel
    already auto-active."""
    await _ensure_on_edit(client, parent_job)
    if not panel_already_open:
        await _click_extend_mode(client)
    if prompt:
        await _type_extend_prompt(client.page, prompt)
    # Model selector lives inside extend panel — pick LP / free.
    await select_model(
        client.page, model=model, free_mode=free_mode,
        profile=client.profile_name,
    )
    sub = await _confirm_and_capture(
        client, failure_kind="extend_submit_not_confirmed",
        prompt_text=prompt,
    )
    sub["op_type"] = "extend-video"
    return sub


async def submit_camera(
    client,
    parent_job: dict,
    direction: str,
    *,
    panel_already_open: bool = False,
) -> dict[str, Any]:
    """Submit one camera-move op."""
    await _ensure_on_edit(client, parent_job)
    if not panel_already_open:
        await _click_camera_mode(client, direction)
    sub = await _confirm_and_capture(
        client, failure_kind="camera_submit_not_confirmed",
    )
    sub["op_type"] = "camera-move"
    sub["direction"] = direction
    return sub


async def submit_insert(
    client,
    parent_job: dict,
    prompt: str = "",
    bbox: dict | None = None,
    *,
    panel_already_open: bool = False,
) -> dict[str, Any]:
    """Submit one insert-object op."""
    await _ensure_on_edit(client, parent_job)
    if not panel_already_open:
        await _click_insert_mode(client)
    if bbox:
        drew = await draw_bbox_on_video(client.page, bbox)
        if not drew:
            logger.warning("Insert bbox drawing failed — Flow will use default region")
    if prompt:
        await _type_insert_prompt(client.page, prompt)
    sub = await _confirm_and_capture(
        client, failure_kind="insert_submit_not_confirmed",
        prompt_text=prompt,
    )
    sub["op_type"] = "insert-object"
    return sub


async def submit_remove(
    client,
    parent_job: dict,
    bbox: dict | None = None,
    *,
    panel_already_open: bool = False,
) -> dict[str, Any]:
    """Submit one remove-object op. bbox required (defaults to centre when
    omitted, matching legacy `remove_object`)."""
    await _ensure_on_edit(client, parent_job)
    if not panel_already_open:
        await _click_remove_mode(client)
    if not bbox:
        bbox = {"x": 0.25, "y": 0.25, "w": 0.5, "h": 0.5}
        logger.warning("No bbox for remove (batch) — using centre default")
    drew = await draw_bbox_on_video(client.page, bbox)
    if not drew:
        logger.warning("Remove bbox drawing failed — Flow will use default region")
    sub = await _confirm_and_capture(
        client, failure_kind="remove_submit_not_confirmed",
    )
    sub["op_type"] = "remove-object"
    return sub


# ---------------------------------------------------------------------------
# Wait
# ---------------------------------------------------------------------------


async def wait_for_all_l2_gens(
    client,
    submits: list[dict],
    *,
    parent_media_id: str | None = None,
    hard_timeout: int = L2_HARD_TIMEOUT_SEC,
    no_signal_timeout: int = L2_NO_SIGNAL_SEC,
) -> list[dict]:
    """Collective wait for N L2 gens.

    Mirrors :func:`flow.operations._l1_batch.wait_for_all_l1_gens`. Flow's
    modern submit endpoint emits per-op responses we cannot filter by
    gen_id, so we wait for N distinct media_ids (excluding the parent)
    post-earliest-submit and assign them in submission order.
    """
    n = len(submits)
    if n == 0:
        return []
    earliest_submit_ts = min(s["submit_ts"] for s in submits)

    start = time.monotonic()
    last_count = 0
    last_signal_time = start
    last_recaptcha_check = 0.0

    while True:
        elapsed = time.monotonic() - start
        if elapsed > hard_timeout:
            err = f"timeout after {hard_timeout}s waiting for {n} L2 gens"
            await capture_failure_nonblocking(
                client, "timeout",
                extra={"want": n, "got": last_count, "elapsed": int(elapsed)},
            )
            return [
                {"status": "failed", "media_id": None, "media_ids": [], "error": err}
                for _ in submits
            ]

        network_kind = await detect_recaptcha_in_network(client)
        if network_kind:
            call = first_recaptcha_call(client) or {}
            err = RecaptchaError(kind=network_kind, url=str(call.get("url") or ""))
            cap = await capture_failure_nonblocking(client, f"recaptcha_{network_kind}")
            if cap:
                setattr(err, "capture_path", cap)
            raise err

        mids = _collect_media_ids_after(
            client, since_ts=earliest_submit_ts, exclude=parent_media_id,
        )
        if len(mids) > last_count:
            last_count = len(mids)
            last_signal_time = time.monotonic()
        if len(mids) >= n:
            return [
                {
                    "status": "completed",
                    "media_id": mids[i],
                    "media_ids": [mids[i]],
                    "error": None,
                }
                for i in range(n)
            ]

        now = time.monotonic()
        if now - last_recaptcha_check >= 10:
            last_recaptcha_check = now
            if await detect_recaptcha(client.page):
                err = RecaptchaError(kind="v2_visible", url=client.page.url)
                cap = await capture_failure_nonblocking(client, "recaptcha_v2_visible")
                if cap:
                    setattr(err, "capture_path", cap)
                raise err

        silence = time.monotonic() - last_signal_time
        if silence > no_signal_timeout:
            err = (
                f"no_signal_timeout after {int(silence)}s "
                f"waiting for {n - last_count} more L2 gens"
            )
            await capture_failure_nonblocking(
                client, "no_signal_timeout",
                extra={"want": n, "got": last_count, "silent_sec": int(silence)},
            )
            return [
                (
                    {
                        "status": "completed",
                        "media_id": mids[i],
                        "media_ids": [mids[i]],
                        "error": None,
                    }
                    if i < last_count
                    else {
                        "status": "failed",
                        "media_id": None,
                        "media_ids": [],
                        "error": err,
                    }
                )
                for i in range(n)
            ]

        await asyncio.sleep(0.6)


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------


# Per-op download prefix (matches legacy finalize_operation defaults).
_PREFIX_BY_TYPE = {
    "extend-video": "ext",
    "camera-move": "cam",
    "insert-object": "ins",
    "remove-object": "rm",
}


async def download_l2_gen_at_tile(
    client,
    *,
    tile_index: int,
    media_id: str,
    edit_url: str,
    prefix: str = "l2",
    quality: str = "1080p",
) -> list[str]:
    """Download the i-th UNIQUE history-tile for an L2 batch.

    Mirrors :func:`flow.operations._l1_batch.download_l1_gen_at_tile` but
    targets the history rail visible inside ``/edit/`` (not the project
    grid). Each batched L2 op appends a new tile; when we land on the
    parent's edit_url the rail orders them newest-first too.
    """
    if not media_id:
        raise RuntimeError("download_l2_gen_at_tile called without media_id")
    page = client.page

    # Make sure we're sitting on /edit/ — the rail is always present there.
    if "/edit/" not in page.url:
        if edit_url:
            try:
                await page.goto(edit_url, wait_until="domcontentloaded", timeout=20000)
                await asyncio.sleep(2)
            except Exception as exc:
                logger.warning("download_l2_gen_at_tile: goto edit failed: %s", exc)

    try:
        unique_tile_ids = await page.evaluate(
            """() => {
                const seen = new Set(); const out = [];
                for (const el of document.querySelectorAll('[data-tile-id^="fe_id_"]')) {
                    const tid = el.getAttribute('data-tile-id');
                    if (!tid || seen.has(tid)) continue;
                    seen.add(tid);
                    const r = el.getBoundingClientRect();
                    if (r.width < 50 || r.height < 50) continue;
                    out.push(tid);
                }
                return out;
            }"""
        )
        logger.info(
            "download_l2_gen_at_tile: %d unique tiles, target idx=%d",
            len(unique_tile_ids), tile_index,
        )
        if tile_index >= len(unique_tile_ids):
            raise RuntimeError(
                f"download_l2_gen_at_tile: tile_index={tile_index} but only "
                f"{len(unique_tile_ids)} unique tiles rendered"
            )
        target_tile_id = unique_tile_ids[tile_index]
        target = page.locator(f"[data-tile-id='{target_tile_id}']").first
        await target.wait_for(state="attached", timeout=8000)
        await target.scroll_into_view_if_needed(timeout=2000)
        try:
            await target.click(timeout=5000)
        except Exception:
            await page.evaluate(
                """(tid) => {
                    const el = document.querySelector(`[data-tile-id="${tid}"]`);
                    if (!el) return false;
                    const r = el.getBoundingClientRect();
                    const cx = r.x + r.width/2, cy = r.y + r.height/2;
                    for (const t of ['pointerdown','mousedown','pointerup','mouseup','click']) {
                        el.dispatchEvent(new MouseEvent(t, {
                            bubbles: true, cancelable: true, view: window,
                            clientX: cx, clientY: cy, button: 0,
                        }));
                    }
                    return true;
                }""",
                target_tile_id,
            )
        deadline = time.time() + 10
        while time.time() < deadline:
            if "/edit/" in page.url:
                break
            await asyncio.sleep(0.2)
        await asyncio.sleep(1.5)
    except Exception as exc:
        raise RuntimeError(
            f"download_l2_gen_at_tile: tile click failed (index={tile_index}): {exc}"
        ) from exc

    files = await download_video(
        client,
        media_ids=[media_id],
        prefix=prefix,
        quality=quality,
        media_kind="video",
    )
    if not files:
        raise RuntimeError(
            f"download_l2_gen_at_tile(idx={tile_index}, mid={media_id[:12]}): "
            f"no output file"
        )
    return files


# ---------------------------------------------------------------------------
# Result builder
# ---------------------------------------------------------------------------


def build_l2_result(
    *,
    job: dict,
    submit: dict,
    wait: dict,
    output_files: list[str] | None,
    profile: str,
    project_url: str,
    project_id: str,
    locale: str,
    error: str | None = None,
) -> dict[str, Any]:
    """Compose a worker job-update payload for one L2 result.

    Mirrors L1 `build_l1_result` shape but threads `parent_job_id` through
    so DB updates preserve chain lineage.
    """
    parent_job_id = job.get("parent_job_id") or ""
    if error:
        return {
            "status": "failed",
            "error": error,
            "project_url": project_url,
            "generation_id": submit.get("gen_id"),
            "profile": profile,
            "parent_job_id": parent_job_id,
        }
    media_id = wait.get("media_id")
    edit_url_val = (
        f"{flow_url(locale)}/project/{project_id}/edit/{media_id}"
        if media_id and project_id else ""
    )
    return {
        "status": "completed",
        "project_url": project_url,
        "media_id": media_id,
        "edit_url": edit_url_val,
        "output_files": output_files or [],
        "generation_id": submit.get("gen_id"),
        "profile": profile,
        "parent_job_id": parent_job_id,
    }


# Re-export sibling-relevant constants so the orchestrator can dispatch
# by op_type without re-importing per-module.
SUBMIT_BY_TYPE = {
    "extend-video": "submit_extend",
    "camera-move": "submit_camera",
    "insert-object": "submit_insert",
    "remove-object": "submit_remove",
}


__all__ = [
    "L2_HARD_TIMEOUT_SEC",
    "L2_NO_SIGNAL_SEC",
    "build_l2_result",
    "download_l2_gen_at_tile",
    "submit_camera",
    "submit_extend",
    "submit_insert",
    "submit_remove",
    "wait_for_all_l2_gens",
]
