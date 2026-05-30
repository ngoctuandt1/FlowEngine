#!/usr/bin/env python3
"""R24: t2v L1 + extend L2 live test — validates wait.py Method 4.

Usage on debian:
    cd /opt/flowengine
    DISPLAY=:99 CHROME_USER_DATA_DIR=/opt/flowengine/chrome-profiles \
    FLOW_REAL_CHROME=1 FLOW_USE_BASE_PROFILE=1 \
    python3 scripts/run_r24_extend_chain.py [profile] [project_url]

If project_url supplied, skip L1 and just run extend on that project
(requires --media_id too, see argparse below).

Exit codes: 0=PASS, 1=FAIL
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("r24")


async def run(profile: str, project_url: str | None, media_id: str | None) -> int:
    from flow.client import FlowClient
    from flow.operations.generate import text_to_video
    from flow.operations.extend import extend_video

    os.environ.setdefault("CHROME_USER_DATA_DIR", "./chrome-profiles")
    os.environ.setdefault("FLOW_REAL_CHROME", "1")
    os.environ.setdefault("FLOW_USE_BASE_PROFILE", "1")

    async with FlowClient(profile) as client:
        # --- L1: text-to-video ---
        if project_url and media_id:
            log.info("[R24] Skipping L1, using supplied project_url + media_id")
            l1_project_url = project_url
            l1_media_id = media_id
        else:
            log.info("[R24] Step 1: text-to-video L1 ...")
            job_l1 = {
                "id": "r24-l1",
                "type": "text-to-video",
                "job_level": 1,
                "profile": profile,
                "project_url": project_url or "",
                "prompt": "A calm river flowing through a forest at sunrise",
                "parent_job_id": None,
                "chain_id": "r24",
            }
            try:
                r1 = await text_to_video(
                    client,
                    prompt=job_l1["prompt"],
                    free_mode=True,
                )
                log.info("[R24] L1 raw result keys: %s", list(r1.keys()))
                l1_project_url = r1.get("project_url") or ""
                l1_media_id = r1.get("media_id") or ""
                log.info(
                    "[R24] L1 done: project_url=%s  media_id=%s",
                    l1_project_url[:60],
                    l1_media_id[:40],
                )
            except Exception as exc:
                log.error("[R24] L1 FAILED: %s", exc)
                return 1

        if not l1_media_id:
            log.error("[R24] No media_id from L1 — cannot proceed to L2")
            return 1

        # --- L2: extend-video ---
        log.info("[R24] Step 2: extend-video L2 ...")
        edit_url = f"{l1_project_url.rstrip('/')}/edit/{l1_media_id}"
        job_l2 = {
            "id": "r24-l2",
            "type": "extend-video",
            "job_level": 2,
            "profile": profile,
            "project_url": l1_project_url,
            "media_id": l1_media_id,
            "edit_url": edit_url,
            "prompt": "Continue the peaceful river scene",
            "parent_job_id": "r24-l1",
            "chain_id": "r24",
        }
        try:
            r2 = await extend_video(
                client,
                job=job_l2,
                prompt=job_l2["prompt"],
                free_mode=True,
            )
            log.info("[R24] L2 raw result keys: %s", list(r2.keys()))
            l2_media_id = r2.get("media_id") or ""
            log.info(
                "[R24] L2 done: media_id=%s",
                l2_media_id[:40],
            )
            if l2_media_id and l2_media_id != l1_media_id:
                log.info("[R24] PASS — new distinct media_id from extend ✓")
                return 0
            else:
                log.error("[R24] FAIL — no new media_id (got %r)", l2_media_id)
                return 1
        except Exception as exc:
            log.error("[R24] L2 FAILED: %s", exc, exc_info=True)
            return 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("profile", nargs="?", default="ngoctuandt20")
    parser.add_argument("--project-url", default=None)
    parser.add_argument("--media-id", default=None)
    args = parser.parse_args()
    return asyncio.run(run(args.profile, args.project_url, args.media_id))


if __name__ == "__main__":
    sys.exit(main())
