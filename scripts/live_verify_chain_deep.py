#!/usr/bin/env python3
"""Live verify a 5-deep chain: L1 → L2 → L3 → L4 → L5.

Coverage: ``dispatch_chain_in_tab`` is generic (level-agnostic) but
the existing live tests stop at L3. This script proves the same code
path holds for L4+ by stacking 4 ops on a single L1 parent.

Usage::

    sudo -u flowengine env DISPLAY=:99 ... \\
      ./.venv/bin/python scripts/live_verify_chain_deep.py ngoctuandt20

PASS ⇔ 1 L1 + 4 chain ops all complete with 5 distinct media_ids and
       5 distinct output files.
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
    log = logging.getLogger("chain-deep")

    from flow.client import FlowClient
    from flow.operations._burn_recovery import with_recaptcha_recovery
    from flow.operations._l1_batch import (
        download_l1_gen_at_tile,
        snapshot_unique_tile_ids,
    )
    from flow.operations._l1_inflate_batch import submit_l1_batch_via_inflate
    from flow.operations._l1_status_poll import poll_status_via_api
    from flow.operations._multitab import batch_dispatch_chains

    download_dir = Path(os.environ.get("FLOW_DOWNLOAD_DIR", "./downloads"))
    download_dir.mkdir(parents=True, exist_ok=True)

    log.info("=" * 64)
    log.info("5-deep chain verify: 1 L1 → L2 → L3 → L4 → L5")
    log.info("Profile: %s", profile)
    log.info("=" * 64)

    t0 = time.time()
    profile_base_dir = os.environ.get(
        "CHROME_USER_DATA_DIR", "./chrome-profiles"
    )
    ts = int(time.time())

    # All extends — camera-move on an extend-child triggers Flow's
    # mode-button lockout (FLOW_BUTTON_EXACT §5.1). Extend chains
    # legally to arbitrary depth, which is what we want to verify.
    chain_ops_spec = [
        ("extend-video", {"prompt": "the camera tilts up to reveal a wider scene"}),
        ("extend-video", {"prompt": "soft warm light fades in slowly"}),
        ("extend-video", {"prompt": "the scene transitions to early dawn"}),
        ("extend-video", {"prompt": "first rays of sunlight cut through the mist"}),
    ]

    async def _full_chain() -> dict:
        client = FlowClient(
            profile_name=profile,
            profile_base_dir=profile_base_dir,
            download_dir=str(download_dir),
        )
        async with client:
            client._job_id = "chain-deep"

            log.info("Phase A: 1 L1 via sequential submit")
            submits = await submit_l1_batch_via_inflate(
                client,
                prompts=["a calm river running through a misty forest at dawn"],
                aspect_ratio="16:9",
            )
            if not submits:
                return {"error": "L1 submit failed"}
            l1 = submits[0]

            project_url = l1["project_url"]
            log.info("L1 project=%s gen=%s",
                     project_url[:80], l1["gen_id"][-12:])

            # Poll L1 status
            try:
                statuses = await poll_status_via_api(
                    client,
                    gen_ids=[l1["gen_id"]],
                    hard_timeout_sec=600,
                )
            except Exception as exc:
                log.exception("L1 status poll failed: %s", exc)
                return {"error": "L1 status failed"}
            entry = statuses.get(l1["gen_id"], {}) if isinstance(statuses, dict) else {}
            if entry.get("status") != "completed":
                return {"error": f"L1 status={entry.get('status')}"}

            mid = entry.get("media_id")
            if not mid:
                # fallback via UI tile snapshot
                snap = await snapshot_unique_tile_ids(client.page, [])
                mid = snap[0] if snap else None
            if not mid:
                return {"error": "L1 no media_id"}

            edit_url = f"{project_url.rstrip('/')}/edit/{mid}"
            log.info("L1 media_id=%s edit=%s", mid[:12], edit_url[-60:])

            # Optionally download L1 base file for distinctness check
            try:
                files = await download_l1_gen_at_tile(
                    client, mid, project_url=project_url, prefix="l1-deep",
                )
            except Exception:
                files = []
            l1_file = files[0] if files else None

            # ---------- Phase B: 1 chain × 4 ops ----------
            log.info("Phase B: 1 chain with 4 ops (L2→L5)")
            ops: list[dict] = []
            for j, (op_type, kw) in enumerate(chain_ops_spec):
                level = 2 + j
                ops.append({
                    "id": f"l{level}-deep-{j}-{ts}",
                    "type": op_type,
                    "job_level": level,
                    "parent_job_id": (
                        f"l{level - 1}-deep-{j - 1}-{ts}"
                        if j > 0 else f"l1-deep-{ts}"
                    ),
                    **kw,
                })

            chains_arg = [{
                "l1_parent": {
                    "edit_url": edit_url,
                    "media_id": mid,
                    "project_url": project_url,
                },
                "ops": ops,
            }]
            chain_outputs = await batch_dispatch_chains(client, chains_arg)
            chain_results = chain_outputs[0] if chain_outputs else []

            return {
                "l1": {
                    "media_id": mid,
                    "file": l1_file,
                    "project_url": project_url,
                },
                "chain_results": chain_results,
            }

    try:
        chain = await with_recaptcha_recovery(profile, _full_chain, attempts=2)
    except Exception:
        log.exception("chain crashed")
        return 3

    wall = time.time() - t0
    log.info("=" * 64)
    log.info("Chain wall-time: %.1fs", wall)
    if not isinstance(chain, dict) or "l1" not in chain:
        log.error("chain returned bad shape: %s", str(chain)[:200])
        return 3

    l1 = chain["l1"]
    chain_results = chain.get("chain_results", [])
    log.info("L1: mid=%s file=%s",
             (l1.get("media_id") or "")[:12],
             Path(l1["file"]).name if l1.get("file") else None)
    for i, r in enumerate(chain_results):
        log.info("  L%d (%s) status=%s mid=%s file=%s",
                 i + 2, r.get("op_type") or r.get("type"),
                 r.get("status"),
                 (r.get("media_id") or "")[:12],
                 Path(r["output_files"][0]).name if r.get("output_files") else None)

    completed = [r for r in chain_results if r.get("status") == "completed" and r.get("media_id")]
    mids = {l1.get("media_id")} | {r.get("media_id") for r in completed}
    mids.discard(None)
    files = set()
    if l1.get("file"):
        files.add(l1["file"])
    for r in completed:
        if r.get("output_files"):
            files.add(r["output_files"][0])

    expected_levels = 1 + len(chain_ops_spec)  # L1 + 4 ops = 5
    if (
        len(completed) == len(chain_ops_spec)
        and len(mids) == expected_levels
        and len(files) == expected_levels
    ):
        log.info(
            "PASS — %d levels deep, %d distinct mids, %d distinct files.",
            expected_levels, len(mids), len(files),
        )
        return 0
    log.error(
        "FAIL — completed=%d/%d distinct_mids=%d/%d distinct_files=%d/%d",
        len(completed), len(chain_ops_spec),
        len(mids), expected_levels,
        len(files), expected_levels,
    )
    return 3


def main():
    if len(sys.argv) < 2:
        print(f"usage: {sys.argv[0]} <profile>", file=sys.stderr)
        sys.exit(64)
    sys.exit(asyncio.run(run(sys.argv[1])))


if __name__ == "__main__":
    main()
