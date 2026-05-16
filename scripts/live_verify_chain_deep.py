#!/usr/bin/env python3
"""Live verify a deep extend chain: L1 → L2 → ... → LN.

Coverage: ``dispatch_chain_in_tab`` is generic (level-agnostic) but
the existing live tests stop at L3. This script proves the same code
path holds for L4+ by stacking extend ops on a single L1 parent.

Usage::

    sudo -u flowengine env DISPLAY=:99 ... \\
      ./.venv/bin/python scripts/live_verify_chain_deep.py ngoctuandt20

PASS ⇔ 1 L1 + chain ops all complete with distinct media_ids,
       distinct output files, and per-level duration ~= level × 8s.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


DEFAULT_CHAIN_PROMPTS = [
    "the camera tilts up to reveal a wider scene",
    "soft warm light fades in slowly",
    "the scene transitions to early dawn",
    "first rays of sunlight cut through the mist",
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Live verify a deep extend chain",
    )
    parser.add_argument("profile", help="Chrome profile name")
    parser.add_argument(
        "--assert-duration",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Assert ffprobe duration grows by chain level (default: on)",
    )
    parser.add_argument(
        "--depth",
        type=int,
        default=5,
        help="Total chain depth including L1 (default: 5)",
    )
    parser.add_argument(
        "--duration-tolerance-sec",
        type=float,
        default=2.0,
        help="Allowed per-level duration delta in seconds (default: 2.0)",
    )
    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    return build_parser().parse_args(argv)


def build_chain_ops_spec(depth: int) -> list[tuple[str, dict]]:
    if depth < 1:
        raise ValueError("depth must be >= 1")
    ops = []
    for index in range(depth - 1):
        prompt = DEFAULT_CHAIN_PROMPTS[index % len(DEFAULT_CHAIN_PROMPTS)]
        ops.append(("extend-video", {"prompt": prompt}))
    return ops


def collect_duration_downloads(l1: dict, chain_results: list[dict]) -> list[dict]:
    downloads: list[dict] = []
    if l1.get("file"):
        downloads.append({
            "level": 1,
            "media_id": l1.get("media_id") or "",
            "path": Path(l1["file"]),
        })

    for index, result in enumerate(chain_results):
        output_files = result.get("output_files") or []
        if not output_files:
            continue
        downloads.append({
            "level": int(result.get("job_level") or index + 2),
            "media_id": result.get("media_id") or "",
            "path": Path(output_files[0]),
        })
    return downloads


def run_duration_assertion(
    downloads: list[dict],
    *,
    profile: str,
    ts: int,
    tolerance_sec: float,
) -> dict:
    from flow.diagnostics_duration import assert_chain_duration, write_duration_report

    result = assert_chain_duration(downloads, tolerance_sec=tolerance_sec)
    report_path = REPO_ROOT / "error-captures" / f"duration_chain_{profile}_{ts}.md"
    write_duration_report(result, report_path)
    if not result["all_pass"]:
        failed_levels = [row["level"] for row in result["rows"] if not row["pass"]]
        raise RuntimeError(f"Duration assertion failed at levels: {failed_levels}")
    return result


def verify_chain_outputs(
    l1: dict,
    chain_results: list[dict],
    chain_ops_spec: list[tuple[str, dict]],
    *,
    assert_duration: bool,
    profile: str,
    ts: int,
    duration_tolerance_sec: float,
    log: logging.Logger,
) -> int:
    completed = [
        result for result in chain_results
        if result.get("status") == "completed" and result.get("media_id")
    ]
    mids = {l1.get("media_id")} | {result.get("media_id") for result in completed}
    mids.discard(None)
    files = set()
    if l1.get("file"):
        files.add(l1["file"])
    for result in completed:
        if result.get("output_files"):
            files.add(result["output_files"][0])

    if assert_duration:
        duration_downloads = collect_duration_downloads(l1, chain_results)
        duration_result = run_duration_assertion(
            duration_downloads,
            profile=profile,
            ts=ts,
            tolerance_sec=duration_tolerance_sec,
        )
        log.info(
            "Duration PASS — %d checked videos within ±%.1fs.",
            len(duration_result["rows"]),
            duration_tolerance_sec,
        )

    expected_levels = 1 + len(chain_ops_spec)
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


async def run(
    profile: str,
    *,
    depth: int = 5,
    assert_duration: bool = True,
    duration_tolerance_sec: float = 2.0,
) -> int:
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
    log.info("%d-deep chain verify: L1 → L%d", depth, depth)
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
    chain_ops_spec = build_chain_ops_spec(depth)

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

            # ---------- Phase B: 1 chain × N ops ----------
            log.info(
                "Phase B: 1 chain with %d ops (L2→L%d)",
                len(chain_ops_spec),
                depth,
            )
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

    return verify_chain_outputs(
        l1,
        chain_results,
        chain_ops_spec,
        assert_duration=assert_duration,
        profile=profile,
        ts=ts,
        duration_tolerance_sec=duration_tolerance_sec,
        log=log,
    )


def main():
    args = parse_args()
    sys.exit(asyncio.run(run(
        args.profile,
        depth=args.depth,
        assert_duration=args.assert_duration,
        duration_tolerance_sec=args.duration_tolerance_sec,
    )))


if __name__ == "__main__":
    main()
