"""B5 regression — `update_job` must auto-set `completed_at` on terminal status.

Before B5: `update_job` only wrote the fields the caller passed. Since no caller
ever passed `completed_at` (and `JobUpdate` had no such field), the column
stayed NULL forever even though the job had reached a terminal state.

After B5: when `update_job` writes `status` ∈ {completed, failed, cancelled}
and the caller did NOT explicitly supply `completed_at`, the DB layer stamps
`completed_at` with `_now_iso()`. Explicit values from the caller still win.
"""

from datetime import UTC, datetime, timedelta

from server.db.job_store import (
    create_job,
    delete_jobs_by_status,
    get_job,
    list_jobs,
    update_job,
)
from server.models.job import Job, JobStatus, JobType, JobUpdate


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


# ---------------------------------------------------------------------------
# P2b — bulk-delete by status
# ---------------------------------------------------------------------------

async def _make_job_with_status(job_id: str, status: JobStatus) -> Job:
    """Helper: insert a job and update it to the requested status."""
    await create_job(_make_pending_job(job_id))
    if status != JobStatus.PENDING:
        await update_job(job_id, JobUpdate(status=status))
    return (await get_job(job_id))


async def test_bulk_delete_only_touches_target_status(db):
    """P2b: DELETE WHERE status=target leaves jobs in other statuses intact."""
    # Seed a mixed workload: 3 completed, 2 pending, 1 failed.
    for i in range(3):
        await _make_job_with_status(f"done-{i}", JobStatus.COMPLETED)
    for i in range(2):
        await _make_job_with_status(f"pend-{i}", JobStatus.PENDING)
    await _make_job_with_status("fail-0", JobStatus.FAILED)

    deleted = await delete_jobs_by_status("completed")

    assert deleted == 3, "should report rowcount of removed jobs"
    # Target status cleared.
    assert await list_jobs(status=JobStatus.COMPLETED) == []
    # Other statuses preserved.
    remaining_pending = await list_jobs(status=JobStatus.PENDING)
    assert len(remaining_pending) == 2
    remaining_failed = await list_jobs(status=JobStatus.FAILED)
    assert len(remaining_failed) == 1
    # Individual jobs still retrievable.
    for i in range(2):
        assert await get_job(f"pend-{i}") is not None
    assert await get_job("fail-0") is not None
    # Deleted jobs gone.
    for i in range(3):
        assert await get_job(f"done-{i}") is None


async def test_bulk_delete_empty_result_returns_zero(db):
    """P2b: bulk-delete against an empty status bucket returns 0 and no error."""
    # Only non-completed jobs in the DB.
    await _make_job_with_status("p-1", JobStatus.PENDING)
    await _make_job_with_status("r-1", JobStatus.RUNNING)

    deleted = await delete_jobs_by_status("completed")

    assert deleted == 0
    # Other jobs still there.
    assert await get_job("p-1") is not None
    assert await get_job("r-1") is not None
