"""Per-project in-flight cap.

Limits how many jobs can run concurrently on the same Flow project_url.
Default cap = 1 preserves the historical mutex semantics; raise via
``FLOW_PROJECT_INFLIGHT`` (or constructor arg) to allow N concurrent
operations against the same project. Useful when a single L1 fans out
into multiple L2/L3 children that the operator wants to run in parallel
on the same Flow project page.

Tracking is idempotent on the (project_url, job_id) pair so a duplicate
acquire from the same job is a no-op (returns True). Each unique job_id
counts toward the cap exactly once.
"""

from __future__ import annotations

import logging
import os
from typing import Iterable

logger = logging.getLogger(__name__)


def _resolve_default_cap() -> int:
    raw = os.environ.get("FLOW_PROJECT_INFLIGHT", "1").strip()
    try:
        value = int(raw)
    except ValueError:
        return 1
    return max(1, value)


class ProjectLock:
    """In-memory per-project semaphore (single worker process)."""

    def __init__(self, max_inflight: int | None = None) -> None:
        if max_inflight is None:
            max_inflight = _resolve_default_cap()
        self._max_inflight = max(1, int(max_inflight))
        # project_url -> set of job_ids currently holding a slot
        self._inflight: dict[str, set[str]] = {}

    @property
    def max_inflight(self) -> int:
        return self._max_inflight

    def acquire(self, project_url: str, job_id: str) -> bool:
        """Try to acquire one in-flight slot for *project_url* on behalf of *job_id*.

        Returns True if the job already holds a slot (idempotent), or if a
        slot was successfully claimed. Returns False when the project is at
        capacity with other jobs.
        """
        holders = self._inflight.setdefault(project_url, set())
        if job_id in holders:
            return True
        if len(holders) >= self._max_inflight:
            logger.warning(
                "Project %s at capacity %d/%d (jobs=%s); cannot acquire for job %s",
                project_url, len(holders), self._max_inflight,
                sorted(holders), job_id,
            )
            return False
        holders.add(job_id)
        logger.info(
            "Project lock ACQUIRED: %s -> job %s (slot %d/%d)",
            project_url, job_id, len(holders), self._max_inflight,
        )
        return True

    def release(self, project_url: str, job_id: str | None = None) -> None:
        """Release a slot.

        With ``job_id`` omitted the call clears every holder for the project
        — kept for back-compat with the historical mutex API. Pass a
        specific ``job_id`` to release only that job's slot.
        """
        holders = self._inflight.get(project_url)
        if not holders:
            return
        if job_id is None:
            removed = sorted(holders)
            self._inflight.pop(project_url, None)
            logger.info(
                "Project lock RELEASED: %s (was jobs %s)", project_url, removed,
            )
            return
        if job_id in holders:
            holders.discard(job_id)
            logger.info(
                "Project lock RELEASED: %s -> job %s (remaining %d/%d)",
                project_url, job_id, len(holders), self._max_inflight,
            )
        if not holders:
            self._inflight.pop(project_url, None)

    def held_by(self, project_url: str) -> Iterable[str]:
        return tuple(self._inflight.get(project_url, ()))

    def __repr__(self) -> str:
        snapshot = {url: sorted(holders) for url, holders in self._inflight.items()}
        return f"<ProjectLock cap={self._max_inflight} inflight={snapshot}>"
