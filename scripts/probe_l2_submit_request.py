#!/usr/bin/env python3
"""Probe Flow's L2 submit request schemas for reverse-API.

Drives ONE L2 op via the legacy UI path against an existing L1 parent's
``/edit/{parent_media_id}`` URL, and dumps every captured POST request
body so we can build endpoint-specific direct-API submitters.

Usage:

    ./.venv/bin/python scripts/probe_l2_submit_request.py \\
        <profile> <parent_edit_url> <op_type> [arg]

  op_type ∈ {extend, camera, insert, remove}
  arg     = prompt (extend / insert) or direction (camera) or
            "x,y,w,h" bbox normalized 0-1 (insert / remove)

Output: prints captured requests JSON to stdout between markers.
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


def _parse_bbox(s: str) -> dict:
    parts = [float(p) for p in s.split(",")]
    if len(parts) != 4:
        raise ValueError(f"bad bbox: {s}")
    return {"x": parts[0], "y": parts[1], "w": parts[2], "h": parts[3]}


async def main(
    profile: str,
    parent_edit_url: str,
    op_type: str,
    arg: str,
) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    log = logging.getLogger("probe-l2")

    from flow.client import FlowClient
    from flow.operations._l1_batch import install_batch_response_capture

    client = FlowClient(
        profile_name=profile,
        profile_base_dir=os.environ.get(
            "CHROME_USER_DATA_DIR", "./chrome-profiles"
        ),
        download_dir=os.environ.get("FLOW_DOWNLOAD_DIR", "./downloads"),
    )

    # Build a synthetic job that the legacy ops can navigate from.
    parent_media_id = parent_edit_url.rstrip("/").rsplit("/", 1)[-1]
    project_url = parent_edit_url.split("/edit/")[0]
    job_base = {
        "id": f"probe-l2-{op_type}",
        "profile": profile,
        "job_level": 2,
        "parent_job_id": "probe-parent",
        "media_id": parent_media_id,
        "project_url": project_url,
    }

    async with client:
        client._job_id = job_base["id"]
        install_batch_response_capture(client)
        try:
            if op_type == "extend":
                from flow.operations.extend import extend_video
                job = {**job_base, "type": "extend-video", "prompt": arg}
                result = await extend_video(client, job, prompt=arg)
            elif op_type == "camera":
                from flow.operations.camera import camera_move
                job = {**job_base, "type": "camera-move", "direction": arg}
                result = await camera_move(client, job, direction=arg)
            elif op_type == "insert":
                # arg = "prompt|x,y,w,h"
                p, bbox_s = arg.split("|", 1)
                bbox = _parse_bbox(bbox_s)
                from flow.operations.insert import insert_object
                job = {
                    **job_base, "type": "insert-object",
                    "prompt": p, "bbox": bbox,
                }
                result = await insert_object(client, job, prompt=p, bbox=bbox)
            elif op_type == "remove":
                bbox = _parse_bbox(arg)
                from flow.operations.remove import remove_object
                job = {**job_base, "type": "remove-object", "bbox": bbox}
                result = await remove_object(client, job, bbox=bbox)
            else:
                log.error("unknown op_type: %s", op_type)
                return 64
            log.info("op result: %s", result)
        except Exception:
            log.exception("op failed; dumping captures anyway")

        out = {
            "op_type": op_type,
            "parent_edit_url": parent_edit_url,
            "requests": [
                {
                    "url": r.get("url"),
                    "method": r.get("method"),
                    "headers": {
                        k: v for k, v in (r.get("headers") or {}).items()
                        if k.lower() in {
                            "content-type", "x-goog-api-key",
                            "authorization", "x-goog-authuser",
                        }
                    },
                    "post_data": r.get("post_data"),
                }
                for r in (getattr(client, "_batch_requests", []) or [])
            ],
            "responses": [
                {
                    "url": r.get("url"),
                    "status": r.get("status"),
                    "body_keys": (
                        list(r["body"].keys())
                        if isinstance(r.get("body"), dict) else None
                    ),
                    "body_sample": (
                        json.dumps(r["body"])[:600]
                        if isinstance(r.get("body"), (dict, list)) else None
                    ),
                }
                for r in (getattr(client, "_batch_responses", []) or [])
            ],
        }
        print("===PROBE_L2_OUTPUT===")
        print(json.dumps(out, indent=2, default=str))
        print("===END===")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print(
            f"usage: {sys.argv[0]} <profile> <parent_edit_url> "
            f"<extend|camera|insert|remove> [arg]",
            file=sys.stderr,
        )
        sys.exit(64)
    sys.exit(asyncio.run(main(
        sys.argv[1], sys.argv[2], sys.argv[3],
        sys.argv[4] if len(sys.argv) > 4 else "",
    )))
