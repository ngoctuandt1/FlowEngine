"""Live probe for L2 media_id route slug behavior via the FlowEngine REST API."""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import requests


ROUTE_SLUG_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
POLL_INTERVAL_SECONDS = 5
DEFAULT_SERVER = "http://127.0.0.1:8080"
DEFAULT_PROMPT = "probe cat walking"
DEFAULT_TIMEOUT = 300
BBOX = {"x": 0.3, "y": 0.3, "w": 0.4, "h": 0.4}
RUNS_DIR = Path(__file__).resolve().parent / "probe_runs"


class JobPollTimeout(TimeoutError):
    def __init__(self, job_id: str, timeout_seconds: int, checkpoints: list[dict[str, Any]]):
        super().__init__(f"Timed out waiting for job {job_id} after {timeout_seconds}s")
        self.job_id = job_id
        self.timeout_seconds = timeout_seconds
        self.checkpoints = checkpoints


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Probe L2 insert/remove media_id route slugs through the REST API."
    )
    parser.add_argument("--server", default=DEFAULT_SERVER, help="FlowEngine server base URL")
    parser.add_argument("--profile", required=True, help="Chrome profile name")
    parser.add_argument("--prompt", default=DEFAULT_PROMPT, help="L1 text-to-video prompt")
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT,
        help="Per-job timeout in seconds while polling for completion",
    )
    return parser.parse_args()


def build_job_summary(job_type: str, job: dict[str, Any] | None) -> dict[str, Any]:
    job = job or {}
    output_files = job.get("output_files") or []
    return {
        "type": job_type,
        "id": job.get("id"),
        "status": job.get("status"),
        "media_id": job.get("media_id"),
        "edit_url": job.get("edit_url"),
        "output_count": len(output_files),
        "error": job.get("error"),
    }


def build_assertions(l1: dict[str, Any] | None, insert: dict[str, Any] | None, remove: dict[str, Any] | None) -> dict[str, bool]:
    l1_media_id = (l1 or {}).get("media_id")
    insert_media_id = (insert or {}).get("media_id")
    remove_media_id = (remove or {}).get("media_id")
    jobs = [job for job in (l1, insert, remove) if job is not None]

    return {
        "all_completed": len(jobs) == 3 and all(job.get("status") == "completed" for job in jobs),
        "l1_media_id_is_route_slug_format": bool(l1_media_id and ROUTE_SLUG_RE.fullmatch(l1_media_id)),
        "insert_media_id_is_route_slug_format": bool(
            insert_media_id and ROUTE_SLUG_RE.fullmatch(insert_media_id)
        ),
        "remove_media_id_is_route_slug_format": bool(
            remove_media_id and ROUTE_SLUG_RE.fullmatch(remove_media_id)
        ),
        "insert_media_id_differs_from_l1": bool(insert_media_id and l1_media_id and insert_media_id != l1_media_id),
        "remove_media_id_differs_from_l1": bool(remove_media_id and l1_media_id and remove_media_id != l1_media_id),
        "insert_media_id_differs_from_remove": bool(
            insert_media_id and remove_media_id and insert_media_id != remove_media_id
        ),
    }


