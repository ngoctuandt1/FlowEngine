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

    Forwards every other attribute access to the underlying real
    FlowClient so capture buffers + auth state stay shared.
    """

    def __init__(self, real_client, tab_page) -> None:
        self._real = real_client
        self.page = tab_page
        # Some helpers read client.profile_name / _job_id directly.
        # `__getattr__` covers them by delegation; explicit attrs here
        # exist only to satisfy IDE autocompletion.

    def __getattr__(self, name: str) -> Any:
        # Falls through for anything not on the proxy itself
        # (profile_name, _calls, _video_urls, _media_id_events, etc.).
        return getattr(self._real, name)

    def clear_captures(self) -> None:
        # Per-tab caller may invoke this — delegate to real client so
        # the shared buffer truly resets.
        self._real.clear_captures()


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
        try:
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

        proxy = _TabClient(real_client, tab)
        # Tag the proxy with the per-tab job_id so any failure-capture
        # call writes useful filenames.
        proxy._job_id = job.get("id") or job.get("type")

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
    coros = [dispatch_op_in_new_tab(client, j) for j in jobs]
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
