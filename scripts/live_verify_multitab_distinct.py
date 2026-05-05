#!/usr/bin/env python3
"""Focused live verify: 2 parallel L2 chains on EXISTING L1 parents.

Goal: prove ``_gen_id_from_submit_response`` authoritative override
solves multi-tab metadata contamination — without depending on the
flaky Phase A inflate-batch path.

Inputs (CLI args):
    profile          Chrome profile name
    project_url      Existing project URL (must contain at least 2 L1s)
    parent_a_mid     Existing L1 media_id for chain A
    parent_b_mid     Existing L1 media_id for chain B

Each chain runs ONE op (camera-move) in a new tab in parallel. PASS:

    chain A's resolved L2 media_id  != chain B's resolved L2 media_id
    chain A's resolved L2 media_id  != parent_a_mid
    chain B's resolved L2 media_id  != parent_b_mid
    file md5 (chain A) != file md5 (chain B)

Costs ~5-10 credits (2 camera-move ops). Wall-time ~3-5 min.
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
    profile: str, project_url: str, parent_a_mid: str, parent_b_mid: str,
) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    log = logging.getLogger("multitab-distinct")

    from flow.client import FlowClient
    from flow.operations._burn_recovery import with_recaptcha_recovery
    from flow.operations._multitab import batch_dispatch_chains

    download_dir = Path(os.environ.get("FLOW_DOWNLOAD_DIR", "./downloads"))
    download_dir.mkdir(parents=True, exist_ok=True)

    log.info("=" * 64)
    log.info("Multitab DISTINCT verify (2 parallel L2 chains)")
    log.info("Profile: %s", profile)
    log.info("Project: %s", project_url)
    log.info("Parent A (chain 0): %s", parent_a_mid[:12])
    log.info("Parent B (chain 1): %s", parent_b_mid[:12])
    log.info("=" * 64)

    base = project_url.rstrip("/")
    edit_a = f"{base}/edit/{parent_a_mid}"
    edit_b = f"{base}/edit/{parent_b_mid}"
    ts = int(time.time())

    async def _do() -> list[list[dict]]:
        client = FlowClient(
            profile_name=profile,
            profile_base_dir=os.environ.get(
                "CHROME_USER_DATA_DIR", "./chrome-profiles",
            ),
            download_dir=str(download_dir),
        )
        async with client:
            client._job_id = "multitab-distinct"
            chains = [
                {
                    "l1_parent": {
                        "edit_url": edit_a,
                        "media_id": parent_a_mid,
                        "project_url": project_url,
                    },
                    "ops": [{
                        "id": f"chain-A-cam-dolly-in-{ts}",
                        "type": "camera-move",
                        "direction": "Dolly in",
                        "job_level": 2,
                    }],
                },
                {
                    "l1_parent": {
                        "edit_url": edit_b,
                        "media_id": parent_b_mid,
                        "project_url": project_url,
                    },
                    "ops": [{
                        "id": f"chain-B-cam-orbit-r-{ts}",
                        "type": "camera-move",
                        "direction": "Orbit right",
                        "job_level": 2,
                    }],
                },
            ]
            return await batch_dispatch_chains(client, chains)

    try:
        results = await with_recaptcha_recovery(profile, _do, attempts=2)
    except Exception:
        log.exception("crashed")
        return 3

    if not isinstance(results, list) or len(results) < 2:
        log.error("bad shape: %s", str(results)[:200])
        return 3

    chain_a = (results[0] or [{}])[0] if results[0] else {}
    chain_b = (results[1] or [{}])[0] if results[1] else {}

    a_mid = chain_a.get("media_id") or ""
    b_mid = chain_b.get("media_id") or ""
    a_files = chain_a.get("output_files") or []
    b_files = chain_b.get("output_files") or []

    log.info("=" * 64)
    log.info("Chain A: status=%s mid=%s file=%s",
             chain_a.get("status"), a_mid[:12],
             Path(a_files[0]).name if a_files else "no")
    log.info("Chain B: status=%s mid=%s file=%s",
             chain_b.get("status"), b_mid[:12],
             Path(b_files[0]).name if b_files else "no")

    a_ok = chain_a.get("status") == "completed"
    b_ok = chain_b.get("status") == "completed"
    if not (a_ok and b_ok):
        log.error("FAIL — at least one chain did not complete")
        return 3

    if a_mid == b_mid:
        log.error("FAIL — both chains resolved IDENTICAL mid (%s)", a_mid[:12])
        return 3
    if a_mid == parent_a_mid or b_mid == parent_b_mid:
        log.error("FAIL — chain resolved its own parent's mid")
        return 3
    if a_mid == parent_b_mid or b_mid == parent_a_mid:
        log.error("FAIL — chain resolved sibling parent's mid (CONTAMINATION)")
        return 3

    if not a_files or not b_files:
        log.error("FAIL — missing output files")
        return 3
    a_md5 = _md5(a_files[0])
    b_md5 = _md5(b_files[0])
    log.info("md5 A=%s  B=%s", a_md5[:8], b_md5[:8])
    if a_md5 == b_md5:
        log.error("FAIL — output file md5 collision")
        return 3

    log.info("PASS — multi-tab gen_id authoritative override verified.")
    log.info("  chain A: parent=%s -> resolved=%s",
             parent_a_mid[:12], a_mid[:12])
    log.info("  chain B: parent=%s -> resolved=%s",
             parent_b_mid[:12], b_mid[:12])
    return 0


def main():
    if len(sys.argv) < 5:
        print(
            f"usage: {sys.argv[0]} <profile> <project_url> "
            f"<parent_a_mid> <parent_b_mid>",
            file=sys.stderr,
        )
        sys.exit(64)
    sys.exit(asyncio.run(run(
        sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4],
    )))


if __name__ == "__main__":
    main()
