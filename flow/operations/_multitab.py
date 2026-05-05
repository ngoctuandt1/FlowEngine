"""Multi-tab orchestration for L2 / L3 / L4+ ops.

Flow's `/edit/{media_id}` editor is single-tenant per tab — the panel
state (Extend / Camera / Insert / Remove) belongs to the tab and back-
to-back submits in the same tab race against Flow's panel-disable cycle.

Pattern (verified by user's runtime experience): to parallelise N ops on
the same Chrome session, open N TABS, drive one op per tab, gather
async. Same primitive applies whether the parent is an L1 (siblings on
same project), an L2 (L3 stacked), or deeper (L4+).

This module exposes:

* :func:`dispatch_op_in_new_tab` — opens a new tab, navigates to a
  specific `/edit/{parent_media_id}`, dispatches one op via the legacy
  per-op handler, downloads, closes the tab. Returns the op's result.
* :func:`batch_dispatch_ops_multitab` — orchestrator: takes a list of
  ops with their parent context, runs them in parallel via
  ``asyncio.gather``. Each op gets its own tab.

Per-job dict shape::

    {
        "id": <job_id>,
        "type": "extend-video" | "camera-move" | "insert-object" | "remove-object",
        "parent_edit_url": "<https://...labs.google/.../edit/{parent_media_id}>",
        "parent_media_id": "<uuid>",
        "parent_project_url": "<https://...labs.google/.../project/{id}>",
        "prompt": <str>           # extend / insert
        "direction": <str>        # camera
        "bbox": {x,y,w,h}         # insert / remove
    }

Returns one result per job in input order (same shape as legacy
finalize_operation result + a ``job_id`` field).
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from flow.recaptcha import RecaptchaError

logger = logging.getLogger(__name__)


class _TabClient:
    """Minimal FlowClient-like proxy that pins ``.page`` to a specific tab.

    The legacy per-op handlers (``extend_video`` / ``camera_move`` /
    ``insert_object`` / ``remove_object``) all read ``client.page`` and
    pass ``client`` to nested helpers. Wrapping with this proxy lets us
    reuse them unchanged across N tabs without refactoring.

    Critical: capture buffers (``_calls``, ``_video_urls``,
    ``_media_id_events``, ``_gen_id``) MUST be per-tab. The shared
    real-client list contaminates ``_collect_media_ids(start_index=N)``
    when 2+ tabs run concurrently — Tab A's media event would appear
    in Tab B's window. Live evidence 2026-05-04 chain v8: 3 chains all
    resolved their L2 mid as ``55e08aed-e99`` (chain 0's mid leaked).
    Each ``_TabClient`` therefore owns isolated buffers, and the per-
    tab response handler writes only to those.

    Forwards every other attribute (profile_name, context, auth state)
    to the underlying real FlowClient.
    """

    def __init__(self, real_client, tab_page) -> None:
        self._real = real_client
        self.page = tab_page
        # Per-tab capture buffers — fully isolated from real client and
        # from sibling tabs. Legacy ops + wait_for_completion read these
        # via attribute access; with __getattr__ falling through, having
        # them as instance attrs here means `client._media_id_events`
        # resolves to OUR list, not the shared one.
        self._calls: list[dict[str, Any]] = []
        self._video_urls: list[dict[str, Any]] = []
        self._media_id_events: list[dict[str, Any]] = []
        self._gen_id: str | None = None
        self._account_info: dict[str, Any] | None = None
        # Side-channel buffers used by inflate / status poll path. Same
        # isolation reasoning.
        self._batch_responses: list[dict[str, Any]] = []
        self._batch_requests: list[dict[str, Any]] = []
        self._batch_capture_installed = False

    def __getattr__(self, name: str) -> Any:
        # Falls through for anything not on the proxy itself
        # (profile_name, context, _job_id, etc.).
        return getattr(self._real, name)

    # FlowClient methods that mutate capture state must rebind to OUR
    # lists. The originals on FlowClient touch ``self._media_id_events``
    # etc. — when called as bound methods via __getattr__, ``self`` is
    # the real client, so writes land in the wrong list. Re-implement
    # the small surface we actually need.

    def clear_captures(self) -> None:
        self._calls.clear()
        self._video_urls.clear()
        self._media_id_events.clear()
        self._gen_id = None

    def _record_media_id(
        self, mid: str, source: str = "", url: str = "",
    ) -> None:
        from flow.media_id import looks_like_media_id, normalize_media_id
        n = normalize_media_id(mid)
        if not n or not looks_like_media_id(n):
            return
        for rec in reversed(self._media_id_events[-200:]):
            if rec.get("mid") == n:
                return
        self._media_id_events.append(
            {"mid": n, "source": source, "url": url, "ts": time.time()}
        )
        if len(self._media_id_events) > 600:
            self._media_id_events = self._media_id_events[-350:]


async def _make_tab_response_handler(proxy: _TabClient):
    """Build a per-tab `_on_response` that writes to proxy buffers only.

    Mirrors :py:meth:`flow.client.FlowClient._on_response` but every
    state mutation lands in the proxy's isolated lists. Required for
    multi-tab correctness (see :class:`_TabClient` docstring).
    """
    from flow.media_id import looks_like_media_id, normalize_media_id, media_id_from_url

    async def _on_response(response):
        try:
            url = response.url
            url_l = url.lower()
            status = response.status
            entry = {
                "url": url,
                "status": status,
                "method": response.request.method,
                "ts": time.time(),
            }
            if status == 200 and (
                "operations/" in url_l
                or "/v1/credits" in url_l
                or "getmediaurlredirect" in url_l
            ):
                try:
                    entry["body"] = await response.json()
                except Exception:
                    try:
                        entry["body"] = await response.text()
                    except Exception:
                        pass
            proxy._calls.append(entry)
            if len(proxy._calls) > 500:
                proxy._calls = proxy._calls[-300:]

            mid = media_id_from_url(url)
            if mid and looks_like_media_id(normalize_media_id(mid)):
                proxy._record_media_id(mid, source="response_url", url=url)

            ctype = (response.headers.get("content-type", "") or "").lower()
            is_video = (
                ".mp4" in url_l
                or ".webm" in url_l
                or ".mov" in url_l
                or "video/" in ctype
                or (
                    "getmediaurlredirect" in url_l
                    and (
                        "mediaurltype=media_url_type_video" in url_l
                        or ".mp4" in url_l
                    )
                )
            )
            if is_video and status == 200:
                if url not in [v["url"] for v in proxy._video_urls]:
                    proxy._video_urls.append({"url": url, "ts": time.time()})
                    if len(proxy._video_urls) > 400:
                        proxy._video_urls = proxy._video_urls[-250:]

            if "operations/" in url_l and status == 200:
                body = entry.get("body")
                if isinstance(body, dict):
                    name = body.get("name", "")
                    if name and not proxy._gen_id:
                        proxy._gen_id = str(name)
        except Exception as exc:
            logger.debug("tab response handler error: %s", exc)

    return _on_response


_OP_HANDLER_FACTORY = {
    "extend-video": "extend_video",
    "camera-move": "camera_move",
    "insert-object": "insert_object",
    "remove-object": "remove_object",
}


async def dispatch_op_in_new_tab(
    real_client,
    job: dict,
) -> dict:
    """Open a new tab in the existing Chrome context, drive one op, close.

    Returns one result dict shaped like the legacy op output, plus a
    ``job_id`` field. On any RecaptchaError raises so the caller can
    swap the profile.
    """
    op_type = job.get("type")
    handler_name = _OP_HANDLER_FACTORY.get(op_type)
    if not handler_name:
        return {
            "job_id": job.get("id"),
            "status": "failed",
            "error": f"unsupported op type: {op_type}",
        }

    parent_edit_url = job.get("parent_edit_url") or job.get("edit_url") or ""
    parent_media_id = job.get("parent_media_id") or job.get("media_id") or ""
    parent_project_url = (
        job.get("parent_project_url") or job.get("project_url") or ""
    )
    if not parent_edit_url or not parent_media_id:
        return {
            "job_id": job.get("id"),
            "status": "failed",
            "error": "missing parent_edit_url / parent_media_id",
        }

    context = real_client.context
    tab = await context.new_page()

    proxy = _TabClient(real_client, tab)
    proxy._job_id = job.get("id") or job.get("type")

    # Bind a per-tab response handler that writes to PROXY buffers only.
    # Sharing real_client._on_response across tabs causes media_id
    # contamination: multi-tab `_collect_media_ids(start_index=N)`
    # windows all start at the same index and pick up sibling tabs'
    # events. Per-tab buffers fix it (live verify v8 2026-05-04
    # observed 3 chains all resolving to chain-0's L2 mid).
    tab_handler = await _make_tab_response_handler(proxy)
    try:
        tab.on("response", tab_handler)
    except Exception as exc:
        logger.warning("multitab: bind response handler failed: %s", exc)
    # Side-channel batch capture (inflate / status poll) — install
    # against the proxy so its _batch_responses buffer fills up. The
    # legacy install_batch_response_capture writes to client._batch_*
    # which on the proxy is OUR isolated list.
    try:
        from flow.operations._l1_batch import install_batch_response_capture
        install_batch_response_capture(proxy)
    except Exception as exc:
        logger.warning("multitab: batch capture install failed: %s", exc)

    try:
        # The legacy ops read these fields off `job` to navigate +
        # build edit_url. Inject the parent context with the keys the
        # legacy helpers expect.
        op_job = dict(job)
        op_job.setdefault("edit_url", parent_edit_url)
        op_job.setdefault("media_id", parent_media_id)
        op_job.setdefault("project_url", parent_project_url)

        # Navigate first so the Slate composer + edit panel mount
        # before the op handler probes the DOM.
        # bring_to_front() before navigate: Chrome throttles background
        # tabs (visibility=hidden), which lazes React's state-flush and
        # leaves Insert/Camera buttons in a transient `disabled` state
        # the legacy probe misread as B28 lockout. Bringing each tab
        # forward briefly lets React commit before we probe; the actual
        # network/submit phase still races across tabs because we
        # gather() them concurrently — only the per-tab UI setup is
        # foreground.
        try:
            try:
                await tab.bring_to_front()
            except Exception as exc:
                logger.debug("multitab: bring_to_front failed: %s", exc)
            await tab.goto(
                parent_edit_url,
                wait_until="domcontentloaded", timeout=30000,
            )
            await asyncio.sleep(2)
        except Exception as exc:
            return {
                "job_id": job.get("id"),
                "status": "failed",
                "error": f"goto edit_url failed: {exc}",
            }

        # Dispatch via the legacy handler — it expects (client, job, ...)
        # and returns a result dict on success or raises on failure.
        if op_type == "extend-video":
            from flow.operations.extend import extend_video
            result = await extend_video(
                proxy, op_job,
                prompt=job.get("prompt", ""),
                model=job.get("model", "veo-3.1-fast-lp"),
                free_mode=True,
            )
        elif op_type == "camera-move":
            from flow.operations.camera import camera_move
            result = await camera_move(
                proxy, op_job,
                direction=job.get("direction", ""),
            )
        elif op_type == "insert-object":
            from flow.operations.insert import insert_object
            result = await insert_object(
                proxy, op_job,
                prompt=job.get("prompt", ""),
                bbox=job.get("bbox") or {},
            )
        elif op_type == "remove-object":
            from flow.operations.remove import remove_object
            result = await remove_object(
                proxy, op_job,
                bbox=job.get("bbox") or {},
            )
        else:
            return {
                "job_id": job.get("id"),
                "status": "failed",
                "error": f"unknown op type: {op_type}",
            }

        result.setdefault("status", "completed")
        result["job_id"] = job.get("id")

        # Authoritative override: see _media_id_from_submit_response.
        # Legacy resolver's `_media_id_events` window can be polluted
        # by sibling-thumbnail prefetches in the same tab; per-tab
        # submit-response media[0].name is contamination-free.
        canonical = _media_id_from_submit_response(proxy, op_type=op_type)
        if canonical:
            prev_mid = result.get("media_id")
            if prev_mid != canonical:
                logger.info(
                    "multitab authoritative: override resolved mid %s "
                    "with submit-response media[0].name %s",
                    (prev_mid or "")[:12], canonical[:12],
                )
            result["media_id"] = canonical

        new_mid = result.get("media_id")
        if new_mid and new_mid != parent_media_id:
            fresh_files = await _redownload_via_media_url(
                proxy, new_mid, parent_project_url,
                prefix=op_type.replace("-", "_")[:16],
            )
            if fresh_files:
                result["output_files"] = fresh_files
                logger.info(
                    "multitab redownload: gen %s fetched via media URL",
                    new_mid[:12],
                )
        return result

    except RecaptchaError:
        raise
    except Exception as exc:
        logger.exception("dispatch_op_in_new_tab failed for job %s: %s",
                         job.get("id"), exc)
        return {
            "job_id": job.get("id"),
            "status": "failed",
            "error": str(exc),
        }
    finally:
        try:
            await tab.close()
        except Exception:
            pass


async def _redownload_via_media_url(
    client,
    media_id: str,
    project_url: str,
    *,
    prefix: str = "op",
) -> list[str]:
    """Fallback download path when the legacy tile-click snags the parent.

    Hits Flow's ``media.getMediaUrlRedirect?name={id}`` endpoint, which
    redirects to (or streams) the binary for THIS specific media_id —
    independent of which tile is currently rendered. Tries the
    ``_upsampled`` 1080p variant first, falls back to the base 720p
    variant. Same shape used in :mod:`flow.download`.

    Upsample timing — ``_upsampled`` returns HTTP 404 while the
    upsampler job is still running. Live runs 2026-05-04 (chains x3
    v4) showed ~50 % of clips fall back to 720p purely because the
    upsampler hadn't finished by the time the dispatcher polled.
    Fix: when the 1080p variant 404s, poll for up to ``upsample_wait``
    seconds before giving up and dropping to 720p.
    """
    import asyncio as _asyncio
    import os
    import time
    from pathlib import Path

    upsample_wait = float(os.environ.get("FLOW_UPSAMPLE_POLL_SEC", "180"))
    upsample_interval = float(os.environ.get("FLOW_UPSAMPLE_POLL_INTERVAL", "5"))

    download_dir = Path(os.environ.get("FLOW_DOWNLOAD_DIR", "./downloads"))
    download_dir.mkdir(parents=True, exist_ok=True)
    page = client.page
    base = "https://labs.google/fx/api/trpc/media.getMediaUrlRedirect"

    for suffix, label in (("_upsampled", "1080p"), ("", "720p")):
        api_url = f"{base}?name={media_id}{suffix}"
        poll_deadline = (
            time.time() + upsample_wait if suffix == "_upsampled" else 0.0
        )
        resp = None
        while True:
            try:
                resp = await page.context.request.get(api_url, timeout=60000)
            except Exception as exc:
                logger.warning(
                    "redl: GET %s failed for %s: %s",
                    label, media_id[:12], exc,
                )
                resp = None
                break
            # 404 on _upsampled = upsampler still running. Poll.
            if (
                resp.status == 404
                and suffix == "_upsampled"
                and time.time() < poll_deadline
            ):
                logger.info(
                    "redl: 1080p not ready (404) for %s — polling",
                    media_id[:12],
                )
                await _asyncio.sleep(upsample_interval)
                continue
            break
        if resp is None:
            continue
        if resp.status != 200:
            logger.info(
                "redl: %s HTTP %d for %s — trying next variant",
                label, resp.status, media_id[:12],
            )
            continue
        ctype = resp.headers.get("content-type", "")
        if "video" not in ctype and "octet" not in ctype:
            logger.info(
                "redl: %s wrong content-type %s for %s",
                label, ctype, media_id[:12],
            )
            continue
        try:
            body = await resp.body()
        except Exception as exc:
            logger.warning("redl: body read failed: %s", exc)
            continue
        if len(body) < 50_000:
            logger.info(
                "redl: %s body too small (%d bytes) for %s",
                label, len(body), media_id[:12],
            )
            continue
        out_path = str(
            download_dir
            / f"{prefix}_redl_{label}_{int(time.time())}_{media_id[:8]}.mp4"
        )
        with open(out_path, "wb") as f:
            f.write(body)
        logger.info(
            "redl: saved %s %d bytes for %s",
            label, len(body), media_id[:12],
        )
        return [out_path]
    return []


async def batch_dispatch_ops_multitab(
    client,
    jobs: list[dict],
) -> list[dict]:
    """Run N ops in parallel — one tab each, all under the same Chrome.

    Caller responsibilities:
      * Each job MUST carry ``parent_edit_url`` + ``parent_media_id``.
      * Same-account constraint still applies (one client = one
        Google account = one Chrome). Ops on a different account
        require a different worker / profile.
      * Project-lock not enforced here — multi-tab safe because each
        op operates on a different ``parent_media_id`` (hence
        different `/edit/{slug}`); same-parent siblings should NOT
        be fanned out to the same tab batch (Flow may serialize them
        backend-side anyway).

    Returns one result per input job, in input order. RecaptchaError
    from any tab propagates out so the orchestrator can wipe+rewarm.
    """
    if not jobs:
        return []
    logger.info(
        "multitab dispatch: %d ops, types=%s",
        len(jobs),
        [j.get("type") for j in jobs],
    )
    t0 = time.time()

    # Stagger tab opens by ~3s. All-at-once submission burns reCAPTCHA-v3
    # score on the shared profile fingerprint (observed live 2026-05-04
    # chain v6: 3 simultaneous L2 submits → recaptcha_v3_invisible after
    # the first L2 completed). The submit POSTs themselves still race,
    # but spreading the navigate + composer setup gives the recaptcha
    # token miner a few seconds of "natural" interaction per tab.
    async def _staggered(idx: int, j: dict) -> dict:
        if idx > 0:
            await asyncio.sleep(idx * 3.0)
        return await dispatch_op_in_new_tab(client, j)

    coros = [_staggered(i, j) for i, j in enumerate(jobs)]
    results = await asyncio.gather(*coros, return_exceptions=True)
    out: list[dict] = []
    for j, r in zip(jobs, results):
        if isinstance(r, RecaptchaError):
            raise r
        if isinstance(r, Exception):
            out.append({
                "job_id": j.get("id"),
                "status": "failed",
                "error": str(r),
            })
        else:
            out.append(r)
    logger.info(
        "multitab dispatch: %d/%d completed in %.1fs",
        sum(1 for r in out if r.get("status") == "completed"),
        len(out),
        time.time() - t0,
    )
    return out


# ---------------------------------------------------------------------------
# Per-tab L2 → L3 → ... chain dispatcher
# ---------------------------------------------------------------------------


async def _run_one_op_on_open_tab(
    real_client,
    proxy,
    *,
    op_type: str,
    parent_edit_url: str,
    parent_media_id: str,
    parent_project_url: str,
    job_spec: dict,
) -> dict:
    """Drive ONE legacy op against an already-open tab.

    Caller has navigated ``proxy.page`` to ``parent_edit_url``. We just
    invoke the legacy handler, finalize, and re-download via media URL.
    """
    op_job = {
        "id": job_spec.get("id"),
        "type": op_type,
        "edit_url": parent_edit_url,
        "media_id": parent_media_id,
        "project_url": parent_project_url,
        "profile": getattr(real_client, "profile_name", ""),
        "parent_job_id": job_spec.get("parent_job_id"),
        "job_level": job_spec.get("job_level"),
    }

    if op_type == "extend-video":
        from flow.operations.extend import extend_video
        result = await extend_video(
            proxy, op_job,
            prompt=job_spec.get("prompt", ""),
            model=job_spec.get("model", "veo-3.1-fast-lp"),
            free_mode=True,
        )
    elif op_type == "camera-move":
        from flow.operations.camera import camera_move
        result = await camera_move(
            proxy, op_job,
            direction=job_spec.get("direction", ""),
        )
    elif op_type == "insert-object":
        from flow.operations.insert import insert_object
        result = await insert_object(
            proxy, op_job,
            prompt=job_spec.get("prompt", ""),
            bbox=job_spec.get("bbox") or {},
        )
    elif op_type == "remove-object":
        from flow.operations.remove import remove_object
        result = await remove_object(
            proxy, op_job,
            bbox=job_spec.get("bbox") or {},
        )
    else:
        return {
            "job_id": job_spec.get("id"),
            "status": "failed",
            "error": f"unknown op type: {op_type}",
        }

    result.setdefault("status", "completed")
    result["job_id"] = job_spec.get("id")

    # Authoritative override: take this tab's submit-response
    # `body.media[0].name` (canonical UUID, per-tab buffer = no
    # cross-tab contamination). The legacy resolver's
    # `_media_id_events` window is polluted by sibling-thumbnail
    # prefetches in the same tab.
    canonical = _media_id_from_submit_response(proxy, op_type=op_type)
    if canonical:
        prev_mid = result.get("media_id")
        if prev_mid != canonical:
            logger.info(
                "chain authoritative: override resolved mid %s "
                "with submit-response media[0].name %s",
                (prev_mid or "")[:12], canonical[:12],
            )
        result["media_id"] = canonical

    new_mid = result.get("media_id")
    if new_mid and new_mid != parent_media_id:
        fresh_files = await _redownload_via_media_url(
            proxy, new_mid, parent_project_url,
            prefix=op_type.replace("-", "_")[:16],
        )
        if fresh_files:
            result["output_files"] = fresh_files
            logger.info(
                "chain redownload: gen %s fetched via media URL",
                new_mid[:12],
            )
    return result


def _media_id_from_submit_response(
    proxy, *, op_type: str,
) -> str | None:
    """Extract canonical new media_id from this tab's most recent submit.

    Flow's batchAsyncGenerate* responses carry a ``media`` array in the
    body alongside ``operations`` — ``media[0].name`` is the canonical
    UUID-format media_id of the new output (verified live 2026-05-04
    chain v9 by inspecting the L1 status poll FULL dump shape:
    ``{"name": "<uuid>", "projectId": ..., "mediaMetadata": {...}}``).

    For L1 text-to-video, ``operations[0].operation.name`` happens to
    equal the new media_id (UUID 8-4-4-4-12). For L2/L3 reshoot /
    extend / insert / remove, ``operations[0].operation.name`` is a
    32-hex-no-dash *operation_id* (NOT a media_id), so prefer
    ``media[0].name`` which is the actual media UUID.

    URL hints (all confirmed live):
      extend  → batchAsyncGenerateVideoExtendVideo       (chain v9, 2026-05-04)
      camera  → batchAsyncGenerateVideoReshootVideo      (chain v9, 2026-05-04; camera = reshoot)
      insert  → batchAsyncGenerateVideoObjectInsertion   (MCP probe, 2026-05-05)
      remove  → batchAsyncGenerateVideoObjectRemoval     (MCP probe, 2026-05-05)
    """
    url_hint = {
        "extend-video": "extend",
        "camera-move": "reshoot",
        "insert-object": "insert",
        "remove-object": "remove",
    }.get(op_type, "")
    responses = getattr(proxy, "_batch_responses", None) or []
    for entry in reversed(responses):
        url_l = (entry.get("url") or "").lower()
        if "batchasyncgenerate" not in url_l:
            continue
        if "batchcheckasync" in url_l or "upsample" in url_l:
            continue
        if url_hint and url_hint not in url_l:
            continue
        body = entry.get("body")
        if not isinstance(body, dict):
            continue
        # Preferred path: body.media[0].name = canonical UUID media_id.
        media = body.get("media")
        if isinstance(media, list) and media:
            first = media[0]
            if isinstance(first, dict):
                mid = first.get("name")
                if isinstance(mid, str) and mid:
                    return mid
        # Fallback (for L1): operations[0].operation.name = media_id.
        ops = body.get("operations") or []
        if isinstance(ops, list) and ops:
            first = ops[0]
            if isinstance(first, dict):
                inner = first.get("operation") or {}
                if isinstance(inner, dict):
                    name = inner.get("name") or first.get("name")
                    if isinstance(name, str) and name and "-" in name:
                        # Heuristic: real media UUIDs contain dashes;
                        # 32-hex op_ids don't.
                        return name
    return None


async def dispatch_chain_in_tab(
    real_client,
    *,
    l1_parent: dict,
    chain_ops: list[dict],
) -> list[dict]:
    """Run a vertical chain (L2 → L3 → L4 → …) sequentially in one tab.

    ``l1_parent`` provides the L1 ``edit_url``/``media_id``/``project_url``.
    ``chain_ops`` is an ordered list of op specs (extend / camera /
    insert / remove). Each op runs against the previous op's resolved
    media_id, with explicit navigation between ops (Flow does not auto-
    navigate after submit — the page stays at the parent's `/edit/{mid}`
    until we click a tile or `goto` a new URL).

    Returns one result per chain_op in order. On failure of an
    intermediate op the chain stops and remaining ops are returned as
    ``{"status": "skipped", "error": "<reason>"}``.

    The tab is opened up-front, listeners bound, and closed in a finally
    block. RecaptchaError propagates out for the orchestrator's
    wipe+rewarm path.
    """
    parent_edit_url = (
        l1_parent.get("edit_url") or l1_parent.get("parent_edit_url") or ""
    )
    parent_media_id = (
        l1_parent.get("media_id") or l1_parent.get("parent_media_id") or ""
    )
    parent_project_url = (
        l1_parent.get("project_url")
        or l1_parent.get("parent_project_url")
        or ""
    )
    if not parent_edit_url or not parent_media_id:
        return [
            {
                "job_id": op.get("id"),
                "status": "failed",
                "error": "chain dispatch: missing l1_parent edit_url/media_id",
            }
            for op in chain_ops
        ]
    if not chain_ops:
        return []

    context = real_client.context
    tab = await context.new_page()

    proxy = _TabClient(real_client, tab)
    proxy._job_id = f"chain-{(chain_ops[0].get('id') or '?')}"

    tab_handler = await _make_tab_response_handler(proxy)
    try:
        tab.on("response", tab_handler)
    except Exception as exc:
        logger.warning("chain: bind response handler failed: %s", exc)
    try:
        from flow.operations._l1_batch import install_batch_response_capture
        install_batch_response_capture(proxy)
    except Exception as exc:
        logger.warning("chain: batch capture install failed: %s", exc)

    results: list[dict] = []
    cur_edit_url = parent_edit_url
    cur_mid = parent_media_id
    try:
        for idx, op_spec in enumerate(chain_ops):
            op_type = op_spec.get("type") or ""
            logger.info(
                "chain[%s]: step %d/%d type=%s parent_mid=%s",
                proxy._job_id, idx + 1, len(chain_ops),
                op_type, cur_mid[:12],
            )
            try:
                await tab.goto(
                    cur_edit_url,
                    wait_until="domcontentloaded", timeout=30000,
                )
                await asyncio.sleep(2)
            except Exception as exc:
                err = f"goto {cur_edit_url[-40:]} failed: {exc}"
                logger.warning("chain[%s]: %s", proxy._job_id, err)
                results.append({
                    "job_id": op_spec.get("id"),
                    "status": "failed",
                    "error": err,
                })
                # Cannot navigate → cannot continue chain. Mark rest skipped.
                for rest in chain_ops[idx + 1:]:
                    results.append({
                        "job_id": rest.get("id"),
                        "status": "skipped",
                        "error": "earlier step failed nav",
                    })
                return results

            # Retry on Flow soft-failures ("Flow is experiencing high
            # demand", transient panel-not-found, etc.) — these are
            # backend capacity issues, not profile burns. recaptcha
            # still propagates out for wipe+rewarm.
            max_attempts = int(op_spec.get("max_retries", 3))
            result = None
            last_err: str | None = None
            for attempt in range(1, max_attempts + 1):
                try:
                    result = await _run_one_op_on_open_tab(
                        real_client, proxy,
                        op_type=op_type,
                        parent_edit_url=cur_edit_url,
                        parent_media_id=cur_mid,
                        parent_project_url=parent_project_url,
                        job_spec=op_spec,
                    )
                except RecaptchaError:
                    raise
                except Exception as exc:
                    last_err = str(exc)
                    logger.warning(
                        "chain[%s] step %d attempt %d/%d crashed: %s",
                        proxy._job_id, idx + 1, attempt, max_attempts, exc,
                    )
                    result = None

                if (
                    result is not None
                    and result.get("status") == "completed"
                ):
                    break
                if attempt < max_attempts:
                    err_msg = (
                        last_err
                        or (result or {}).get("error")
                        or "unknown soft-fail"
                    )
                    logger.info(
                        "chain[%s] step %d retry %d/%d after fail: %s",
                        proxy._job_id, idx + 1, attempt + 1,
                        max_attempts, err_msg[:120],
                    )
                    # Cooldown helps Flow's "high demand" path recover
                    # without burning the profile fingerprint.
                    await asyncio.sleep(8.0)
                    try:
                        await tab.goto(
                            cur_edit_url,
                            wait_until="domcontentloaded", timeout=30000,
                        )
                        await asyncio.sleep(2)
                    except Exception as nav_exc:
                        logger.warning(
                            "chain[%s] step %d retry nav failed: %s",
                            proxy._job_id, idx + 1, nav_exc,
                        )

            if result is None:
                results.append({
                    "job_id": op_spec.get("id"),
                    "status": "failed",
                    "error": last_err or "all retries crashed",
                })
                for rest in chain_ops[idx + 1:]:
                    results.append({
                        "job_id": rest.get("id"),
                        "status": "skipped",
                        "error": "earlier step crashed all retries",
                    })
                return results

            results.append(result)
            if result.get("status") != "completed":
                # Failed op (Flow capacity, recaptcha-soft, etc.)
                # Mark remaining as skipped — we don't have a child mid.
                for rest in chain_ops[idx + 1:]:
                    results.append({
                        "job_id": rest.get("id"),
                        "status": "skipped",
                        "error": (
                            f"earlier step status="
                            f"{result.get('status')}"
                        ),
                    })
                return results

            new_mid = result.get("media_id") or ""
            if not new_mid or new_mid == cur_mid:
                err = (
                    f"step {idx + 1} returned no new media_id "
                    f"(got {new_mid[:12] or '<none>'})"
                )
                logger.warning("chain[%s]: %s", proxy._job_id, err)
                for rest in chain_ops[idx + 1:]:
                    results.append({
                        "job_id": rest.get("id"),
                        "status": "skipped",
                        "error": err,
                    })
                return results

            # Set up next iteration.
            base = parent_edit_url.rsplit("/edit/", 1)[0]
            cur_edit_url = f"{base}/edit/{new_mid}"
            cur_mid = new_mid

        return results

    finally:
        try:
            await tab.close()
        except Exception:
            pass


async def batch_dispatch_chains(
    client,
    chains: list[dict],
) -> list[list[dict]]:
    """Run N vertical chains in parallel — one tab per chain.

    Each ``chain`` dict::

        {
            "l1_parent": {"edit_url", "media_id", "project_url"},
            "ops": [<op_spec>, <op_spec>, ...],
        }

    Returns ``list[list[dict]]`` — one inner list per chain, one entry
    per op in chain order. Tabs run concurrently, ops within each tab
    run sequentially.
    """
    if not chains:
        return []
    logger.info(
        "chain dispatch: %d chains, op-counts=%s",
        len(chains),
        [len(c.get("ops") or []) for c in chains],
    )
    t0 = time.time()

    async def _staggered(idx: int, ch: dict) -> list[dict]:
        if idx > 0:
            await asyncio.sleep(idx * 3.0)
        return await dispatch_chain_in_tab(
            client,
            l1_parent=ch["l1_parent"],
            chain_ops=ch.get("ops") or [],
        )

    coros = [_staggered(i, c) for i, c in enumerate(chains)]
    raw = await asyncio.gather(*coros, return_exceptions=True)
    out: list[list[dict]] = []
    for ch, r in zip(chains, raw):
        if isinstance(r, RecaptchaError):
            raise r
        if isinstance(r, Exception):
            ops = ch.get("ops") or []
            out.append([
                {
                    "job_id": op.get("id"),
                    "status": "failed",
                    "error": str(r),
                }
                for op in ops
            ])
        else:
            out.append(r)
    n_ok = sum(
        1 for chain_results in out for r in chain_results
        if r.get("status") == "completed"
    )
    n_total = sum(len(c) for c in out)
    logger.info(
        "chain dispatch: %d/%d ops completed in %.1fs",
        n_ok, n_total, time.time() - t0,
    )
    return out