def build_report(
    profile: str,
    prompt: str,
    l1: dict[str, Any] | None,
    insert: dict[str, Any] | None,
    remove: dict[str, Any] | None,
    timeline: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    assertions = build_assertions(l1, insert, remove)
    return {
        "chain_id": (l1 or {}).get("chain_id") or (insert or {}).get("chain_id") or (remove or {}).get("chain_id"),
        "profile": profile,
        "prompt": prompt,
        "jobs": [
            build_job_summary("text-to-video", l1),
            build_job_summary("insert-object", insert),
            build_job_summary("remove-object", remove),
        ],
        "timeline": timeline or [],
        "assertions": assertions,
        "verdict": "PASS" if all(assertions.values()) else "FAIL",
    }


def write_report(report: dict[str, Any]) -> Path:
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    report_path = RUNS_DIR / f"{timestamp}.json"
    report_json = json.dumps(report, indent=2)
    report_path.write_text(report_json + "\n", encoding="utf-8")
    print(report_json)
    return report_path


def fail(
    profile: str,
    prompt: str,
    l1: dict[str, Any] | None,
    insert: dict[str, Any] | None,
    remove: dict[str, Any] | None,
    timeline: list[dict[str, Any]] | None = None,
) -> int:
    report = build_report(profile, prompt, l1, insert, remove, timeline)
    write_report(report)
    return 1


def request_json(method: str, url: str, *, session: requests.Session, **kwargs: Any) -> dict[str, Any]:
    response = session.request(method, url, timeout=30, **kwargs)
    response.raise_for_status()
    return response.json()


def build_checkpoint(job: dict[str, Any]) -> dict[str, Any]:
    return {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "status": job.get("status"),
        "edit_url": job.get("edit_url"),
        "media_id": job.get("media_id"),
    }


def build_timeline_entry(job_type: str, job_id: str, checkpoints: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "job_type": job_type,
        "job_id": job_id,
        "checkpoints": checkpoints,
    }


def summarize_timeline(timeline: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summary: list[dict[str, Any]] = []
    for entry in timeline:
        checkpoints = entry.get("checkpoints") or []
        first_edit_url = checkpoints[0].get("edit_url") if checkpoints else None
        last_edit_url = checkpoints[-1].get("edit_url") if checkpoints else None
        summary.append(
            {
                "job_type": entry.get("job_type"),
                "job_id": entry.get("job_id"),
                "first_edit_url": first_edit_url,
                "last_edit_url": last_edit_url,
                "flipped": bool(first_edit_url and last_edit_url and first_edit_url != last_edit_url),
            }
        )
    return summary


def print_timeline_summary(timeline: list[dict[str, Any]]) -> None:
    for item in summarize_timeline(timeline):
        flip_note = " [FLIP]" if item["flipped"] else ""
        print(
            f'{item["job_type"]}: first={item["first_edit_url"]} '
            f'last={item["last_edit_url"]}{flip_note}'
        )


def poll_job(
    job_id: str, *, server: str, timeout_seconds: int, session: requests.Session
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    deadline = time.monotonic() + timeout_seconds
    job_url = f"{server}/api/jobs/{job_id}"
    checkpoints: list[dict[str, Any]] = []
    while True:
        job = request_json("GET", job_url, session=session)
        checkpoints.append(build_checkpoint(job))
        status = job.get("status")
        if status in {"completed", "failed"}:
            return job, checkpoints
        if time.monotonic() >= deadline:
            raise JobPollTimeout(job_id, timeout_seconds, checkpoints)
        time.sleep(POLL_INTERVAL_SECONDS)


def create_job(
    payload: dict[str, Any], *, server: str, timeout_seconds: int, session: requests.Session
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    created = request_json("POST", f"{server}/api/jobs", session=session, json=payload)
    return poll_job(created["id"], server=server, timeout_seconds=timeout_seconds, session=session)


def main() -> int:
    args = parse_args()
    server = args.server.rstrip("/")
    l1_job: dict[str, Any] | None = None
    insert_job: dict[str, Any] | None = None
    remove_job: dict[str, Any] | None = None
    timeline: list[dict[str, Any]] = []

    try:
        with requests.Session() as session:
            l1_payload = {
                "type": "text-to-video",
                "profile": args.profile,
                "prompt": args.prompt,
                "job_level": 1,
            }
            l1_job, _ = create_job(l1_payload, server=server, timeout_seconds=args.timeout, session=session)
            if l1_job.get("status") != "completed":
                return fail(args.profile, args.prompt, l1_job, insert_job, remove_job, timeline)

            insert_payload = {
                "type": "insert-object",
                "parent_job_id": l1_job["id"],
                "bbox": BBOX,
                "prompt": "add hat",
                "job_level": 2,
                "profile": l1_job.get("profile"),
                "project_url": l1_job.get("project_url"),
                "media_id": l1_job.get("media_id"),
                "chain_id": l1_job.get("chain_id"),
            }
            insert_job, insert_timeline = create_job(
                insert_payload, server=server, timeout_seconds=args.timeout, session=session
            )
            timeline.append(build_timeline_entry("insert-object", insert_job["id"], insert_timeline))
            if insert_job.get("status") != "completed":
                return fail(args.profile, args.prompt, l1_job, insert_job, remove_job, timeline)

            remove_payload = {
                "type": "remove-object",
                "parent_job_id": l1_job["id"],
                "bbox": BBOX,
                "job_level": 2,
                "profile": l1_job.get("profile"),
                "project_url": l1_job.get("project_url"),
                "media_id": l1_job.get("media_id"),
                "chain_id": l1_job.get("chain_id"),
            }
            remove_job, remove_timeline = create_job(
                remove_payload, server=server, timeout_seconds=args.timeout, session=session
            )
            timeline.append(build_timeline_entry("remove-object", remove_job["id"], remove_timeline))
            if remove_job.get("status") != "completed":
                return fail(args.profile, args.prompt, l1_job, insert_job, remove_job, timeline)
    except (requests.RequestException, TimeoutError, ValueError) as exc:
        if isinstance(exc, JobPollTimeout):
            if remove_job is None and insert_job is not None:
                timeline.append(build_timeline_entry("remove-object", exc.job_id, exc.checkpoints))
            elif insert_job is None and l1_job is not None:
                timeline.append(build_timeline_entry("insert-object", exc.job_id, exc.checkpoints))
        if remove_job is None and insert_job is not None and not insert_job.get("error"):
            insert_job = {**insert_job, "error": str(exc)}
        elif insert_job is None and l1_job is not None and not l1_job.get("error"):
            l1_job = {**l1_job, "error": str(exc)}
        return fail(args.profile, args.prompt, l1_job, insert_job, remove_job, timeline)

    report = build_report(args.profile, args.prompt, l1_job, insert_job, remove_job, timeline)
    write_report(report)
    print_timeline_summary(report["timeline"])
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
