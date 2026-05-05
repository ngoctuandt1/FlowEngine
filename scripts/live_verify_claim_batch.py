#!/usr/bin/env python3
"""Live verify the claim-batch worker dispatch path.

Three modes:

  1. l1-batch: Insert 3 L1 text-to-video jobs pending. Worker claims all 3
     in one batch, dispatches via inflate-batch (1 Chrome tab, 3 composer
     cycles). All 3 land on same project_url with 3 distinct media_ids.

  2. l2-multitab-mixed: Pre-condition 3 completed L1 jobs (or --parents A,B,C).
     Then insert 3 L2 ops on those parents: extend-video, camera-move,
     insert-object. Worker batch-claims all 3 → multi-tab dispatch (3 tabs
     in 1 Chrome). Asserts all complete with distinct media_ids.

  3. l2-multitab-siblings: Pre-condition 1 completed L1 parent (or --parent X).
     Insert 3 L2 extend-video ops all on that parent. Worker batch-claims →
     multi-tab → 3 distinct media_ids on same project_url.

Usage::

    python scripts/live_verify_claim_batch.py --mode l1-batch
    python scripts/live_verify_claim_batch.py --mode l2-multitab-mixed [--parents A,B,C]
    python scripts/live_verify_claim_batch.py --mode l2-multitab-siblings [--parent X]
    python scripts/live_verify_claim_batch.py --all

Environment variables (or flags):
    SERVER_URL        API base (default: http://127.0.0.1:8899)
    API_KEY           Bearer token (default: dev-key)
    --profile NAME    Chrome profile name (default: ngoctuandt20)
    --server URL      Override SERVER_URL
    --all             Run all 3 modes sequentially
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time
from typing import Any, Optional

try:
    import httpx
except ImportError:
    print("error: httpx not installed", file=sys.stderr)
    sys.exit(1)


class BranchClient:
    """HTTP client for FlowEngine server API."""

    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.client = httpx.Client(timeout=30)

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"}

    def create_job(self, job: dict[str, Any]) -> dict[str, Any]:
        """POST /api/jobs"""
        resp = self.client.post(
            f"{self.base_url}/api/jobs",
            json=job,
            headers=self._headers(),
        )
        resp.raise_for_status()
        return resp.json()

    def get_job(self, job_id: str) -> dict[str, Any]:
        """GET /api/jobs/{id}"""
        resp = self.client.get(
            f"{self.base_url}/api/jobs/{job_id}",
            headers=self._headers(),
        )
        resp.raise_for_status()
        return resp.json()

    def close(self):
        self.client.close()


def setup_logging() -> logging.Logger:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    return logging.getLogger("live-verify-claim-batch")


async def poll_job(client: BranchClient, job_id: str, timeout_sec: int = 480) -> dict[str, Any]:
    """Poll GET /api/jobs/{id} until terminal state, return final result."""
    log = logging.getLogger("live-verify-claim-batch")
    start = time.time()
    while True:
        if time.time() - start > timeout_sec:
            log.warning("timeout waiting for job %s", job_id)
            return {"status": "timeout", "id": job_id}
        try:
            job = client.get_job(job_id)
            status = job.get("status")
            if status in ("completed", "failed", "cancelled"):
                return job
            sys.stdout.write(".")
            sys.stdout.flush()
            await asyncio.sleep(10)
        except Exception as e:
            log.warning("poll error for %s: %s", job_id, e)
            await asyncio.sleep(10)


async def run_l1_batch(client: BranchClient, profile: str) -> int:
    """Insert 3 L1 text-to-video jobs, poll until all complete."""
    log = logging.getLogger("live-verify-claim-batch")
    log.info("=" * 64)
    log.info("Mode: l1-batch (3 L1 text-to-video, 1 project_url)")
    log.info("Profile: %s", profile)
    log.info("=" * 64)

    prompts = [
        "a red cat walking through a field of yellow flowers",
        "a blue dog running on a sandy beach at sunset",
        "a yellow bird flying over a green forest in the rain",
    ]

    job_ids = []
    for i, prompt in enumerate(prompts):
        job_spec = {
            "type": "text-to-video",
            "job_level": 1,
            "prompt": prompt,
            "profile": profile,
            "aspect_ratio": "16:9",
        }
        try:
            result = client.create_job(job_spec)
            job_id = result.get("id")
            job_ids.append(job_id)
            log.info("created L1 job[%d]: id=%s", i, job_id[:12])
        except Exception as e:
            log.error("failed to create L1 job[%d]: %s", i, e)
            return 3

    log.info("polling %d jobs until completion...", len(job_ids))
    results = await asyncio.gather(*[poll_job(client, jid) for jid in job_ids])

    log.info("=" * 64)
    log.info("Results:")
    completed = []
    project_urls = set()
    media_ids = set()
    for i, (job_id, result) in enumerate(zip(job_ids, results)):
        status = result.get("status", "unknown")
        project_url = result.get("project_url", "")
        media_id = result.get("media_id", "")
        log.info(
            "  [%d] id=%s status=%s project=%s media=%s",
            i, job_id[:12], status,
            project_url[:30] if project_url else "(none)",
            media_id[:12] if media_id else "(none)",
        )
        if status == "completed":
            completed.append(result)
            if project_url:
                project_urls.add(project_url)
            if media_id:
                media_ids.add(media_id)

    log.info("=" * 64)
    log.info("Completed: %d / %d", len(completed), len(job_ids))
    log.info("Unique project_urls: %d (want 1)", len(project_urls))
    log.info("Unique media_ids: %d (want %d)", len(media_ids), len(job_ids))

    if (
        len(completed) == len(job_ids)
        and len(project_urls) == 1
        and len(media_ids) == len(job_ids)
    ):
        log.info("PASS — L1 batch all completed, same project_url, distinct media_ids")
        return 0
    if len(completed) > 0:
        log.warning("PARTIAL")
        return 2
    log.error("FAIL")
    return 3


async def run_l2_multitab_mixed(
    client: BranchClient,
    profile: str,
    parent_ids: Optional[list[str]] = None,
) -> int:
    """Insert 3 L2 ops on 3 different L1 parents."""
    log = logging.getLogger("live-verify-claim-batch")
    log.info("=" * 64)
    log.info("Mode: l2-multitab-mixed (3 L2 ops on 3 different parents)")
    log.info("Profile: %s", profile)
    log.info("=" * 64)

    # If parents not provided, create 3 L1 jobs first
    if not parent_ids:
        log.info("Creating 3 L1 parents (no --parents provided)...")
        parent_prompts = [
            "a sunset over an ocean with palm trees",
            "a mountain landscape at dawn",
            "a forest stream in autumn",
        ]
        parent_ids = []
        for prompt in parent_prompts:
            job_spec = {
                "type": "text-to-video",
                "job_level": 1,
                "prompt": prompt,
                "profile": profile,
                "aspect_ratio": "16:9",
            }
            try:
                result = client.create_job(job_spec)
                parent_ids.append(result.get("id"))
                log.info("created L1 parent: %s", result.get("id")[:12])
            except Exception as e:
                log.error("failed to create L1 parent: %s", e)
                return 3

        log.info("polling 3 L1 parents until completion...")
        parents = await asyncio.gather(*[poll_job(client, pid) for pid in parent_ids])
        parent_ids = [p.get("id") for p in parents]
        log.info("all L1 parents completed")

    log.info("inserting 3 L2 ops on parents...")
    l2_specs = [
        {
            "type": "extend-video",
            "job_level": 2,
            "prompt": "extend it slowly",
            "profile": profile,
        },
        {
            "type": "camera-move",
            "job_level": 2,
            "direction": "Pan right",
            "profile": profile,
        },
        {
            "type": "insert-object",
            "job_level": 2,
            "prompt": "small bird",
            "bbox": {"x": 0.4, "y": 0.4, "w": 0.2, "h": 0.2},
            "profile": profile,
        },
    ]

    l2_job_ids = []
    for i, (parent_id, spec) in enumerate(zip(parent_ids, l2_specs)):
        job_spec = {**spec, "parent_job_id": parent_id}
        try:
            result = client.create_job(job_spec)
            job_id = result.get("id")
            l2_job_ids.append(job_id)
            log.info("created L2 job[%d]: type=%s parent=%s", i, spec["type"], parent_id[:12])
        except Exception as e:
            log.error("failed to create L2 job[%d]: %s", i, e)
            return 3

    log.info("polling %d L2 jobs until completion...", len(l2_job_ids))
    l2_results = await asyncio.gather(*[poll_job(client, jid) for jid in l2_job_ids])

    log.info("=" * 64)
    log.info("Results:")
    completed = []
    media_ids = set()
    for i, (parent_id, spec, result) in enumerate(zip(parent_ids, l2_specs, l2_results)):
        status = result.get("status", "unknown")
        media_id = result.get("media_id", "")
        project_url = result.get("project_url", "")
        log.info(
            "  [%d] type=%s parent=%s media=%s status=%s",
            i, spec["type"],
            parent_id[:12],
            media_id[:12] if media_id else "(none)",
            status,
        )
        if status == "completed":
            completed.append(result)
            if media_id:
                media_ids.add(media_id)

    log.info("=" * 64)
    log.info("Completed: %d / %d", len(completed), len(l2_job_ids))
    log.info("Unique media_ids: %d (want %d)", len(media_ids), len(l2_job_ids))

    if (
        len(completed) == len(l2_job_ids)
        and len(media_ids) == len(l2_job_ids)
    ):
        log.info("PASS — L2 mixed batch all completed, distinct media_ids")
        return 0
    if len(completed) > 0:
        log.warning("PARTIAL")
        return 2
    log.error("FAIL")
    return 3


async def run_l2_multitab_siblings(
    client: BranchClient,
    profile: str,
    parent_id: Optional[str] = None,
) -> int:
    """Insert 3 L2 extend-video ops on 1 L1 parent."""
    log = logging.getLogger("live-verify-claim-batch")
    log.info("=" * 64)
    log.info("Mode: l2-multitab-siblings (3 L2 extend ops on 1 parent)")
    log.info("Profile: %s", profile)
    log.info("=" * 64)

    # If parent not provided, create 1 L1 job first
    if not parent_id:
        log.info("Creating 1 L1 parent (no --parent provided)...")
        job_spec = {
            "type": "text-to-video",
            "job_level": 1,
            "prompt": "a serene lake at sunset",
            "profile": profile,
            "aspect_ratio": "16:9",
        }
        try:
            result = client.create_job(job_spec)
            parent_id = result.get("id")
            log.info("created L1 parent: %s", parent_id[:12])
        except Exception as e:
            log.error("failed to create L1 parent: %s", e)
            return 3

        log.info("polling L1 parent until completion...")
        parent = await poll_job(client, parent_id)
        parent_id = parent.get("id")
        log.info("L1 parent completed")

    log.info("inserting 3 L2 extend-video ops on parent %s...", parent_id[:12])
    prompts = [
        "extend it slowly with a smooth pan",
        "extend it with dramatic lighting changes",
        "extend it with additional elements appearing",
    ]

    l2_job_ids = []
    for i, prompt in enumerate(prompts):
        job_spec = {
            "type": "extend-video",
            "job_level": 2,
            "prompt": prompt,
            "parent_job_id": parent_id,
            "profile": profile,
        }
        try:
            result = client.create_job(job_spec)
            job_id = result.get("id")
            l2_job_ids.append(job_id)
            log.info("created L2 extend job[%d]: id=%s", i, job_id[:12])
        except Exception as e:
            log.error("failed to create L2 extend job[%d]: %s", i, e)
            return 3

    log.info("polling %d L2 extend jobs until completion...", len(l2_job_ids))
    l2_results = await asyncio.gather(*[poll_job(client, jid) for jid in l2_job_ids])

    log.info("=" * 64)
    log.info("Results:")
    completed = []
    media_ids = set()
    project_urls = set()
    for i, (result, prompt) in enumerate(zip(l2_results, prompts)):
        status = result.get("status", "unknown")
        media_id = result.get("media_id", "")
        project_url = result.get("project_url", "")
        log.info(
            "  [%d] media=%s status=%s project=%s",
            i,
            media_id[:12] if media_id else "(none)",
            status,
            project_url[:30] if project_url else "(none)",
        )
        if status == "completed":
            completed.append(result)
            if media_id:
                media_ids.add(media_id)
            if project_url:
                project_urls.add(project_url)

    log.info("=" * 64)
    log.info("Completed: %d / %d", len(completed), len(l2_job_ids))
    log.info("Unique project_urls: %d (want 1)", len(project_urls))
    log.info("Unique media_ids: %d (want %d)", len(media_ids), len(l2_job_ids))

    if (
        len(completed) == len(l2_job_ids)
        and len(project_urls) == 1
        and len(media_ids) == len(l2_job_ids)
    ):
        log.info("PASS — L2 siblings all completed, same project_url, distinct media_ids")
        return 0
    if len(completed) > 0:
        log.warning("PARTIAL")
        return 2
    log.error("FAIL")
    return 3


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="Live verify claim-batch worker dispatch path",
    )
    parser.add_argument(
        "--mode",
        choices=["l1-batch", "l2-multitab-mixed", "l2-multitab-siblings"],
        help="Verification mode (mutually exclusive with --all)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Run all 3 modes sequentially",
    )
    parser.add_argument(
        "--profile",
        default="ngoctuandt20",
        help="Chrome profile name (default: ngoctuandt20)",
    )
    parser.add_argument(
        "--server",
        default=None,
        help="Server URL (default: env SERVER_URL or http://127.0.0.1:8899)",
    )
    parser.add_argument(
        "--parents",
        default=None,
        help="Comma-separated parent job IDs for l2-multitab-mixed mode",
    )
    parser.add_argument(
        "--parent",
        default=None,
        help="Parent job ID for l2-multitab-siblings mode",
    )

    args = parser.parse_args()
    setup_logging()
    log = logging.getLogger("live-verify-claim-batch")

    if not args.mode and not args.all:
        parser.print_help()
        return 64

    server_url = args.server or os.environ.get("SERVER_URL", "http://127.0.0.1:8899")
    api_key = os.environ.get("API_KEY", "dev-key")

    client = BranchClient(server_url, api_key)
    try:
        if args.all:
            log.info("Running all 3 modes sequentially...")
            ret1 = await run_l1_batch(client, args.profile)
            log.info("")
            ret2 = await run_l2_multitab_mixed(client, args.profile, None)
            log.info("")
            ret3 = await run_l2_multitab_siblings(client, args.profile, None)
            return max(ret1, ret2, ret3)

        if args.mode == "l1-batch":
            return await run_l1_batch(client, args.profile)
        elif args.mode == "l2-multitab-mixed":
            parents = args.parents.split(",") if args.parents else None
            return await run_l2_multitab_mixed(client, args.profile, parents)
        elif args.mode == "l2-multitab-siblings":
            return await run_l2_multitab_siblings(client, args.profile, args.parent)
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
