#!/usr/bin/env python3
"""Live verify Phase 2 batch dispatch: 3 L2 siblings on one L1 parent.

Standalone script — bypasses HTTP server + systemd. The L1 parent must
already exist (created by an earlier text-to-video run, or supplied via
its `edit_url + media_id` on the command line).

Usage on debian-root::

    cd <worktree>
    sudo -u flowengine env \\
      DISPLAY=:99 \\
      CHROME_USER_DATA_DIR=/opt/flowengine/chrome-profiles \\
      FLOW_DOWNLOAD_DIR=/opt/flowengine-batch/downloads \\
      FLOW_REAL_CHROME=1 FLOW_USE_BASE_PROFILE=1 \\
      FLOW_WARM_CHROME_PATH=/usr/bin/google-chrome \\
      ./.venv/bin/python scripts/live_verify_batch_l2.py \\
        ngoctuandt20 <parent_edit_url> <parent_media_id>

Exit codes:
  0 — all L2 siblings completed with distinct media_ids + distinct files
  2 — partial
  3 — total failure
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def _setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )


async def run(profile: str, parent_edit_url: str, parent_media_id: str) -> int:
    _setup_logging()
    log = logging.getLogger("live-verify-batch-l2")

    from flow.client import FlowClient
    from flow.operations._batch import batch_dispatch_l2_siblings

    download_dir = os.environ.get("FLOW_DOWNLOAD_DIR", "./downloads")
    profile_base_dir = os.environ.get("CHROME_USER_DATA_DIR", "./chrome-profiles")

    log.info("=" * 64)
    log.info("Live verify: Phase 2 batch L2 siblings (3 ops on one L1 parent)")
    log.info("Profile: %s", profile)
    log.info("Parent edit_url: %s", parent_edit_url[:120])
    log.info("Parent media_id: %s", parent_media_id)
    log.info("=" * 64)

    ts = int(time.time())
    parent_job_id = f"live-verify-l1-{ts}"
    l2_jobs = [
        {
            "id": f"live-l2-extend-{ts}",
            "type": "extend-video",
            "prompt": "the camera pulls back to reveal a wide horizon",
            "profile": profile,
            "job_level": 2,
            "parent_job_id": parent_job_id,
            "edit_url": parent_edit_url,
            "media_id": parent_media_id,
        },
        {
            "id": f"live-l2-camera-dolly-{ts}",
            "type": "camera-move",
            "direction": "Dolly in",
            "profile": profile,
            "job_level": 2,
            "parent_job_id": parent_job_id,
            "edit_url": parent_edit_url,
            "media_id": parent_media_id,
        },
        {
            "id": f"live-l2-camera-orbit-{ts}",
            "type": "camera-move",
            "direction": "Orbit left",
            "profile": profile,
            "job_level": 2,
            "parent_job_id": parent_job_id,
            "edit_url": parent_edit_url,
            "media_id": parent_media_id,
        },
    ]

    log.info("Submitting %d L2 jobs:", len(l2_jobs))
    for j in l2_jobs:
        log.info("  - %s | type=%s detail=%r",
                 j["id"], j["type"],
                 j.get("prompt") or j.get("direction"))

    t0 = time.time()
    client = FlowClient(
        profile_name=profile,
        profile_base_dir=profile_base_dir,
        download_dir=download_dir,
    )
    async with client:
        client._job_id = l2_jobs[0]["id"]
        try:
            results = await batch_dispatch_l2_siblings(
                client, parent_edit_url, parent_media_id, l2_jobs,
            )
        except Exception:
            log.exception("Batch L2 crashed")
            return 3
    wall = time.time() - t0

    log.info("=" * 64)
    log.info("Wall-time: %.1fs", wall)
    completed = [r for r in results if r.get("status") == "completed"]
    failed = [r for r in results if r.get("status") != "completed"]
    media_ids = [r.get("media_id") for r in completed if r.get("media_id")]
    files = []
    for r in completed:
        files.extend(r.get("output_files") or [])

    log.info("Completed: %d/%d", len(completed), len(l2_jobs))
    log.info("Failed:    %d/%d", len(failed), len(l2_jobs))
    log.info("Distinct media_ids: %d (want %d)",
             len(set(media_ids)), len(l2_jobs))
    log.info("Distinct output files: %d (want %d)",
             len(set(files)), len(l2_jobs))
    for r in results:
        log.info(
            "  job=%s status=%s gen=%s mid=%s files=%s err=%s",
            r.get("job_id"),
            r.get("status"),
            (r.get("generation_id") or "")[-12:],
            (r.get("media_id") or "")[:12],
            len(r.get("output_files") or []),
            (r.get("error") or "")[:60],
        )

    if (
        len(completed) == len(l2_jobs)
        and len(set(media_ids)) == len(l2_jobs)
        and len(set(files)) >= len(l2_jobs)
    ):
        log.info("PASS — Phase 2 L2 batch dispatch verified.")
        return 0
    if completed:
        log.warning("PARTIAL — some L2 jobs failed.")
        return 2
    log.error("FAIL — no L2 completions.")
    return 3


def main():
    if len(sys.argv) < 4:
        print(
            f"usage: {sys.argv[0]} <profile> <parent_edit_url> <parent_media_id>",
            file=sys.stderr,
        )
        sys.exit(64)
    sys.exit(asyncio.run(run(sys.argv[1], sys.argv[2], sys.argv[3])))


if __name__ == "__main__":
    main()
