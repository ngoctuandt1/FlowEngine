#!/usr/bin/env python3
"""Live verify Phase 1 batch dispatch: 3 L1 t2v same-project.

Standalone script — bypasses HTTP server + systemd. Exercises the batch
primitives directly so we can verify metadata isolation against a live
Google Flow without disturbing the public ai.hassio.io.vn deploy.

Usage on debian-root::

    cd <new-worktree>
    sudo -u flowengine env \\
      CHROME_USER_DATA_DIR=/opt/flowengine/chrome-profiles \\
      FLOW_DOWNLOAD_DIR=/opt/flowengine/downloads \\
      FLOW_BATCH_DISPATCH=1 \\
      FLOW_USE_BASE_PROFILE=1 \\
      ./.venv/bin/python scripts/live_verify_batch_l1.py ngoctuandt20

Exit codes:
  0 — all 3 L1 completed with distinct media_ids + distinct files
  2 — partial (some failed); check stdout report
  3 — total failure / setup error
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


async def run(profile: str) -> int:
    _setup_logging()
    log = logging.getLogger("live-verify-batch")

    from flow.operations._batch import batch_dispatch_l1_same_project
    from flow.client import FlowClient

    download_dir = os.environ.get("FLOW_DOWNLOAD_DIR", "./downloads")
    profile_base_dir = os.environ.get(
        "CHROME_USER_DATA_DIR", "./chrome-profiles"
    )

    log.info("=" * 64)
    log.info("Live verify: Phase 1 batch L1 (3 t2v same-project)")
    log.info("Profile: %s", profile)
    log.info("Profile base: %s", profile_base_dir)
    log.info("Download dir: %s", download_dir)
    log.info("=" * 64)

    n = int(os.environ.get("LIVE_VERIFY_L1_N", "3"))
    all_prompts = [
        "a red cat walking through a field of yellow flowers",
        "a blue dog running on a sandy beach at sunset",
        "a yellow bird flying over a green forest in the rain",
        "a white horse galloping across a snowy mountain pass",
        "a purple jellyfish drifting through deep ocean currents",
        "a golden eagle soaring above misty alpine peaks",
        "a black panther prowling at dusk in a moonlit jungle",
    ]
    jobs = [
        {
            "id": f"live-verify-{int(time.time())}-{i}",
            "type": "text-to-video",
            "prompt": all_prompts[i % len(all_prompts)],
            "profile": profile,
            "job_level": 1,
            "aspect_ratio": "16:9",
        }
        for i in range(n)
    ]
    log.info("Submitting %d L1 jobs:", len(jobs))
    for j in jobs:
        log.info("  - %s | %.50s", j["id"], j["prompt"])

    t0 = time.time()
    client = FlowClient(
        profile_name=profile,
        profile_base_dir=profile_base_dir,
        download_dir=download_dir,
    )
    async with client:
        client._job_id = jobs[0]["id"]
        try:
            results = await batch_dispatch_l1_same_project(client, jobs)
        except Exception:
            log.exception("Batch crashed")
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

    log.info("Completed: %d/%d", len(completed), len(jobs))
    log.info("Failed:    %d/%d", len(failed), len(jobs))
    log.info("Distinct media_ids: %d (want %d)",
             len(set(media_ids)), len(jobs))
    log.info("Distinct output files: %d (want %d)",
             len(set(files)), len(jobs))
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
        len(completed) == len(jobs)
        and len(set(media_ids)) == len(jobs)
        and len(set(files)) >= len(jobs)
    ):
        log.info("PASS — Phase 1 batch dispatch verified.")
        return 0
    if completed:
        log.warning("PARTIAL — some jobs failed; see report above.")
        return 2
    log.error("FAIL — no completions.")
    return 3


def main():
    if len(sys.argv) < 2:
        print(f"usage: {sys.argv[0]} <profile_name>", file=sys.stderr)
        sys.exit(64)
    sys.exit(asyncio.run(run(sys.argv[1])))


if __name__ == "__main__":
    main()
