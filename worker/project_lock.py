"""Per-project serialisation lock.

Ensures at most one job runs against a given project_url at a time.
This prevents concurrent browser sessions from conflicting
on the same Flow project.
"""

import logging

logger = logging.getLogger(__name__)


class ProjectLock:
    """In-memory project-level mutex (single worker process)."""

    def __init__(self) -> None:
        self._locks: dict[str, str] = {}  # project_url -> job_id

    def acquire(self, project_url: str, job_id: str) -> bool:
        """Try to acquire the lock for *project_url*.

        Returns True on success.  Returns False if the project is
        already locked by a different job.
        """
        holder = self._locks.get(project_url)
        if holder is not None and holder != job_id:
            logger.warning(
                "Project %s locked by job %s, cannot acquire for job %s",
                project_url, holder, job_id,
            )
            return False
        self._locks[project_url] = job_id
        logger.info("Project lock ACQUIRED: %s -> job %s", project_url, job_id)
        return True

    def release(self, project_url: str) -> None:
        """Release the lock for *project_url* (idempotent)."""
        removed = self._locks.pop(project_url, None)
        if removed:
            logger.info(
                "Project lock RELEASED: %s (was job %s)", project_url, removed
            )

    def __repr__(self) -> str:
        return f"<ProjectLock locks={dict(self._locks)}>"
