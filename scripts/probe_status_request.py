#!/usr/bin/env python3
"""Probe Flow's batchCheckAsyncVideoGenerationStatus request schema.

Drives one UI submit, then waits for the polling status calls Flow's
own frontend issues. Dumps the request body of the first matching one
so we can build a direct-status primitive for inflate-batch's full
mass-gen path.
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
    log = logging.getLogger("probe-status")

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

    async with client:
        client._job_id = "probe-status"
        install_batch_response_capture(client)
        try:
            sub = await submit_generate_l1(
                client,
                {
                    "id": "_probe_status",
                    "type": "text-to-video",
                    "prompt": "a green frog reading a book on a mossy rock",
                    "profile": profile,
                    "job_level": 1,
                    "aspect_ratio": "16:9",
                },
                project_already_open=False,
            )
            log.info("submit ok, gen=%s project=%s",
                     sub["gen_id"][-12:], sub.get("project_url", "")[:80])
        except Exception:
            log.exception("submit failed")

        # Wait up to 60s for at least one batchCheckAsync* request to land.
        log.info("waiting up to 60s for status polling requests...")
        deadline = asyncio.get_event_loop().time() + 60.0
        target_url_hint = "batchcheckasync"
        captured: list[dict] = []
        while asyncio.get_event_loop().time() < deadline:
            for req in (getattr(client, "_batch_requests", []) or []):
                u = (req.get("url") or "").lower()
                if target_url_hint in u and req not in captured:
                    captured.append(req)
            for resp in (getattr(client, "_batch_responses", []) or []):
                u = (resp.get("url") or "").lower()
                if target_url_hint in u:
                    log.info("status response captured")
            if captured:
                break
            await asyncio.sleep(2)

        out = {
            "status_requests": [
                {
                    "url": r.get("url"),
                    "method": r.get("method"),
                    "headers": {k: v for k, v in (r.get("headers") or {}).items()
                                if k.lower() in {"content-type", "authorization", "referer"}},
                    "post_data": r.get("post_data"),
                }
                for r in captured[:3]
            ],
            "status_responses": [
                {
                    "url": r.get("url"),
                    "status": r.get("status"),
                    "body_keys": (
                        list(r["body"].keys())
                        if isinstance(r.get("body"), dict) else None
                    ),
                    "body_sample": (
                        json.dumps(r["body"])[:1500]
                        if isinstance(r.get("body"), dict) else None
                    ),
                }
                for r in (getattr(client, "_batch_responses", []) or [])
                if "batchcheckasync" in (r.get("url") or "").lower()
            ][:3],
        }
        print("===PROBE_STATUS===")
        print(json.dumps(out, indent=2, default=str))
        print("===END===")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"usage: {sys.argv[0]} <profile>", file=sys.stderr)
        sys.exit(64)
    sys.exit(asyncio.run(main(sys.argv[1])))
