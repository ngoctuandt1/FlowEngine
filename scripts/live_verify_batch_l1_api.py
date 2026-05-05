#!/usr/bin/env python3
"""Live verify reverse-API L1 batch: 1 UI submit + N-1 API submits.

Submits N L1 t2v jobs in one HTTP POST to Flow's
``v1/video:batchAsyncGenerateVideoText`` (the URL Flow's own composer
hits, but with N entries in the ``requests`` list rather than 1).

Saves ~10-15s per submit vs UI path. Intended for fast tests + mass-gen.

Usage::

    sudo -u flowengine env LIVE_VERIFY_L1_N=5 \\
      DISPLAY=:99 ... \\
      ./.venv/bin/python scripts/live_verify_batch_l1_api.py ngoctuandt20
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


async def run(profile: str) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    log = logging.getLogger("live-verify-batch-l1-api")

    from flow.client import FlowClient
    from flow.operations._l1_batch import (
        download_l1_gen_at_tile,
        snapshot_unique_tile_ids,
        wait_for_all_l1_gens,
    )
    from flow.operations._l1_api_batch import submit_l1_batch_via_api

    n = int(os.environ.get("LIVE_VERIFY_L1_N", "5"))
    all_prompts = [
        "a red cat walking through a field of yellow flowers",
        "a blue dog running on a sandy beach at sunset",
        "a yellow bird flying over a green forest in the rain",
        "a white horse galloping across a snowy mountain pass",
        "a purple jellyfish drifting through deep ocean currents",
        "a golden eagle soaring above misty alpine peaks",
        "a black panther prowling at dusk in a moonlit jungle",
    ]
    prompts = [all_prompts[i % len(all_prompts)] for i in range(n)]

    log.info("=" * 64)
    log.info("Live verify: reverse-API L1 batch (N=%d)", n)
    log.info("Profile: %s", profile)
    log.info("=" * 64)

    t0 = time.time()
    client = FlowClient(
        profile_name=profile,
        profile_base_dir=os.environ.get(
            "CHROME_USER_DATA_DIR", "./chrome-profiles"
        ),
        download_dir=os.environ.get("FLOW_DOWNLOAD_DIR", "./downloads"),
    )
    async with client:
        client._job_id = "live-api-batch"

        try:
            submits = await submit_l1_batch_via_api(client, prompts=prompts)
        except Exception:
            log.exception("submit_l1_batch_via_api crashed")
            return 3

        log.info("submitted %d / %d", len(submits), n)
        for i, s in enumerate(submits):
            log.info("  [%d] gen=%s prompt=%.60s", i,
                     s["gen_id"][-12:], s["prompt"])
        if len(submits) < n:
            log.warning("partial submit; continuing with %d", len(submits))

        if not submits:
            return 3

        # Collective wait for all gens to complete.
        try:
            waits = await wait_for_all_l1_gens(client, submits)
        except Exception:
            log.exception("wait_for_all_l1_gens crashed")
            return 3

        # Pin tile-ids before downloads.
        page = client.page
        if "/edit/" in page.url:
            project_url = submits[0].get("project_url") or ""
            if project_url:
                await page.goto(project_url, wait_until="domcontentloaded",
                                timeout=20000)
                await asyncio.sleep(2)
        try:
            pinned = await snapshot_unique_tile_ids(page)
        except Exception:
            pinned = []
        log.info("pinned %d tiles before download", len(pinned))

        completed_indices = [i for i, w in enumerate(waits)
                             if w.get("status") == "completed"]
        n_tiles = len(completed_indices)

        results = []
        for sub_idx, sub in enumerate(submits):
            wait = waits[sub_idx]
            if wait.get("status") != "completed":
                results.append({
                    "gen_id": sub["gen_id"],
                    "media_id": None,
                    "status": "failed",
                    "error": wait.get("error"),
                    "files": [],
                })
                continue
            try:
                rank = completed_indices.index(sub_idx)
                tile_index = n_tiles - 1 - rank
                tid = pinned[tile_index] if tile_index < len(pinned) else None
                files = await download_l1_gen_at_tile(
                    client,
                    tile_index=tile_index,
                    media_id=wait["media_id"],
                    project_url=submits[0].get("project_url", ""),
                    pinned_tile_id=tid,
                )
            except Exception as exc:
                log.exception("download failed: %s", exc)
                files = []
            results.append({
                "gen_id": sub["gen_id"],
                "media_id": wait["media_id"],
                "status": "completed" if files else "failed",
                "files": files,
            })

    wall = time.time() - t0
    log.info("=" * 64)
    log.info("Wall-time: %.1fs", wall)
    completed = [r for r in results if r["status"] == "completed"]
    media_ids = [r["media_id"] for r in completed if r.get("media_id")]
    files = [f for r in completed for f in r["files"]]
    log.info("Completed: %d/%d", len(completed), n)
    log.info("Distinct media_ids: %d (want %d)", len(set(media_ids)), n)
    log.info("Distinct output files: %d (want %d)", len(set(files)), n)
    for r in results:
        log.info("  gen=%s status=%s mid=%s files=%d",
                 r["gen_id"][-12:], r["status"],
                 (r.get("media_id") or "")[:12], len(r["files"]))
    if (len(completed) == n and len(set(media_ids)) == n
            and len(set(files)) >= n):
        log.info("PASS — reverse-API L1 batch verified.")
        return 0
    if completed:
        log.warning("PARTIAL")
        return 2
    log.error("FAIL")
    return 3


def main():
    if len(sys.argv) < 2:
        print(f"usage: {sys.argv[0]} <profile>", file=sys.stderr)
        sys.exit(64)
    sys.exit(asyncio.run(run(sys.argv[1])))


if __name__ == "__main__":
    main()
