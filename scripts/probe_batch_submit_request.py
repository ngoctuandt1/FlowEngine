#!/usr/bin/env python3
"""Probe Flow's batchAsyncGenerateVideoText request schema for reverse-API.

Runs ONE L1 t2v submit through the UI just far enough to trigger Flow's
network call, then dumps the captured POST request body + headers so we
can build a direct-API path that bypasses the composer entirely.

Output: prints JSON to stdout with `url`, `method`, `headers`, `post_data`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


async def main(profile: str) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    log = logging.getLogger("probe-batch-submit")

    from flow.client import FlowClient
    from flow.operations._l1_batch import (
        install_batch_response_capture,
        submit_generate_l1,
    )

    client = FlowClient(
        profile_name=profile,
        profile_base_dir=os.environ.get(
            "CHROME_USER_DATA_DIR", "./chrome-profiles"
        ),
        download_dir=os.environ.get("FLOW_DOWNLOAD_DIR", "./downloads"),
    )
    job = {
        "id": "probe-1",
        "type": "text-to-video",
        "prompt": "a green frog reading a book on a mossy rock",
        "profile": profile,
        "job_level": 1,
        "aspect_ratio": "16:9",
    }

    async with client:
        client._job_id = "probe-1"
        install_batch_response_capture(client)
        try:
            sub = await submit_generate_l1(
                client, job, project_already_open=False,
            )
            log.info("submit ok, gen=%s project=%s",
                     sub["gen_id"][-12:], sub["project_url"][:80])
        except Exception:
            log.exception("submit failed; dumping captures anyway")

        out = {
            "requests": getattr(client, "_batch_requests", [])[:5],
            "responses": [
                {
                    "url": r.get("url"),
                    "status": r.get("status"),
                    "body_keys": (
                        list(r["body"].keys())
                        if isinstance(r.get("body"), dict) else None
                    ),
                }
                for r in (getattr(client, "_batch_responses", []) or [])[:5]
            ],
        }
        print("===PROBE_OUTPUT===")
        print(json.dumps(out, indent=2, default=str))
        print("===END===")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"usage: {sys.argv[0]} <profile>", file=sys.stderr)
        sys.exit(64)
    sys.exit(asyncio.run(main(sys.argv[1])))
