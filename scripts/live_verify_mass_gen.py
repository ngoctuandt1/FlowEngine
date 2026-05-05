#!/usr/bin/env python3
"""Live verify full mass-gen path: inflate-batch + status poll + direct download.

Auto-recovers from reCAPTCHA via wipe+rewarm (mirrors the production
worker's burn-recovery loop). Pipeline:

  1. Composer setup once + 1 user click → inflated POST with N requests.
  2. Backend returns N gen_ids in 1 response (1 high-score recaptcha
     token validates the whole batch).
  3. Poll batchCheckAsyncVideoGenerationStatus per gen_id until
     completed/failed.
  4. Download each gen's media URL via direct GET.

Usage::

    sudo -u flowengine env LIVE_VERIFY_L1_N=5 \\
      DISPLAY=:99 ... \\
      ./.venv/bin/python scripts/live_verify_mass_gen.py ngoctuandt20
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
    log = logging.getLogger("live-verify-mass-gen")

    from flow.client import FlowClient
    from flow.operations._burn_recovery import (
        prime_flow_session,
        with_recaptcha_recovery,
    )
    from flow.operations._l1_batch import (
        download_l1_gen_at_tile,
        snapshot_unique_tile_ids,
    )
    from flow.operations._l1_inflate_batch import submit_l1_batch_via_inflate
    from flow.operations._l1_status_poll import (
        download_via_url,
        poll_status_via_api,
    )

    n = int(os.environ.get("LIVE_VERIFY_L1_N", "3"))
    all_prompts = [
        "a red cat walking through a field of yellow flowers",
        "a blue dog running on a sandy beach at sunset",
        "a yellow bird flying over a green forest in the rain",
        "a white horse galloping across a snowy mountain pass",
        "a purple jellyfish drifting through deep ocean currents",
    ]
    prompts = [all_prompts[i % len(all_prompts)] for i in range(n)]
    download_dir = Path(os.environ.get("FLOW_DOWNLOAD_DIR", "./downloads"))
    download_dir.mkdir(parents=True, exist_ok=True)

    log.info("=" * 64)
    log.info("Live verify: MASS-GEN (inflate + poll + direct download)")
    log.info("Profile: %s, N=%d", profile, n)
    log.info("=" * 64)

    t0 = time.time()
    profile_base_dir = os.environ.get(
        "CHROME_USER_DATA_DIR", "./chrome-profiles"
    )

    # No pre-flight prime: launching two Chrome instances back-to-back
    # on the same FLOW_USE_BASE_PROFILE=1 directory races on cookie
    # flushes and disposes route contexts mid-fetch. submit_generate_l1
    # now drives OAuth login itself when the marketing landing CTA
    # bounces to signin/identifier — see flow/operations/_l1_batch.py.
    # The wipe+rewarm fallback in with_recaptcha_recovery still kicks
    # in if a real RecaptchaError fires.

    async def _full_pipeline() -> list[dict]:
        client = FlowClient(
            profile_name=profile,
            profile_base_dir=profile_base_dir,
            download_dir=str(download_dir),
        )
        async with client:
            client._job_id = "mass-gen"
            submits = await submit_l1_batch_via_inflate(
                client, prompts=prompts,
            )
            log.info("submitted %d / %d", len(submits), n)
            for i, s in enumerate(submits):
                log.info("  [%d] gen=%s prompt=%.55s",
                         i, s["gen_id"][-12:], s["prompt"])
            if not submits:
                return []
            gen_ids = [s["gen_id"] for s in submits]
            log.info("polling status for %d gen_ids...", len(gen_ids))
            statuses = await poll_status_via_api(
                client, gen_ids=gen_ids,
                project_id=submits[0].get("project_id") or None,
                poll_interval_sec=8.0,
                hard_timeout_sec=900.0,
            )
            log.info("status poll complete:")
            for g in gen_ids:
                s = statuses.get(g, {})
                log.info(
                    "  gen=%s status=%s mid=%s url=%s",
                    g[-12:], s.get("status"),
                    (s.get("media_id") or "")[:12],
                    "yes" if s.get("media_url") else "no",
                )
            ts = int(time.time())
            results: list[dict] = []
            for i, s in enumerate(submits):
                g = s["gen_id"]
                sst = statuses.get(g, {})
                if sst.get("status") != "completed":
                    results.append({
                        "gen_id": g,
                        "status": sst.get("status"),
                        "media_id": None,
                        "file": None,
                        "error": sst.get("error") or "no media url",
                    })
                    continue
                url = sst.get("media_url")
                saved: str | None = None
                if url:
                    out_path = str(download_dir / f"mass_{ts}_{i}.mp4")
                    saved = await download_via_url(
                        client, url=url, out_path=out_path,
                    )
                else:
                    # Fallback: drive the UI tile-pinned upscale path
                    # (Phase 1 verified). Status endpoint doesn't expose
                    # the download URL — only state — so we route through
                    # Flow's project-tile + Download menu.
                    if "/edit/" in client.page.url:
                        try:
                            await client.page.goto(
                                submits[0]["project_url"],
                                wait_until="domcontentloaded",
                                timeout=20000,
                            )
                            await asyncio.sleep(2)
                        except Exception:
                            pass
                    try:
                        pinned = await snapshot_unique_tile_ids(client.page)
                    except Exception:
                        pinned = []
                    pinned_id = pinned[i] if i < len(pinned) else None
                    try:
                        files = await download_l1_gen_at_tile(
                            client,
                            tile_index=i,
                            media_id=sst.get("media_id") or g,
                            project_url=submits[0].get("project_url", ""),
                            pinned_tile_id=pinned_id,
                        )
                        saved = files[0] if files else None
                    except Exception as exc:
                        log.warning("UI tile download failed for [%d]: %s", i, exc)
                        saved = None
                results.append({
                    "gen_id": g,
                    "status": "completed" if saved else "failed",
                    "media_id": sst.get("media_id"),
                    "file": saved,
                    "error": None if saved else "download failed (no URL + UI fallback failed)",
                })
            return results

    try:
        results = await with_recaptcha_recovery(
            profile,
            _full_pipeline,
            attempts=2,
        )
    except Exception:
        log.exception("mass-gen pipeline crashed")
        return 3

    wall = time.time() - t0
    log.info("=" * 64)
    log.info("Wall-time: %.1fs", wall)
    if not results:
        log.error("FAIL — no results")
        return 3
    completed = [r for r in results if r["status"] == "completed"]
    media_ids = [r["media_id"] for r in completed if r.get("media_id")]
    files = [r["file"] for r in completed if r.get("file")]
    log.info("Completed: %d/%d", len(completed), n)
    log.info("Distinct media_ids: %d (want %d)", len(set(media_ids)), n)
    log.info("Distinct output files: %d (want %d)", len(set(files)), n)
    for r in results:
        log.info("  gen=%s status=%s mid=%s file=%s err=%s",
                 r["gen_id"][-12:], r["status"],
                 (r.get("media_id") or "")[:12],
                 (r.get("file") or "").rsplit("/", 1)[-1],
                 (r.get("error") or "")[:60])
    if (
        len(completed) == n
        and len(set(media_ids)) == n
        and len(set(files)) >= n
    ):
        log.info("PASS — mass-gen verified.")
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
