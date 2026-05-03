"""L1 (text-to-video) batch primitives — submit / wait / download split.

These functions support FLOW_BATCH_DISPATCH=1 batch mode: submit N t2v jobs
back-to-back into one project, parallel-poll completions, sequential download.

The legacy single-job path (`flow.operations.generate.text_to_video`) is left
untouched. This module is parallel infrastructure so the well-tested 1-1-1
flow has zero risk of regression.

Per-submit isolation invariant: each submit captures its own gen_id by
slicing `client._calls[calls_before:]` immediately after submit (never
relies on `client._gen_id` since that attribute is overwritten by the next
submit). Wait + download both consume that captured gen_id and never read
the global "latest" value.
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
from flow.landing import dismiss_flow_marketing_landing
from flow.login import handle_login_redirect, is_login_page
from flow.media_id import looks_like_media_id, normalize_media_id
from flow.model_selector import DEFAULT_MODEL, select_model
from flow.navigation import extract_project_id, flow_url
from flow.recaptcha import (
    RecaptchaError,
    detect_recaptcha,
    detect_recaptcha_in_network,
    first_recaptcha_call,
)
from flow.submit import submit_with_confirmation

logger = logging.getLogger(__name__)


# Reuse the well-tuned primitives from generate.py rather than copy-paste
# their UI selectors. They are private to that module by convention but
# importing them directly keeps the batch path in lock-step with the
# legacy path's selector evolution.
from flow.operations.generate import (  # noqa: E402  (import after stdlib)
    NEW_PROJECT_SELECTORS,
    _count_visible_cards,
    _dismiss_overlays,
    _set_aspect_ratio,
    _set_output_count,
    _type_prompt,
    _wait_for_composer,
)


# ---------------------------------------------------------------------------
# Submit
# ---------------------------------------------------------------------------


async def submit_generate_l1(
    client,
    job: dict,
    *,
    project_already_open: bool,
    model: str = DEFAULT_MODEL,
    free_mode: bool = True,
) -> dict:
    """Submit one L1 text-to-video without waiting for completion.

    First submit (project_already_open=False):
      * navigate Flow homepage, click "New project"
      * land on /project/{id}
      * compose, submit, capture gen_id

    Subsequent submits (project_already_open=True):
      * assume page is already on /project/{id} (set up by previous submit)
      * compose into the same project's composer, submit, capture gen_id

    Returns a per-submit record consumed by `wait_for_l1_gen` and
    `download_l1_gen` later in the batch::

        {
            "gen_id": str,                 # operations/{uuid} captured this submit
            "project_url": str,            # /project/{id} root URL
            "project_id": str,             # bare project uuid
            "calls_before": int,           # len(client._calls) right before submit
            "submit_ts": float,            # time.time() at submit confirmation
            "prompt": str,
        }
    """
    page = client.page
    locale = ""

    if not project_already_open:
        homepage = flow_url(locale)
        await page.goto(homepage, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(2)

        if is_login_page(page.url):
            login_ok = await handle_login_redirect(
                page, timeout=60, profile_name=client.profile_name, client=client,
            )
            if not login_ok:
                msg = await message_with_failure_capture(
                    client, "google_login_required",
                    "Google login required — profile session expired.",
                )
                raise RuntimeError(msg)
            await page.goto(homepage, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(2)

        if "/vi/" in page.url:
            locale = "vi"

        # Marketing-landing fallback (mirrors legacy text_to_video).
        async def _new_btn_attached(timeout_ms: int = 1000) -> bool:
            try:
                await page.wait_for_selector(
                    "text=/New project|Dự án mới|Tạo dự án/",
                    state="attached",
                    timeout=timeout_ms,
                )
                return True
            except Exception:
                return False

        if not await _new_btn_attached(1000):
            await dismiss_flow_marketing_landing(page, logger, _new_btn_attached)
        if not await _new_btn_attached(15000):
            logger.warning("New-project button did not attach within 15s")
        await asyncio.sleep(1)
        await _dismiss_overlays(page)

        clicked = False
        for name in ("New project", "Dự án mới", "Tạo dự án"):
            try:
                btn = page.get_by_role("button", name=name).filter(visible=True).first
                if await btn.is_visible(timeout=2000):
                    await btn.click(timeout=5000)
                    clicked = True
                    break
            except Exception:
                continue
        if not clicked:
            for sel in NEW_PROJECT_SELECTORS:
                try:
                    btn = page.locator(sel).locator("visible=true").first
                    if await btn.is_visible(timeout=1500):
                        await btn.click(timeout=5000)
                        clicked = True
                        break
                except Exception:
                    continue
        if not clicked:
            msg = await message_with_failure_capture(
                client, "new_project_button_not_found",
                "Failed to find '+ New project' button on Flow homepage",
            )
            raise RuntimeError(msg)

        try:
            await page.wait_for_url("**/project/**", timeout=20000)
        except Exception:
            await asyncio.sleep(5)
        await asyncio.sleep(2)
        await _wait_for_composer(page)
    else:
        # Subsequent submit: composer is already mounted on /project/{id}.
        # Wait briefly to let any post-submit UI animation settle (Flow
        # often re-focuses the composer after a generation kicks off).
        await _wait_for_composer(page, timeout_sec=8.0)

    project_url_full = page.url
    project_id = extract_project_id(project_url_full) or ""
    if "/vi/" in project_url_full and not locale:
        locale = "vi"

    # Composer setup: model + aspect + output count + prompt.
    await select_model(
        page, model=model, free_mode=free_mode, profile=client.profile_name,
    )
    aspect = job.get("aspect_ratio") or "16:9"
    try:
        await _set_aspect_ratio(page, aspect)
    except Exception as e:
        logger.warning("aspect_ratio set failed (continuing): %s", e)
    try:
        await _set_output_count(page, 1)
    except Exception as e:
        logger.warning("output_count x1 failed (continuing): %s", e)

    prompt = (job.get("prompt") or "").strip()
    if not prompt:
        raise RuntimeError("L1 t2v requires non-empty prompt")
    await _type_prompt(page, prompt)

    # --- Snapshot before submit, then submit. ---
    # Do NOT call client.clear_captures() here — for batch mode the captures
    # from previous siblings must remain so this submit can window them out
    # by index slice, while the wait_for_l1_gen() call later still sees the
    # pre-submit context (e.g. the project's first operations response).
    calls_before = len(getattr(client, "_calls", []))
    before_cards = await _count_visible_cards(page)
    submit_ts = time.time()

    confirmed = await submit_with_confirmation(
        client,
        before_card_count=before_cards,
        timeout_sec=15.0,
        prompt_text=prompt,
        failure_kind="batch_submit_not_confirmed",
    )
    if not confirmed:
        msg = await message_with_failure_capture(
            client, "batch_submit_not_confirmed",
            "Submit not confirmed — generation may not have started",
        )
        raise RuntimeError(msg)

    # --- Capture THIS submit's gen_id from its own window slice. ---
    gen_id = _capture_gen_id_from_window(client, calls_before)
    if not gen_id:
        # Fall back to legacy single-attribute (still set by submit_with_confirmation
        # via flow.client's _on_response). Better than nothing on first submit.
        gen_id = getattr(client, "_gen_id", None) or ""
    if not gen_id:
        msg = await message_with_failure_capture(
            client, "batch_no_gen_id_captured",
            "Submit confirmed but no gen_id appeared in operations/ network calls",
        )
        raise RuntimeError(msg)

    proj_url = f"{flow_url(locale)}/project/{project_id}" if project_id else project_url_full

    return {
        "gen_id": gen_id,
        "project_url": proj_url,
        "project_id": project_id,
        "locale": locale,
        "calls_before": calls_before,
        "submit_ts": submit_ts,
        "prompt": prompt,
    }


def _capture_gen_id_from_window(client, calls_before: int) -> str:
    """Find the operations/ POST response inside this submit's window.

    Walks `client._calls[calls_before:]` for an entry whose URL contains
    `operations/` and whose body has a `name` field — that name is the
    canonical gen_id Flow uses for this generation.
    """
    calls = getattr(client, "_calls", [])
    for entry in calls[calls_before:]:
        url = entry.get("url", "") or ""
        body = entry.get("body")
        if "operations/" not in url:
            continue
        if not isinstance(body, dict):
            continue
        name = body.get("name") or ""
        if name:
            return str(name)
    return ""


# ---------------------------------------------------------------------------
# Wait
# ---------------------------------------------------------------------------


# Per-submit timeouts mirror flow.wait.TIMEOUTS for text-to-video.
L1_HARD_TIMEOUT_SEC = 900     # 15 min
L1_NO_SIGNAL_SEC = 300        # 5 min idle


async def wait_for_l1_gen(
    client,
    gen_id: str,
    *,
    calls_before: int,
    submit_ts: float,
    parent_media_id: str | None = None,
    hard_timeout: int = L1_HARD_TIMEOUT_SEC,
    no_signal_timeout: int = L1_NO_SIGNAL_SEC,
) -> dict:
    """Poll Flow until the specific `gen_id` reports done or fails.

    Filters `client._calls` by matching `body.name == gen_id` so concurrent
    sibling generations do not falsely satisfy this wait. Reports completion
    when either:

      * an `operations/` response with the matching name has `done: true`
      * an `operations/` response with the matching name has an `error`

    Returns::

        {
            "status": "completed" | "failed",
            "media_id": str | None,
            "media_ids": list[str],   # all mids resolved from this gen's window
            "error": str | None,
        }

    `media_id` resolution scope: only `_media_id_events` recorded after
    `submit_ts` and not equal to `parent_media_id` are considered. The first
    such mid is treated as this generation's output.
    """
    start = time.monotonic()
    last_signal_time = start
    last_progress = 0
    last_recaptcha_check = 0.0

    while True:
        elapsed = time.monotonic() - start
        if elapsed > hard_timeout:
            err = f"timeout after {hard_timeout}s waiting for gen={gen_id[-12:]}"
            await capture_failure_nonblocking(
                client, "timeout",
                extra={"gen_id": gen_id, "elapsed": int(elapsed)},
            )
            return {"status": "failed", "media_id": None, "media_ids": [], "error": err}

        # Network-level reCAPTCHA detection (raises RecaptchaError → caller
        # marks the profile burned and the entire batch fails).
        network_kind = await detect_recaptcha_in_network(client)
        if network_kind:
            call = first_recaptcha_call(client) or {}
            err = RecaptchaError(kind=network_kind, url=str(call.get("url") or ""))
            cap = await capture_failure_nonblocking(client, f"recaptcha_{network_kind}")
            if cap:
                setattr(err, "capture_path", cap)
            raise err

        # Inspect operations/ calls filtered to OUR gen_id.
        api = _scan_api_for_gen(client, gen_id)
        if api["progress"] > last_progress:
            last_progress = api["progress"]
            last_signal_time = time.monotonic()
        if api["done"]:
            # Drain a final beat so any tail media_id events land.
            await asyncio.sleep(2.0)
            mids = _collect_media_ids_after(
                client, since_ts=submit_ts, exclude=parent_media_id,
            )
            primary = mids[0] if mids else None
            return {
                "status": "completed",
                "media_id": primary,
                "media_ids": mids,
                "error": None,
            }
        if api["error"]:
            return {
                "status": "failed",
                "media_id": None,
                "media_ids": [],
                "error": api["error"],
            }

        # Throttled DOM reCAPTCHA probe.
        now = time.monotonic()
        if now - last_recaptcha_check >= 10:
            last_recaptcha_check = now
            if await detect_recaptcha(client.page):
                err = RecaptchaError(kind="v2_visible", url=client.page.url)
                cap = await capture_failure_nonblocking(client, "recaptcha_v2_visible")
                if cap:
                    setattr(err, "capture_path", cap)
                raise err

        # Idle watchdog.
        silence = time.monotonic() - last_signal_time
        if silence > no_signal_timeout:
            err = f"no_signal_timeout after {int(silence)}s for gen={gen_id[-12:]}"
            await capture_failure_nonblocking(
                client, "no_signal_timeout",
                extra={"gen_id": gen_id, "silent_sec": int(silence)},
            )
            return {"status": "failed", "media_id": None, "media_ids": [], "error": err}

        await asyncio.sleep(0.6)


def _scan_api_for_gen(client, gen_id: str) -> dict:
    """Filter operations/ responses to our gen_id and report status."""
    out = {"progress": 0, "done": False, "error": None}
    calls = getattr(client, "_calls", [])
    # Walk newest first. Don't cap (batch waits can have hundreds of
    # ops responses; fixed cap would miss our terminal entry).
    for call in reversed(calls):
        url = call.get("url", "") or ""
        body = call.get("body")
        status = call.get("status", 0)
        if "operations/" not in url or not isinstance(body, dict):
            continue
        name = body.get("name") or ""
        if name != gen_id:
            continue
        # 4xx on our gen_id specifically is an error.
        if status in (403, 429):
            out["error"] = f"blocked_{status}"
            return out
        if body.get("done"):
            out["done"] = True
            return out
        err = body.get("error")
        if err:
            out["error"] = str(err)
            return out
        progress = body.get("progressPercentage", 0) or 0
        if progress > out["progress"]:
            out["progress"] = progress
    return out


def _collect_media_ids_after(
    client,
    *,
    since_ts: float,
    exclude: str | None = None,
) -> list[str]:
    """Return media_ids recorded after `since_ts`, in capture order.

    Excludes `exclude` (typically the parent_media_id, so we never
    misreport the source clip as the new output) and de-duplicates while
    preserving order.
    """
    excl = normalize_media_id(exclude) if exclude else None
    seen: set[str] = set()
    out: list[str] = []
    for evt in getattr(client, "_media_id_events", []):
        ts = evt.get("ts", 0) or 0
        if ts < since_ts:
            continue
        mid = normalize_media_id(evt.get("mid") or evt.get("media_id") or "")
        if not mid or not looks_like_media_id(mid):
            continue
        if excl and mid == excl:
            continue
        if mid in seen:
            continue
        seen.add(mid)
        out.append(mid)
    return out


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------


async def download_l1_gen(
    client,
    media_id: str,
    *,
    prefix: str = "t2v",
    quality: str = "1080p",
) -> list[str]:
    """Download the 1080p mp4 for `media_id`, scoped to that mid only.

    Wraps `flow.download.download_video` with an explicit `media_ids=[mid]`
    so the network filter does not pull a sibling generation's video by
    accident.
    """
    if not media_id:
        raise RuntimeError("download_l1_gen called without media_id")
    files = await download_video(
        client,
        media_ids=[media_id],
        prefix=prefix,
        quality=quality,
        media_kind="video",
    )
    if not files:
        raise RuntimeError(f"download_l1_gen({media_id[:12]}): no output file")
    return files


# ---------------------------------------------------------------------------
# Result builder
# ---------------------------------------------------------------------------


def build_l1_result(
    *,
    submit: dict,
    wait: dict,
    output_files: list[str] | None,
    profile: str,
    error: str | None = None,
) -> dict[str, Any]:
    """Compose a job-update payload from per-phase outputs."""
    if error:
        return {
            "status": "failed",
            "error": error,
            "project_url": submit.get("project_url", ""),
            "generation_id": submit.get("gen_id"),
            "profile": profile,
        }

    media_id = wait.get("media_id")
    project_id = submit.get("project_id") or ""
    locale = submit.get("locale") or ""
    edit_url = (
        f"{flow_url(locale)}/project/{project_id}/edit/{media_id}"
        if media_id and project_id else ""
    )
    return {
        "status": "completed",
        "project_url": submit.get("project_url", ""),
        "media_id": media_id,
        "edit_url": edit_url,
        "output_files": output_files or [],
        "generation_id": submit.get("gen_id"),
        "profile": profile,
    }
