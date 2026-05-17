"""Requeue FlowEngine jobs through the public jobs API.

Examples:
    python scripts/requeue_jobs.py --status failed
    python scripts/requeue_jobs.py --status cancelled
    python scripts/requeue_jobs.py --id <job_id>
    python scripts/requeue_jobs.py --type extend-video --status failed --dry-run
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


REQUEUEABLE_STATUSES = ("failed", "cancelled")
DEFAULT_SERVER_URL = "http://localhost:8080"
PAGE_SIZE = 2000


@dataclass(frozen=True)
class ApiError(Exception):
    """HTTP/API error with response context."""

    method: str
    url: str
    status_code: int | None
    detail: str

    def __str__(self) -> str:
        status = self.status_code if self.status_code is not None else "request failed"
        return f"{self.method} {self.url} -> {status}: {self.detail}"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Find failed/cancelled FlowEngine jobs and requeue them via /api/jobs/{id}/requeue.",
    )
    parser.add_argument("--type", dest="job_type", help="Filter by job.type, e.g. extend-video.")
    parser.add_argument(
        "--status",
        choices=REQUEUEABLE_STATUSES,
        help="Filter by current status. Defaults to failed and cancelled.",
    )
    parser.add_argument("--id", dest="job_id", help="Requeue a single job by ID.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print matching jobs without requeueing them.",
    )
    parser.add_argument(
        "--server-url",
        default=DEFAULT_SERVER_URL,
        help=f"FlowEngine server URL (default: {DEFAULT_SERVER_URL}).",
    )
    return parser.parse_args(argv)


def _api_url(server_url: str, path: str, query: dict[str, Any] | None = None) -> str:
    url = f"{server_url.rstrip('/')}{path}"
    if query:
        url = f"{url}?{urlencode(query)}"
    return url


def _request_json(
    method: str,
    server_url: str,
    path: str,
    *,
    query: dict[str, Any] | None = None,
) -> Any:
    url = _api_url(server_url, path, query)
    request = Request(url, method=method)
    request.add_header("Accept", "application/json")

    try:
        with urlopen(request, timeout=30) as response:  # noqa: S310 - operator-supplied URL.
            payload = response.read().decode("utf-8")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace") or exc.reason
        raise ApiError(method, url, exc.code, detail) from exc
    except URLError as exc:
        raise ApiError(method, url, None, str(exc.reason)) from exc

    if not payload:
        return None
    try:
        return json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ApiError(method, url, None, f"invalid JSON response: {payload[:200]}") from exc


def get_job(server_url: str, job_id: str) -> dict[str, Any]:
    return _request_json("GET", server_url, f"/api/jobs/{job_id}")


def list_jobs_page(
    server_url: str,
    *,
    status: str,
    job_type: str | None,
    offset: int,
) -> list[dict[str, Any]]:
    query: dict[str, Any] = {"status": status, "limit": PAGE_SIZE}
    if offset:
        query["offset"] = offset
    if job_type:
        query["type"] = job_type
    return _request_json("GET", server_url, "/api/jobs", query=query)


def list_jobs(server_url: str, *, status: str, job_type: str | None) -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    offset = 0
    while True:
        page = list_jobs_page(
            server_url,
            status=status,
            job_type=job_type,
            offset=offset,
        )
        jobs.extend(page)
        if len(page) < PAGE_SIZE:
            return jobs
        offset += PAGE_SIZE


def requeue_job(server_url: str, job_id: str) -> dict[str, Any]:
    return _request_json("POST", server_url, f"/api/jobs/{job_id}/requeue")


def matching_jobs(args: argparse.Namespace) -> list[dict[str, Any]]:
    server_url = args.server_url
    if args.job_id:
        job = get_job(server_url, args.job_id)
        if args.status and job.get("status") != args.status:
            return []
        if args.job_type and job.get("type") != args.job_type:
            return []
        return [job]

    statuses = [args.status] if args.status else list(REQUEUEABLE_STATUSES)
    jobs: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for status in statuses:
        for job in list_jobs(server_url, status=status, job_type=args.job_type):
            job_id = job.get("id")
            if isinstance(job_id, str) and job_id not in seen_ids:
                jobs.append(job)
                seen_ids.add(job_id)
    return jobs


def describe_job(job: dict[str, Any]) -> str:
    fields = [
        f"id={job.get('id')}",
        f"type={job.get('type')}",
        f"status={job.get('status')}",
    ]
    if job.get("profile"):
        fields.append(f"profile={job['profile']}")
    if job.get("parent_job_id"):
        fields.append(f"parent={job['parent_job_id']}")
    return " ".join(fields)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    try:
        jobs = matching_jobs(args)
        if not jobs:
            print("No matching jobs found.")
            return 0

        action = "Would requeue" if args.dry_run else "Requeueing"
        for job in jobs:
            print(f"{action}: {describe_job(job)}")
            if not args.dry_run:
                updated = requeue_job(args.server_url, job["id"])
                print(f"  -> status={updated.get('status')} output_files={updated.get('output_files')}")

        suffix = "matched" if args.dry_run else "requeued"
        print(f"{len(jobs)} job(s) {suffix}.")
        return 0
    except ApiError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
