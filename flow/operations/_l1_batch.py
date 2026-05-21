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
from flow.operations._l1_status_poll import poll_status_via_api
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

    # Side-channel response listener — the canonical capture path because
    # Flow's modern submit endpoint isn't covered by `flow.client._on_response`.
    install_batch_response_capture(client)

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
        # Marketing-landing CTAs sometimes bounce to OAuth signin/identifier
        # (cookies insufficient for Flow's client_id even when Gmail is
        # logged in — the freshly-warmed-profile case). Drive the login
        # flow once, then re-goto the homepage and re-attempt.
        if not await _new_btn_attached(2000) and is_login_page(page.url):
            logger.warning("Marketing CTA bounced to signin — driving login")
            try:
                await handle_login_redirect(
                    page, timeout=90, profile_name=client.profile_name,
                    client=client,
                )
            except Exception as exc:
                logger.warning("login redirect drive failed: %s", exc)
            await page.goto(homepage, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(3)
            if not await _new_btn_attached(2000):
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
    # by index slice, while the wait_for_all_l1_gens() call later still sees
    # the pre-submit context (e.g. the project's first operations response).
    #
    # We snapshot two clocks:
    #   * len(_calls) — the legacy buffer from flow.client._on_response
    #   * len(_batch_responses) — our side-channel listener buffer.
    # `_batch_responses` is the authoritative clock for batch isolation
    # because flow.client doesn't fetch the body of v1/video:batchAsync...
    # responses, so its length doesn't always advance between submits.
    calls_before = len(getattr(client, "_calls", []))
    batch_resp_before = len(getattr(client, "_batch_responses", []) or [])
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

    # --- Capture THIS submit's backend handles from its own window slice. ---
    # `submit_with_confirmation` may return on a UI signal (cards count
    # increased) BEFORE the operations/ POST response lands in
    # `client._calls`. Poll briefly so the per-submit window contains the
    # network event before we move on.
    # 30s — inflate-batch rewrites the body to N requests, and Flow's
    # response time scales with N. Round 11 PASS measured 15s for N=3;
    # leave headroom for N≤5 or slow-day fluctuations.
    submit_meta = await _await_submit_metadata_in_window(
        client, calls_before,
        batch_resp_before=batch_resp_before,
        timeout_sec=30.0,
    )
    gen_id = submit_meta.get("gen_id", "")
    if not gen_id:
        # Look at the side-channel responses for a 403/429 on the
        # submit URL. Flow returns those when reCAPTCHA throttles or
        # blocks; we want burn_recovery (catches RecaptchaError) to
        # wipe+rewarm and retry rather than failing as a generic
        # RuntimeError.
        block_status = _detect_block_in_window(client, batch_resp_before)
        if block_status:
            cap = await capture_failure_nonblocking(
                client, f"recaptcha_blocked_{block_status}",
            )
            err = RecaptchaError(
                kind=f"blocked_{block_status}",
                url="v1/video:batchAsyncGenerateVideoText",
            )
            if cap:
                setattr(err, "capture_path", cap)
            raise err
        msg = await message_with_failure_capture(
            client, "batch_no_gen_id_captured",
            "Submit confirmed but no generation handle appeared in batch "
            "submit network calls "
            "within 30s",
        )
        raise RuntimeError(msg)

    response_project_id = submit_meta.get("project_id", "")
    if not project_id and response_project_id:
        project_id = response_project_id

    proj_url = f"{flow_url(locale)}/project/{project_id}" if project_id else project_url_full

    return {
        "gen_id": gen_id,
        "workflow_id": submit_meta.get("workflow_id", ""),
        "media_id": submit_meta.get("media_id", ""),
        "batch_id": submit_meta.get("batch_id", ""),
        "workflow_step_id": submit_meta.get("workflow_step_id", ""),
        "project_url": proj_url,
        "project_id": project_id,
        "locale": locale,
        "calls_before": calls_before,
        "batch_resp_before": batch_resp_before,
        "submit_ts": submit_ts,
        "prompt": prompt,
    }


def _detect_block_in_window(client, batch_resp_before: int) -> int:
    """Return the HTTP status code (403/429) if the most recent submit
    response in this window indicates a Flow-side block; 0 otherwise.

    Used by submit_generate_l1 to translate "no gen_id captured because
    Flow returned 403" into a RecaptchaError that the burn-recovery
    wrapper can react to (wipe+rewarm + retry).
    """
    responses = getattr(client, "_batch_responses", None) or []
    for entry in responses[batch_resp_before:]:
        url_l = (entry.get("url") or "").lower()
        if "batchasyncgenerate" not in url_l:
            continue
        status = entry.get("status") or 0
        if status in (403, 429):
            return int(status)
    return 0


async def _await_gen_id_in_window(
    client,
    calls_before: int,
    *,
    batch_resp_before: int = 0,
    timeout_sec: float = 15.0,
    poll_sec: float = 0.3,
) -> str:
    """Poll until this submit's operation response lands.

    Flow's submit endpoint is one of:
      * legacy ``operations/...`` (older Flow builds — body has ``name``)
      * current ``v1/video:batchAsyncGenerateVideoText`` (the body is
        opaque to ``flow.client._on_response`` because that handler only
        fetches JSON for ``operations/`` / ``/v1/credits`` URLs).

    We install a side-channel listener at batch-start (see
    :func:`install_batch_response_capture`) that records full response
    bodies for the new URL pattern into ``client._batch_responses``.
    Both sources are inspected here.

    Bounded by ``timeout_sec``.
    """
    meta = await _await_submit_metadata_in_window(
        client,
        calls_before,
        batch_resp_before=batch_resp_before,
        timeout_sec=timeout_sec,
        poll_sec=poll_sec,
    )
    return meta.get("gen_id", "")


async def _await_submit_metadata_in_window(
    client,
    calls_before: int,
    *,
    batch_resp_before: int = 0,
    timeout_sec: float = 15.0,
    poll_sec: float = 0.3,
) -> dict[str, str]:
    """Poll until this submit's backend handles land.

    Current Flow submit responses no longer include ``operations/<id>``.
    The canonical output id is ``media[0].name``; the workflow id in
    ``workflows[0].name`` is retained as our generation handle for result
    metadata. Legacy operation-name responses still map to ``gen_id``.
    """
    deadline = asyncio.get_event_loop().time() + timeout_sec
    while True:
        meta = _capture_submit_metadata_from_window(
            client, calls_before, batch_resp_before=batch_resp_before,
        )
        if meta.get("gen_id"):
            return meta
        if asyncio.get_event_loop().time() >= deadline:
            return {}
        await asyncio.sleep(poll_sec)


_BATCH_GEN_URL_HINTS = (
    "operations/",
    "v1/video:batchasyncgeneratevideotext",
    "v1/video:batchasyncgenerate",
    "v1/video:asyncgenerate",
    ":generatevideo",
    "v1/video:batchcheckasync",       # status polling
    "v1/video:batchcheckasyncvideo",
    "v1/media:batchasync",            # download URL fetch (variant)
    ":batchcheckasync",
)


def _capture_gen_id_from_window(
    client,
    calls_before: int,
    *,
    batch_resp_before: int = 0,
) -> str:
    """Find this submit's operation id inside its own window slice.

    Inspects two sources in order:

      1. ``client._batch_responses[batch_resp_before:]`` — set up by
         :func:`install_batch_response_capture`. This is the authoritative
         clock for batch isolation: each new submit advances the buffer
         length, even when ``client._calls`` doesn't (because legacy
         flow.client only fetches body for ``operations/`` URLs).
      2. ``client._calls[calls_before:]`` — useful when Flow falls back
         to the old ``operations/`` endpoint.

    Returns the first non-empty generation handle found, or ``""``.
    """
    return _capture_submit_metadata_from_window(
        client,
        calls_before,
        batch_resp_before=batch_resp_before,
    ).get("gen_id", "")


def _capture_submit_metadata_from_window(
    client,
    calls_before: int,
    *,
    batch_resp_before: int = 0,
) -> dict[str, str]:
    """Find this submit's media/workflow ids inside its own window slice."""
    batch_responses = getattr(client, "_batch_responses", None) or []
    for entry in batch_responses[batch_resp_before:]:
        body = entry.get("body")
        if not isinstance(body, dict):
            continue
        meta = _extract_submit_metadata(body)
        if meta.get("gen_id"):
            return meta

    calls = getattr(client, "_calls", [])
    for entry in calls[calls_before:]:
        url = (entry.get("url", "") or "").lower()
        if not any(hint in url for hint in _BATCH_GEN_URL_HINTS):
            continue
        body = entry.get("body")
        if isinstance(body, dict):
            meta = _extract_submit_metadata(body)
            if meta.get("gen_id"):
                return meta
    return {}


def _extract_submit_metadata(body: dict) -> dict[str, str]:
    """Extract current/legacy L1 submit handles from Flow response JSON."""
    metadata: dict[str, str] = {
        "gen_id": "",
        "workflow_id": "",
        "media_id": "",
        "project_id": "",
        "batch_id": "",
        "workflow_step_id": "",
    }

    media = body.get("media")
    if isinstance(media, list) and media:
        first_media = media[0]
        if isinstance(first_media, dict):
            media_id = first_media.get("name") or first_media.get("mediaId") or ""
            if media_id:
                metadata["media_id"] = str(media_id)
            project_id = first_media.get("projectId") or ""
            if project_id:
                metadata["project_id"] = str(project_id)
            workflow_id = first_media.get("workflowId") or ""
            if workflow_id:
                metadata["workflow_id"] = str(workflow_id)
            workflow_step_id = first_media.get("workflowStepId") or ""
            if workflow_step_id:
                metadata["workflow_step_id"] = str(workflow_step_id)
            media_meta = first_media.get("mediaMetadata") or {}
            if isinstance(media_meta, dict):
                batch_id = media_meta.get("batchId") or ""
                if batch_id:
                    metadata["batch_id"] = str(batch_id)

    workflows = body.get("workflows")
    if isinstance(workflows, list) and workflows:
        first_workflow = workflows[0]
        if isinstance(first_workflow, dict):
            workflow_id = first_workflow.get("name") or ""
            if workflow_id:
                metadata["workflow_id"] = str(workflow_id)
            workflow_meta = first_workflow.get("metadata") or {}
            if isinstance(workflow_meta, dict):
                for source_key, dest_key in (
                    ("primaryMediaId", "media_id"),
                    ("projectId", "project_id"),
                    ("batchId", "batch_id"),
                ):
                    value = workflow_meta.get(source_key) or ""
                    if value and not metadata[dest_key]:
                        metadata[dest_key] = str(value)

    legacy_name = _extract_op_name(body)
    metadata["gen_id"] = (
        metadata["workflow_id"]
        or metadata["media_id"]
        or legacy_name
    )
    if legacy_name and not metadata["workflow_id"]:
        metadata["workflow_id"] = legacy_name
    return metadata


def _extract_op_name(body: dict) -> str:
    """Extract a stable per-generation identifier from a Flow response body.

    Flow uses several shapes across builds:

      * legacy: ``{"name": "operations/abc"}``
      * current ``v1/video:batchAsyncGenerateVideoText`` (verified live
        2026-05-04 on ngoctuandt20)::

            {
              "operations": [
                {
                  "operation": {"name": "<uuid>"},   # ← gen_id
                  "sceneId": "...",
                  "status": "MEDIA_GENERATION_STATUS_PENDING"
                }, ...
              ],
              "remainingCredits": ...,
              "workflows": [...],
              "media": [...]
            }
      * newer build flat: ``{"id": "...", "operationName": "operations/abc"}``

    Returns the first plausible name. Empty string when nothing matches.
    """
    direct = body.get("name") or body.get("operationName") or ""
    if direct:
        return str(direct)
    ops = body.get("operations")
    if isinstance(ops, list) and ops:
        first = ops[0]
        if isinstance(first, dict):
            # Current schema: nested under "operation".
            inner = first.get("operation")
            if isinstance(inner, dict):
                n = inner.get("name") or inner.get("operationName") or ""
                if n:
                    return str(n)
            # Legacy / future flat schema.
            n = first.get("name") or first.get("operationName") or ""
            if n:
                return str(n)
    raw_id = body.get("id") or ""
    if raw_id:
        return str(raw_id)
    return ""


def install_batch_response_capture(client) -> None:
    """Install a Playwright response listener that records batch-submit
    response bodies into ``client._batch_responses``.

    Required because :func:`flow.client.FlowClient._on_response` only
    fetches JSON bodies for legacy ``operations/`` URLs, leaving the
    new ``v1/video:batchAsyncGenerateVideoText`` body un-parsed. We
    layer this side-channel without modifying ``flow/client.py``.

    Safe to call multiple times; subsequent calls are no-ops because
    Playwright would otherwise duplicate the listener per call.
    """
    if getattr(client, "_batch_capture_installed", False):
        return
    if not hasattr(client, "_batch_responses"):
        client._batch_responses = []
    if not hasattr(client, "_batch_requests"):
        client._batch_requests = []
    client._batch_capture_installed = True
    page = client.page

    def _on_request(request):
        try:
            url_l = (request.url or "").lower()
        except Exception:
            return
        if not any(hint in url_l for hint in _BATCH_GEN_URL_HINTS):
            return
        try:
            body = request.post_data
        except Exception:
            body = None
        try:
            headers = dict(request.headers)
        except Exception:
            headers = {}
        client._batch_requests.append({
            "url": request.url,
            "method": request.method,
            "headers": headers,
            "post_data": body,
            "ts": time.time(),
        })
        body_str = body if isinstance(body, str) else ""
        logger.info(
            "batch request capture: %s %s body_len=%d body[:300]=%s",
            request.method, request.url[:100],
            len(body_str), body_str[:300],
        )

    page.on("request", _on_request)

    async def _on_response(response):
        try:
            url_l = (response.url or "").lower()
        except Exception:
            return
        if not any(hint in url_l for hint in _BATCH_GEN_URL_HINTS):
            return
        try:
            status = response.status
        except Exception:
            status = 0
        body: Any = None
        body_err: str | None = None
        try:
            if status == 200:
                body = await response.json()
        except Exception as e1:
            body_err = f"json: {e1!r}"
            try:
                body = await response.text()
            except Exception as e2:
                body_err += f" / text: {e2!r}"
                body = None
        client._batch_responses.append({
            "url": response.url,
            "status": status,
            "body": body,
            "body_err": body_err,
            "calls_index": len(getattr(client, "_calls", [])),
            "ts": time.time(),
        })
        logger.info(
            "batch capture: url=%s status=%s body_type=%s body_err=%s",
            response.url[:100], status, type(body).__name__, body_err,
        )
        if isinstance(body, dict):
            logger.info("batch capture body keys: %s", list(body.keys())[:10])
            import json as _json
            ops = body.get("operations") or []
            media = body.get("media") or []
            workflows = body.get("workflows") or []
            logger.info(
                "batch capture array sizes: ops=%d media=%d workflows=%d",
                len(ops) if isinstance(ops, list) else -1,
                len(media) if isinstance(media, list) else -1,
                len(workflows) if isinstance(workflows, list) else -1,
            )
            if isinstance(ops, list):
                for i, op in enumerate(ops[:5]):
                    try:
                        logger.info("batch capture ops[%d]: %s", i,
                                    _json.dumps(op)[:500])
                    except Exception:
                        logger.info("batch capture ops[%d] str: %s", i,
                                    str(op)[:500])
            if isinstance(media, list) and media:
                try:
                    logger.info("batch capture media[0]: %s",
                                _json.dumps(media[0])[:500])
                except Exception:
                    logger.info("batch capture media[0] str: %s",
                                str(media[0])[:500])
            if isinstance(workflows, list) and workflows:
                try:
                    logger.info("batch capture workflows[0]: %s",
                                _json.dumps(workflows[0])[:500])
                except Exception:
                    logger.info("batch capture workflows[0] str: %s",
                                str(workflows[0])[:500])

    page.on("response", _on_response)
    logger.info("Batch response capture installed for client profile=%s",
                getattr(client, "profile_name", "?"))


# ---------------------------------------------------------------------------
# Wait
# ---------------------------------------------------------------------------


# Per-submit timeouts mirror flow.wait.TIMEOUTS for text-to-video.
L1_HARD_TIMEOUT_SEC = 900     # 15 min
L1_NO_SIGNAL_SEC = 300        # 5 min idle


async def wait_for_all_l1_gens(
    client,
    submits: list[dict],
    *,
    parent_media_id: str | None = None,
    hard_timeout: int = L1_HARD_TIMEOUT_SEC,
    no_signal_timeout: int = L1_NO_SIGNAL_SEC,
) -> list[dict]:
    """Collective wait for N concurrent L1 gens, then return submit order.

    Current Flow submit responses expose ``media[0].name`` as the canonical
    media id and ``workflows[0].name`` as the workflow handle. Completion is
    read by polling ``video:batchCheckAsyncVideoGenerationStatus`` with those
    media ids. Older operation/media-event fallback remains for legacy builds.

    Returns one result dict per submit, in submission order::

        {"status": "completed", "media_id": ..., "media_ids": [..],
         "error": None}
        # or
        {"status": "failed", "media_id": None, "media_ids": [],
         "error": "timeout|no_signal_timeout|recaptcha_*"}

    On any RecaptchaError the function raises immediately so the
    dispatcher can mark the profile burned for the whole batch.
    """
    n = len(submits)
    if n == 0:
        return []

    status_api_results = await _wait_for_all_l1_gens_via_status_api(
        client,
        submits,
        hard_timeout=hard_timeout,
    )
    if status_api_results is not None:
        return status_api_results

    earliest_submit_ts = min(s["submit_ts"] for s in submits)

    start = time.monotonic()
    last_count = 0
    last_signal_time = start
    last_recaptcha_check = 0.0

    while True:
        elapsed = time.monotonic() - start
        if elapsed > hard_timeout:
            err = f"timeout after {hard_timeout}s waiting for {n} L1 gens"
            await capture_failure_nonblocking(
                client, "timeout",
                extra={"want": n, "got": last_count, "elapsed": int(elapsed)},
            )
            return _all_failed(submits, err)

        # reCAPTCHA via network → propagate.
        network_kind = await detect_recaptcha_in_network(client)
        if network_kind:
            call = first_recaptcha_call(client) or {}
            err = RecaptchaError(kind=network_kind, url=str(call.get("url") or ""))
            cap = await capture_failure_nonblocking(client, f"recaptcha_{network_kind}")
            if cap:
                setattr(err, "capture_path", cap)
            raise err

        # New media events post-submit (excluding parent).
        mids = _collect_media_ids_after(
            client, since_ts=earliest_submit_ts, exclude=parent_media_id,
        )
        if len(mids) > last_count:
            last_count = len(mids)
            last_signal_time = time.monotonic()
        if len(mids) >= n:
            # All done — assign first N in chronological order to submits[i].
            return [
                {
                    "status": "completed",
                    "media_id": mids[i],
                    "media_ids": [mids[i]],
                    "error": None,
                }
                for i in range(n)
            ]

        # DOM reCAPTCHA throttled probe.
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
            err = (
                f"no_signal_timeout after {int(silence)}s "
                f"waiting for {n - last_count} more L1 gens"
            )
            await capture_failure_nonblocking(
                client, "no_signal_timeout",
                extra={"want": n, "got": last_count, "silent_sec": int(silence)},
            )
            # Partial: completed ones get their mid, the rest fail.
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


async def _wait_for_all_l1_gens_via_status_api(
    client,
    submits: list[dict],
    *,
    hard_timeout: int,
) -> list[dict] | None:
    """Poll Flow status API by canonical media ids, preserving input order."""
    media_ids = [str(s.get("media_id") or "") for s in submits]
    if not media_ids or any(not mid for mid in media_ids):
        return None

    project_ids = [str(s.get("project_id") or "") for s in submits]
    project_id = next((pid for pid in project_ids if pid), "")
    if not project_id:
        return None

    logger.info(
        "L1 batch status API wait: polling %d media handles in project=%s",
        len(media_ids),
        project_id,
    )
    poll_result = await poll_status_via_api(
        client,
        gen_ids=media_ids,
        project_id=project_id,
        hard_timeout_sec=float(hard_timeout),
    )

    results: list[dict] = []
    for media_id in media_ids:
        slot = poll_result.get(media_id) if isinstance(poll_result, dict) else None
        if not isinstance(slot, dict):
            results.append({
                "status": "failed",
                "media_id": None,
                "media_ids": [],
                "error": f"status API returned no slot for media_id={media_id}",
            })
            continue

        status = slot.get("status")
        if status == "completed":
            resolved_media_id = str(slot.get("media_id") or media_id)
            results.append({
                "status": "completed",
                "media_id": resolved_media_id,
                "media_ids": [resolved_media_id],
                "error": None,
            })
        elif status == "failed":
            results.append({
                "status": "failed",
                "media_id": None,
                "media_ids": [],
                "error": str(slot.get("error") or "status API failed"),
            })
        else:
            results.append({
                "status": "failed",
                "media_id": None,
                "media_ids": [],
                "error": f"status API did not complete (status={status})",
            })
    return results


def _all_failed(submits: list[dict], err: str) -> list[dict]:
    return [
        {"status": "failed", "media_id": None, "media_ids": [], "error": err}
        for _ in submits
    ]


# Backwards-compat per-gen wait (legacy path, single submit). Phase 1
# orchestrator uses :func:`wait_for_all_l1_gens` instead. Kept so unit tests
# that exercise the per-gen helper continue to work.
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
    metadata: dict | None = None,
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
        metadata=metadata,
    )
    if not files:
        raise RuntimeError(f"download_l1_gen({media_id[:12]}): no output file")
    return files


