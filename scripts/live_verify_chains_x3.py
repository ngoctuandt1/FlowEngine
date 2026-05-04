#!/usr/bin/env python3
"""3 parallel chains × 2 ops (L2 → L3) on EXISTING L1 parents.

Skips flaky Phase A inflate-batch — caller provides 3 known-good L1
parent media_ids. Each chain runs L2 + L3 sequentially in its own tab,
all 3 chains in parallel.

Usage::

    ./.venv/bin/python scripts/live_verify_chains_x3.py \\
        <profile> <project_url> <parent1> <parent2> <parent3>

PASS criteria (all must hold):
  * 3/3 chains produce both L2 and L3 results
  * 6 resolved media_ids — all distinct, none == any parent
  * 6 output files — all distinct md5

Cost ~30 credits (6 ops × ~5 credits @ 1080p Lite). Wall ~5-8 min.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def _md5(path: str) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


async def run(
    profile: str, project_url: str, parents: list[str],
) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    log = logging.getLogger("chains-x3")

    from flow.client import FlowClient
    from flow.operations._burn_recovery import with_recaptcha_recovery
    from flow.operations._multitab import batch_dispatch_chains

    download_dir = Path(os.environ.get("FLOW_DOWNLOAD_DIR", "./downloads"))
    download_dir.mkdir(parents=True, exist_ok=True)

    log.info("=" * 64)
    log.info("3 parallel L2→L3 chains on existing L1 parents")
    log.info("Profile: %s", profile)
    log.info("Project: %s", project_url[:80])
    for i, p in enumerate(parents):
        log.info("  parent[%d] = %s", i, p[:16])
    log.info("=" * 64)

    base = project_url.rstrip("/")
    ts = int(time.time())
    # All directions confirmed in MCP screenshot 2026-05-04 (Camera
    # motion tab on Veo 3.1 - Lite): Dolly in / Dolly out / Orbit
    # left / Orbit right / Orbit up / Orbit low / Dolly in zoom out /
    # Dolly out zoom in. "Orbit down" did NOT exist — caused chain 2
    # L3 retry-fail. Use only verified-visible directions below.
    chain_specs = [
        [
            ("camera-move", {"direction": "Dolly in"}),
            ("camera-move", {"direction": "Orbit right"}),
        ],
        [
            ("camera-move", {"direction": "Dolly out"}),
            ("camera-move", {"direction": "Orbit left"}),
        ],
        [
            ("camera-move", {"direction": "Orbit up"}),
            ("camera-move", {"direction": "Orbit low"}),
        ],
    ]

    async def _do() -> list[list[dict]]:
        client = FlowClient(
            profile_name=profile,
            profile_base_dir=os.environ.get(
                "CHROME_USER_DATA_DIR", "./chrome-profiles",
            ),
            download_dir=str(download_dir),
        )
        async with client:
            client._job_id = "chains-x3"
            chains = []
            for i, parent in enumerate(parents):
                spec = chain_specs[i % len(chain_specs)]
                ops = []
                for j, (op_type, kwargs) in enumerate(spec):
                    level = 2 + j
                    ops.append({
                        "id": f"l{level}-c{i}-s{j}-{ts}",
                        "type": op_type,
                        "job_level": level,
                        **kwargs,
                    })
                chains.append({
                    "l1_parent": {
                        "edit_url": f"{base}/edit/{parent}",
                        "media_id": parent,
                        "project_url": project_url,
                    },
                    "ops": ops,
                })
            return await batch_dispatch_chains(client, chains)

    try:
        results = await with_recaptcha_recovery(profile, _do, attempts=2)
    except Exception:
        log.exception("crashed")
        return 3

    if not isinstance(results, list) or len(results) < len(parents):
        log.error("bad shape: %s", str(results)[:200])
        return 3

    log.info("=" * 64)
    log.info("Per-chain summary:")
    all_mids: list[str] = []
    all_files: list[str] = []
    chain_full_ok = 0
    for i, chain_results in enumerate(results):
        l2 = chain_results[0] if len(chain_results) >= 1 else {}
        l3 = chain_results[1] if len(chain_results) >= 2 else {}
        log.info(
            "  chain[%d] parent=%s",
            i, parents[i][:16],
        )
        for label, r in (("L2", l2), ("L3", l3)):
            mid = r.get("media_id") or ""
            files = r.get("output_files") or []
            log.info(
                "    %s: status=%s mid=%s file=%s",
                label, r.get("status"), mid[:16],
                Path(files[0]).name if files else "no",
            )
            if r.get("status") == "completed" and mid and files:
                all_mids.append(mid)
                all_files.append(files[0])
        if (
            l2.get("status") == "completed"
            and l3.get("status") == "completed"
        ):
            chain_full_ok += 1

    log.info("=" * 64)
    log.info("Aggregate:")
    log.info("  full chains (L2+L3 ok): %d / %d", chain_full_ok, len(parents))
    log.info("  resolved mids: %d", len(all_mids))
    distinct_mids = set(all_mids)
    log.info("  distinct mids: %d", len(distinct_mids))
    parent_collisions = sum(1 for m in all_mids if m in set(parents))
    log.info("  collisions with parents: %d", parent_collisions)

    md5s = [_md5(p) for p in all_files if Path(p).is_file()]
    distinct_md5 = set(md5s)
    log.info("  files md5 distinct: %d / %d", len(distinct_md5), len(md5s))

    if (
        chain_full_ok == len(parents)
        and len(distinct_mids) == len(all_mids)
        and parent_collisions == 0
        and len(distinct_md5) == len(md5s)
    ):
        log.info("PASS — 3 chains × 2 ops fully isolated, %d distinct.",
                 len(md5s))
        return 0
    if chain_full_ok >= 1 and len(distinct_md5) == len(md5s):
        log.warning("PARTIAL — distinct md5 ok but not all chains complete")
        return 2
    log.error("FAIL")
    return 3


def main():
    if len(sys.argv) < 6:
        print(
            f"usage: {sys.argv[0]} <profile> <project_url> "
            f"<parent1> <parent2> <parent3>",
            file=sys.stderr,
        )
        sys.exit(64)
    sys.exit(asyncio.run(run(
        sys.argv[1], sys.argv[2],
        [sys.argv[3], sys.argv[4], sys.argv[5]],
    )))


if __name__ == "__main__":
    main()
