"""Batch dispatch orchestration — 1 Chrome → N successive submits.

Public surface:

* `batch_dispatch_l1_same_project(client, l1_jobs)` — Phase 1 (implemented).
  Submits N text-to-video jobs into one freshly-created project, parallel-polls
  each generation by its captured gen_id, downloads each in turn.

* `batch_dispatch_l2_siblings(...)` — Phase 2 (implemented).
* `batch_dispatch_l3_siblings(...)` — Phase 3 (implemented; delegates to L2).

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
    download_l1_gen_at_tile,
    snapshot_unique_tile_ids,
    submit_generate_l1,
    wait_for_all_l1_gens,
    wait_for_l1_gen,
)
from flow.operations._l2_batch import (
    build_l2_result,
    download_l2_gen_at_tile,
    submit_camera,
    submit_extend,
    submit_insert,
    submit_remove,
    wait_for_all_l2_gens,
)
from flow.model_selector import DEFAULT_MODEL, _is_paid_model
from flow.recaptcha import RecaptchaError

logger = logging.getLogger(__name__)


def _model_and_free_mode(job: dict) -> tuple[str, bool]:
    model = job.get("model") or DEFAULT_MODEL
    return model, not _is_paid_model(model)


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
            model, free_mode = _model_and_free_mode(job)
            sub = await submit_generate_l1(
                client, job, project_already_open=(idx > 0),
                model=model, free_mode=free_mode,
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
    # Collective wait: under Flow's modern ``v1/video:batchAsyncGenerate``
    # endpoint the per-operation polling responses are not captured by
    # ``flow.client._on_response`` — so we cannot filter completions by
    # gen_id. Wait for N distinct new media_ids and assign them to
    # successful submits in submission order (FIFO assumption holds for
    # L1 t2v on the same project).
    successful_subs = [rec["submit"] for rec in submits if rec.get("submit") and not rec.get("error")]
    if successful_subs:
        try:
            collective = await wait_for_all_l1_gens(client, successful_subs)
        except RecaptchaError:
            raise
        except Exception as exc:
            logger.exception("L1 batch collective wait failed: %s", exc)
            collective = [
                {"status": "failed", "error": str(exc), "media_id": None,
                 "media_ids": []}
                for _ in successful_subs
            ]
    else:
        collective = []

    waits: list[dict[str, Any]] = []
    coll_iter = iter(collective)
    for rec in submits:
        if rec.get("submit") and not rec.get("error"):
            try:
                waits.append(next(coll_iter))
            except StopIteration:
                waits.append({"status": "failed", "error": "wait result missing",
                              "media_id": None, "media_ids": []})
        else:
            waits.append({"status": "failed",
                          "error": rec.get("error") or "no submit",
                          "media_id": None, "media_ids": []})

    # ----------------------------------------------------------------- C
    # Tile-index download mapping. Flow's project view orders tiles by
    # creation timestamp DESC (newest first), but the legacy upscale path
    # always clicks ``[data-tile-id^=fe_id_].first`` which would download
    # the same (most-recent) tile N times — verified contamination on
    # 2026-05-04 live verify (3 batched downloads → identical md5).
    #
    # Map per-submit to tile_index. Only successful submits (with a
    # completed wait + resolved media_id) earn a tile to download.
    completed_indices = [
        i for i, (rec, wait) in enumerate(zip(submits, waits))
        if (
            rec.get("submit") and not rec.get("error")
            and wait.get("status") == "completed"
            and wait.get("media_id")
        )
    ]
    n_tiles = len(completed_indices)
    # Reverse mapping: oldest completed submit → highest tile_index.
    submit_to_tile: dict[int, int] = {}
    for rank, submit_idx in enumerate(completed_indices):
        submit_to_tile[submit_idx] = n_tiles - 1 - rank

    # Snapshot data-tile-ids ONCE before any download. Downloads upscale
    # tiles which Flow promotes to tile_index=0 mid-batch (live evidence:
    # round 11 had two tiles with identical md5 because tile order shifted
    # between the 2nd and 3rd download). Pin id-by-tile_index here so
    # download_l1_gen_at_tile bypasses the live-list resolve.
    pinned_tile_ids: list[str] = []
    if n_tiles > 0:
        try:
            page = client.page
            if "/project/" not in page.url or "/edit/" in page.url:
                if project_url:
                    await page.goto(project_url, wait_until="domcontentloaded",
                                    timeout=20000)
                    await asyncio.sleep(2)
            pinned_tile_ids = await snapshot_unique_tile_ids(page)
            logger.info(
                "L1 batch tile snapshot: %d unique tiles pinned for download",
                len(pinned_tile_ids),
            )
        except Exception as exc:
            logger.warning("L1 batch tile snapshot failed: %s", exc)
            pinned_tile_ids = []

    profile = getattr(client, "profile_name", "") or ""
    results: list[dict[str, Any]] = []
    for rec, wait in zip(submits, waits):
        job = rec["job"]
        sub = rec.get("submit") or {}

        # Each successful submit's result inherits the project_url from the
        # first submit (collective wait erases per-submit project context).
        if sub and not sub.get("project_url") and project_url:
            sub["project_url"] = project_url

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
            submit_index = submits.index(rec)
            tile_index = submit_to_tile.get(submit_index)
            dl_meta = {
                "job_type": job.get("type", "text-to-video"),
                "prompt": job.get("prompt", ""),
                "media_id": media_id,
                "project_url": project_url or "",
                "profile": profile or "",
            }
            if tile_index is None:
                files = await download_l1_gen(client, media_id, metadata=dl_meta)
            else:
                pinned = (
                    pinned_tile_ids[tile_index]
                    if tile_index < len(pinned_tile_ids) else None
                )
                files = await download_l1_gen_at_tile(
                    client,
                    tile_index=tile_index,
                    media_id=media_id,
                    project_url=project_url,
                    pinned_tile_id=pinned,
                    metadata=dl_meta,
                )
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
# Phase 2 / 3 orchestrators.
# ---------------------------------------------------------------------------


async def batch_dispatch_l2_siblings(
    client,
    parent_edit_url: str,
    parent_media_id: str,
    l2_jobs: list[dict],
) -> list[dict[str, Any]]:
    """Submit N L2 ops back-to-back on a shared parent L1, return results.

    PRD §4. Caller guarantees:
      * all jobs have ``job_level == 2``
      * all share the same ``parent_job_id`` (= the L1)
      * all share ``profile`` = ``client.profile_name``
      * ``parent_edit_url`` and ``parent_media_id`` come from the parent L1

    Phase A — sequential submits dispatched by ``type`` (extend / camera /
    insert / remove). Each ``submit_X`` clicks its own mode panel; the
    first submit lands on /edit/ and subsequent ones reuse the same page.
    Phase B — collective wait for N new media_ids via
    :func:`wait_for_all_l2_gens` (excluding parent's mid).
    Phase C — sequential downloads keyed by tile_index (oldest submit →
    highest index, mirroring L1 batch's mapping).
    """
    if not l2_jobs:
        return []

    from flow.navigation import detect_locale, extract_project_id

    # Synthetic "parent job" object for navigate_to_edit. We only need
    # edit_url/project_url/media_id keys; the rest of the fields are unused.
    parent_synth = {
        "edit_url": parent_edit_url,
        "media_id": parent_media_id,
        "project_url": "",  # navigate_to_edit happily uses edit_url alone
    }

    # ----------------------------------------------------------------- A
    submits: list[dict[str, Any]] = []
    project_id = ""
    locale = ""
    for idx, job in enumerate(l2_jobs):
        try:
            sub = await _dispatch_l2_submit(
                client, job, parent_synth,
                first=(idx == 0),
            )
        except RecaptchaError:
            raise
        except Exception as exc:
            logger.exception(
                "L2 batch submit %d/%d failed: %s",
                idx + 1, len(l2_jobs), exc,
            )
            submits.append({"job": job, "submit": None, "error": str(exc)})
            continue
        submits.append({"job": job, "submit": sub, "error": None})
        logger.info(
            "L2 batch submit %d/%d ok: type=%s gen=%s",
            idx + 1, len(l2_jobs), sub["op_type"], sub["gen_id"][-12:],
        )

    # Page is now on /edit/{parent or latest}; harvest project_id + locale.
    page_url = client.page.url
    project_id = extract_project_id(page_url) or ""
    locale = detect_locale(page_url)
    project_url = (
        f"https://labs.google/fx/{(locale + '/') if locale else ''}tools/flow"
        f"/project/{project_id}" if project_id else ""
    )

    # ----------------------------------------------------------------- B
    successful = [
        rec["submit"] for rec in submits
        if rec.get("submit") and not rec.get("error")
    ]
    if successful:
        try:
            collective = await wait_for_all_l2_gens(
                client, successful, parent_media_id=parent_media_id,
            )
        except RecaptchaError:
            raise
        except Exception as exc:
            logger.exception("L2 batch collective wait failed: %s", exc)
            collective = [
                {"status": "failed", "error": str(exc),
                 "media_id": None, "media_ids": []}
                for _ in successful
            ]
    else:
        collective = []

    waits: list[dict[str, Any]] = []
    coll_iter = iter(collective)
    for rec in submits:
        if rec.get("submit") and not rec.get("error"):
            try:
                waits.append(next(coll_iter))
            except StopIteration:
                waits.append({"status": "failed", "error": "wait result missing",
                              "media_id": None, "media_ids": []})
        else:
            waits.append({"status": "failed",
                          "error": rec.get("error") or "no submit",
                          "media_id": None, "media_ids": []})

    # ----------------------------------------------------------------- C
    completed_indices = [
        i for i, (rec, wait) in enumerate(zip(submits, waits))
        if (
            rec.get("submit") and not rec.get("error")
            and wait.get("status") == "completed"
            and wait.get("media_id")
        )
    ]
    n_tiles = len(completed_indices)
    submit_to_tile: dict[int, int] = {}
    for rank, submit_idx in enumerate(completed_indices):
        submit_to_tile[submit_idx] = n_tiles - 1 - rank

    profile = getattr(client, "profile_name", "") or ""
    results: list[dict[str, Any]] = []
    for i, (rec, wait) in enumerate(zip(submits, waits)):
        job = rec["job"]
        sub = rec.get("submit") or {}

        common = dict(
            job=job, submit=sub, profile=profile,
            project_url=project_url, project_id=project_id, locale=locale,
        )

        if rec.get("error"):
            results.append(_attach_job_id(job, build_l2_result(
                wait={}, output_files=None, error=rec["error"], **common,
            )))
            continue
        if wait.get("status") != "completed":
            results.append(_attach_job_id(job, build_l2_result(
                wait=wait, output_files=None,
                error=wait.get("error") or "wait failed", **common,
            )))
            continue

        media_id = wait.get("media_id")
        if not media_id:
            results.append(_attach_job_id(job, build_l2_result(
                wait=wait, output_files=None,
                error="no media_id resolved post-completion", **common,
            )))
            continue

        try:
            tile_index = submit_to_tile.get(i)
            edit_url_val = (
                f"https://labs.google/fx/{(locale + '/') if locale else ''}"
                f"tools/flow/project/{project_id}/edit/{media_id}"
                if project_id else parent_edit_url
            )
            files = await download_l2_gen_at_tile(
                client,
                tile_index=tile_index if tile_index is not None else 0,
                media_id=media_id,
                edit_url=edit_url_val,
                prefix=_prefix_for_l2(job),
                metadata={
                    "job_type": job.get("type", ""),
                    "prompt": job.get("prompt", ""),
                    "media_id": media_id,
                    "project_url": common.get("project_url", ""),
                    "profile": common.get("profile", ""),
                },
            )
        except Exception as exc:
            logger.exception("L2 batch download failed for mid=%s: %s",
                             media_id[:12], exc)
            results.append(_attach_job_id(job, build_l2_result(
                wait=wait, output_files=None,
                error=f"download: {exc}", **common,
            )))
            continue

        results.append(_attach_job_id(job, build_l2_result(
            wait=wait, output_files=files, **common,
        )))

    return results


def _prefix_for_l2(job: dict) -> str:
    return {
        "extend-video": "ext",
        "camera-move": "cam",
        "insert-object": "ins",
        "remove-object": "rm",
    }.get(job.get("type") or "", "l2")


async def _dispatch_l2_submit(client, job: dict, parent_synth: dict, *,
                              first: bool) -> dict:
    """Route one job to its op-specific submit_X by ``job['type']``."""
    op = (job.get("type") or "").strip()
    # ``panel_already_open`` is False for every submit — between siblings
    # the prior op's panel may have closed (Flow auto-resets on submit).
    # Each submit_X re-clicks its mode button idempotently.
    panel_open = False
    if op == "extend-video":
        model, free_mode = _model_and_free_mode(job)
        return await submit_extend(
            client, parent_synth, prompt=job.get("prompt") or "",
            panel_already_open=panel_open,
            model=model, free_mode=free_mode,
        )
    if op == "camera-move":
        direction = job.get("direction") or "Dolly in"
        return await submit_camera(
            client, parent_synth, direction,
            panel_already_open=panel_open,
        )
    if op == "insert-object":
        return await submit_insert(
            client, parent_synth, prompt=job.get("prompt") or "",
            bbox=job.get("bbox"), panel_already_open=panel_open,
        )
    if op == "remove-object":
        return await submit_remove(
            client, parent_synth, bbox=job.get("bbox"),
            panel_already_open=panel_open,
        )
    raise RuntimeError(f"Unsupported L2 batch op type: {op!r}")


async def batch_dispatch_l3_siblings(
    client,
    parent_edit_url: str,
    parent_media_id: str,
    l3_jobs: list[dict],
) -> list[dict[str, Any]]:
    """Submit N L3+ ops back-to-back on a shared parent L2/L3, return results.

    PRD §5. Caller guarantees:
      * all jobs have ``job_level >= 3``
      * all share the same ``parent_job_id`` (= the direct L2 / L3 parent)
      * all share ``profile`` = ``client.profile_name``
      * ``parent_edit_url`` and ``parent_media_id`` come from that parent

    The per-op submit/wait/download primitives don't care which level their
    inputs are at — they operate on ``parent_edit_url + parent_media_id``.
    Phase 3 therefore delegates to :func:`batch_dispatch_l2_siblings`. The
    distinct entry point keeps the dispatcher / claim-loop wiring honest
    (different gate, different DB filter) without duplicating orchestrator
    code.
    """
    return await batch_dispatch_l2_siblings(
        client, parent_edit_url, parent_media_id, l3_jobs,
    )