async def snapshot_unique_tile_ids(page) -> list[str]:
    """Return the live list of distinct data-tile-id values on /project/.

    Flow renders each tile twice (main grid + side rail). Visible tiles
    are de-duped here in DOM order so callers can lock a stable mapping
    of submit-index → tile-id BEFORE any tile is clicked (downloads
    promote the most-recently-edited tile to position 0, breaking any
    index that's resolved fresh per call).
    """
    return await page.evaluate(
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


async def download_l1_gen_at_tile(
    client,
    *,
    tile_index: int,
    media_id: str,
    project_url: str,
    pinned_tile_id: str | None = None,
    prefix: str = "t2v",
    quality: str = "1080p",
    metadata: dict | None = None,
) -> list[str]:
    """Download the i-th project tile (most-recent-first ordering).

    Phase 1 Live verify on debian-root 2026-05-04 surfaced a contamination
    bug: Flow's project view orders tiles by creation timestamp DESC, but
    `flow.upscale._ensure_edit_view` always clicks ``[data-tile-id^=fe_id_]``
    .first. Three batched L1 t2v downloads therefore all fetched tile[0]
    (the most-recent submit's video, repeated 3×, identical md5).

    This wrapper routes around that by clicking tile_index BEFORE calling
    download_video. Once the SPA settles on ``/edit/{routing_slug}`` the
    upscale path's tile-click branch is skipped (it's gated on
    ``"/edit/" in page.url``).

    Tile-index → submit-index mapping (caller's responsibility):

      tile order   = [submit_N-1, submit_N-2, ..., submit_0]   # newest first
      tile_index   = N - 1 - submit_index

    so submit 0 (oldest) downloads from tile_index = N-1.
    """
    if not media_id:
        raise RuntimeError("download_l1_gen_at_tile called without media_id")
    page = client.page

    # Step 0 — make sure we're on the project root view (NOT /edit/) so
    # the tiles are addressable and reorderable. If we're already on /edit/
    # of one tile, the tile rail still renders, but to be safe step out
    # to the project URL first.
    if "/project/" not in page.url or "/edit/" in page.url:
        if project_url:
            try:
                await page.goto(project_url, wait_until="domcontentloaded",
                                timeout=20000)
                await asyncio.sleep(2)
            except Exception as exc:
                logger.warning("download_l1_gen_at_tile: goto project failed: %s", exc)

    # Step 1 — resolve which data-tile-id we want to click.
    # Live verify on debian-root 2026-05-04 surfaced three contamination
    # modes in the tile-index download path:
    #   (a) Flow renders each tile twice in the project view (deduplicate).
    #   (b) Tile ORDER changes between downloads (promote-on-upscale).
    #   (c) data-tile-id values themselves can change after an upscale —
    #       the post-upscale snapshot may not contain a tile-id we pinned
    #       before any download. Fall back to live tile_index in that case.
    try:
        chosen_via = "live"
        if pinned_tile_id:
            try:
                await page.locator(
                    f"[data-tile-id='{pinned_tile_id}']"
                ).first.wait_for(state="attached", timeout=2500)
                target_tile_id = pinned_tile_id
                chosen_via = "pinned"
            except Exception:
                logger.warning(
                    "download_l1_gen_at_tile: pinned id %s not found post-upscale; "
                    "falling back to live tile_index=%d",
                    pinned_tile_id[:30], tile_index,
                )
                target_tile_id = None
        else:
            target_tile_id = None

        if target_tile_id is None:
            unique_tile_ids = await snapshot_unique_tile_ids(page)
            logger.info(
                "download_l1_gen_at_tile: %d unique tiles, target idx=%d, ids=%s",
                len(unique_tile_ids), tile_index,
                [t[:25] for t in unique_tile_ids[:10]],
            )
            if tile_index >= len(unique_tile_ids):
                raise RuntimeError(
                    f"download_l1_gen_at_tile: tile_index={tile_index} but only "
                    f"{len(unique_tile_ids)} unique tiles rendered"
                )
            target_tile_id = unique_tile_ids[tile_index]

        logger.info(
            "download_l1_gen_at_tile: clicking tile id=%s via=%s",
            target_tile_id[:30], chosen_via,
        )
        target = page.locator(f"[data-tile-id='{target_tile_id}']").first
        await target.wait_for(state="attached", timeout=8000)
        await target.scroll_into_view_if_needed(timeout=2000)
        # Some tiles are <div> without click handler on the outer element —
        # fall back to dispatching MouseEvents on the inner card via JS.
        try:
            await target.click(timeout=5000)
        except Exception:
            tile_id = await target.get_attribute("data-tile-id")
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
                tile_id,
            )
        # Wait for /edit/ URL to settle.
        deadline = time.time() + 10
        while time.time() < deadline:
            if "/edit/" in page.url:
                break
            await asyncio.sleep(0.2)
        else:
            logger.warning(
                "download_l1_gen_at_tile: SPA didn't reach /edit/ after click; URL=%s",
                page.url[:120],
            )
        await asyncio.sleep(1.5)
    except Exception as exc:
        raise RuntimeError(
            f"download_l1_gen_at_tile: tile click failed (index={tile_index}): {exc}"
        ) from exc

    # Step 2 — download. download_video will see we're on /edit/ already
    # and skip its own tile.first click.
    files = await download_video(
        client,
        media_ids=[media_id],
        prefix=prefix,
        quality=quality,
        media_kind="video",
        metadata=metadata,
    )
    if not files:
        raise RuntimeError(
            f"download_l1_gen_at_tile(idx={tile_index}, mid={media_id[:12]}): "
            f"no output file"
        )
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
