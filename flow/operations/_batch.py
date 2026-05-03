"""Batch dispatch orchestration — 1 Chrome → N successive submits.

Public surface:

* `batch_dispatch_l1_same_project(client, l1_jobs)` — Phase 1 (implemented).
  Submits N text-to-video jobs into one freshly-created project, parallel-polls
  each generation by its captured gen_id, downloads each in turn.

* `batch_dispatch_l2_siblings(...)` — Phase 2 placeholder.
* `batch_dispatch_l3_siblings(...)` — Phase 3 placeholder.

All entry points share the same Phase A/B/C structure:

    A. Sequential submit (cannot parallelize: composer is mutated per submit).
    B. Parallel wait via `asyncio.gather` over per-submit gen_ids.
    C. Sequential download (CDP attach contention; downloads are short).

Design invariants (see PRD §0.4):

* gen_id captured into per-submit dict immediately; never re-read from
  `client._gen_id` later.
* Network calls window per submit isolates `_calls` slicing and
  `_media_id_events` time-windowing.
* Per-job result dict is always `{job_id, status, ...}` so the worker
  can persist failures and successes uniformly.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from flow.operations._l1_batch import (
    build_l1_result,
    download_l1_gen,
    submit_generate_l1,
    wait_for_l1_gen,
)
from flow.recaptcha import RecaptchaError

logger = logging.getLogger(__name__)


async def batch_dispatch_l1_same_project(
    client,
    l1_jobs: list[dict],
) -> list[dict[str, Any]]:
    """Submit N L1 text-to-video jobs into a fresh project, return results.

    Per PRD Phase 1 §3.2:

      * jobs[0] navigates homepage → "+ New project" → composer.
      * jobs[1..N-1] reuse the now-mounted /project/{id} composer.
      * All N waits run concurrently via `asyncio.gather`.
      * Downloads run sequentially per completed gen.

    Result list is in the same order as input. Each result carries a
    `job_id` and the canonical job-update payload (see `build_l1_result`).

    A `RecaptchaError` raised mid-batch propagates immediately so the
    dispatcher can mark the profile burned. Any other per-job failure is
    recorded into that job's result and the rest of the batch continues.
    """
    if not l1_jobs:
        return []

    # ----------------------------------------------------------------- A
    submits: list[dict[str, Any]] = []
    project_url = ""
    for idx, job in enumerate(l1_jobs):
        try:
            sub = await submit_generate_l1(
                client, job, project_already_open=(idx > 0),
            )
        except RecaptchaError:
            raise
        except Exception as exc:
            logger.exception(
                "L1 batch submit %d/%d failed: %s",
                idx + 1, len(l1_jobs), exc,
            )
            submits.append({"job": job, "submit": None, "error": str(exc)})
            # If the FIRST submit fails the rest cannot proceed (no project to
            # share). Mark all remaining as not-attempted and return early.
            if idx == 0:
                for j in l1_jobs[1:]:
                    submits.append({"job": j, "submit": None,
                                    "error": "skipped: first submit failed"})
                return _finalize_failed_batch(client, submits)
            continue
        if idx == 0:
            project_url = sub["project_url"]
        else:
            sub["project_url"] = project_url or sub.get("project_url", "")
        submits.append({"job": job, "submit": sub, "error": None})
        logger.info(
            "L1 batch submit %d/%d ok: gen=%s",
            idx + 1, len(l1_jobs), sub["gen_id"][-12:],
        )

    # ----------------------------------------------------------------- B
    async def _one_wait(rec: dict[str, Any]) -> dict[str, Any]:
        if rec.get("error") or not rec.get("submit"):
            return {"status": "failed", "error": rec.get("error") or "no submit"}
        sub = rec["submit"]
        try:
            return await wait_for_l1_gen(
                client,
                sub["gen_id"],
                calls_before=sub["calls_before"],
                submit_ts=sub["submit_ts"],
            )
        except RecaptchaError:
            raise
        except Exception as exc:
            logger.exception("L1 batch wait failed for gen=%s: %s",
                             sub["gen_id"][-12:], exc)
            return {"status": "failed", "error": str(exc)}

    waits = await asyncio.gather(
        *[_one_wait(rec) for rec in submits],
        return_exceptions=False,
    )

    # ----------------------------------------------------------------- C
    profile = getattr(client, "profile_name", "") or ""
    results: list[dict[str, Any]] = []
    for rec, wait in zip(submits, waits):
        job = rec["job"]
        sub = rec.get("submit") or {}

        if rec.get("error"):
            results.append(_attach_job_id(job, build_l1_result(
                submit=sub, wait={}, output_files=None, profile=profile,
                error=rec["error"],
            )))
            continue

        if wait.get("status") != "completed":
            results.append(_attach_job_id(job, build_l1_result(
                submit=sub, wait=wait, output_files=None, profile=profile,
                error=wait.get("error") or "wait failed",
            )))
            continue

        media_id = wait.get("media_id")
        if not media_id:
            results.append(_attach_job_id(job, build_l1_result(
                submit=sub, wait=wait, output_files=None, profile=profile,
                error="no media_id resolved post-completion",
            )))
            continue

        try:
            files = await download_l1_gen(client, media_id)
        except Exception as exc:
            logger.exception("L1 batch download failed for mid=%s: %s",
                             media_id[:12], exc)
            results.append(_attach_job_id(job, build_l1_result(
                submit=sub, wait=wait, output_files=None, profile=profile,
                error=f"download: {exc}",
            )))
            continue

        results.append(_attach_job_id(job, build_l1_result(
            submit=sub, wait=wait, output_files=files, profile=profile,
        )))

    return results


def _attach_job_id(job: dict, payload: dict) -> dict:
    return {"job_id": job.get("id") or job.get("job_id"), **payload}


def _finalize_failed_batch(client, submits: list[dict]) -> list[dict]:
    profile = getattr(client, "profile_name", "") or ""
    out = []
    for rec in submits:
        out.append(_attach_job_id(rec["job"], build_l1_result(
            submit=(rec.get("submit") or {}),
            wait={}, output_files=None, profile=profile,
            error=rec.get("error") or "batch aborted",
        )))
    return out


# ---------------------------------------------------------------------------
# Phase 2 / 3 placeholders — keep import surface stable.
# ---------------------------------------------------------------------------


async def batch_dispatch_l2_siblings(*args, **kwargs) -> list[dict]:
    raise NotImplementedError("Phase 2 not yet implemented")


async def batch_dispatch_l3_siblings(*args, **kwargs) -> list[dict]:
    raise NotImplementedError("Phase 3 not yet implemented")
