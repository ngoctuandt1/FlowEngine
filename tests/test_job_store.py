"""B5 regression — `update_job` must auto-set `completed_at` on terminal status.

Before B5: `update_job` only wrote the fields the caller passed. Since no caller
ever passed `completed_at` (and `JobUpdate` had no such field), the column
stayed NULL forever even though the job had reached a terminal state.

After B5: when `update_job` writes `status` ∈ {completed, failed, cancelled}
and the caller did NOT explicitly supply `completed_at`, the DB layer stamps
`completed_at` with `_now_iso()`. Explicit values from the caller still win.
"""

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from server.db.job_store import create_job, get_job, update_job
from server.models.job import Job, JobStatus, JobType, JobUpdate
from server.models.project import Project
from server.db.project_store import create_project


def _make_pending_job(job_id: str) -> Job:
    now = datetime.now(UTC)
    return Job(
        id=job_id,
        type=JobType.TEXT_TO_VIDEO,
        status=JobStatus.PENDING,
        prompt="x",
        created_at=now,
        updated_at=now,
    )


async def test_completed_at_auto_set_on_completed(db):
    """B5: status → completed must auto-populate completed_at."""
    await create_job(_make_pending_job("b5-a"))
    assert (await get_job("b5-a")).completed_at is None

    before = datetime.now(UTC)
    await update_job("b5-a", JobUpdate(status=JobStatus.COMPLETED))
    after = datetime.now(UTC)

    updated = await get_job("b5-a")
    assert updated.status == JobStatus.COMPLETED
    assert updated.completed_at is not None, "completed_at must be set on completion"
    # Auto-set value should sit between the call bracket (tz-aware compare).
    assert before <= updated.completed_at <= after


async def test_completed_at_auto_set_on_failed(db):
    """B5: failed is terminal — completed_at must also be stamped."""
    await create_job(_make_pending_job("b5-b"))

    await update_job(
        "b5-b",
        JobUpdate(status=JobStatus.FAILED, error="boom"),
    )

    updated = await get_job("b5-b")
    assert updated.status == JobStatus.FAILED
    assert updated.completed_at is not None


async def test_completed_at_explicit_wins_over_auto_set(db):
    """B5: when the caller passes completed_at explicitly, auto-set is skipped."""
    await create_job(_make_pending_job("b5-c"))

    explicit = datetime.now(UTC) - timedelta(hours=1)
    await update_job(
        "b5-c",
        JobUpdate(status=JobStatus.COMPLETED, completed_at=explicit),
    )

    updated = await get_job("b5-c")
    assert updated.completed_at is not None
    # Round-trip via ISO string — allow sub-second drift only.
    drift = abs((updated.completed_at - explicit).total_seconds())
    assert drift < 1, f"explicit completed_at lost in round-trip: drift={drift}s"


async def test_completed_at_not_set_on_non_terminal_status(db):
    """B5: status=running must NOT auto-set completed_at."""
    await create_job(_make_pending_job("b5-d"))

    await update_job("b5-d", JobUpdate(status=JobStatus.RUNNING))

    updated = await get_job("b5-d")
    assert updated.status == JobStatus.RUNNING
    assert updated.completed_at is None, (
        "Non-terminal status transitions must not populate completed_at"
    )


async def test_create_job_rejects_unknown_project_id_on_fresh_schema(db):
    job = _make_pending_job("f2-project-id-a")
    job.project_id = "missing-project"

    with pytest.raises(sqlite3.IntegrityError):
        await create_job(job)


async def test_delete_project_nulls_linked_job_project_ids(db):
    project = Project(name="Linked Project")
    await create_project(project)

    job = _make_pending_job("f2-project-id-b")
    job.project_id = project.id
    await create_job(job)

    from server.db.project_store import delete_project

    deleted = await delete_project(project.id)
    assert deleted is True

    updated = await get_job("f2-project-id-b")
    assert updated.project_id is None
