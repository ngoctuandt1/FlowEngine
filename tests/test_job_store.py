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

from server.db.database import get_db
from server.db.job_store import create_job, get_job, update_job
from server.db.profile_store import create_profile, get_profile
from server.models.job import Job, JobStatus, JobType, JobUpdate
from server.models.profile import Profile, ProfileStatus
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


async def test_requeue_clears_job_and_profile_claim_metadata(db):
    """Requeue path: update_job(status=pending, worker_id=None, claimed_at=None) must
    clear both the jobs table (worker_id, claimed_at) and the profiles table
    (current_job_id) so the freshly-warmed profile is not stuck as 'claimed'.
    """
    profile = Profile(name="rq-profile", google_account="rq@gmail.com")
    await create_profile(profile)

    job = _make_pending_job("rq-j1")
    job.profile = "rq-profile"
    job.worker_id = "w-burn"
    await create_job(job)

    # Simulate claim: set profiles.current_job_id to point at this job.
    async with get_db() as db_conn:
        await db_conn.execute(
            "UPDATE profiles SET current_job_id = ?, worker_id = ? WHERE name = ?",
            ("rq-j1", "w-burn", "rq-profile"),
        )
        await db_conn.commit()

    # Verify setup: profile is "claimed".
    p_before = await get_profile("rq-profile")
    assert p_before.current_job_id == "rq-j1"

    # Act: requeue — reset job to pending and clear claim ownership.
    result = await update_job(
        "rq-j1",
        JobUpdate(
            status=JobStatus.PENDING,
            worker_id=None,
            claimed_at=None,
            error=None,
        ),
    )

    # Assert: job claim metadata is cleared.
    assert result is not None
    assert result.status == JobStatus.PENDING
    assert result.worker_id is None
    assert result.claimed_at is None

    # Assert: profiles table is also cleared (DB-level verification).
    p_after = await get_profile("rq-profile")
    assert p_after.current_job_id is None, (
        "profiles.current_job_id must be cleared on requeue so the profile "
        "is not stuck as 'claimed' while awaiting re-warm."
    )
    assert p_after.worker_id is None
