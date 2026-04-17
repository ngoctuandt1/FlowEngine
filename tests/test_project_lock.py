"""Tests for bug #7 — project-level serialisation lock.

Acceptance criteria (from issue #7):
  AC1. Two jobs targeting the same project_url never run concurrently
       across the fleet.
  AC2. When job A holds the lock, job B on the same project waits and
       is picked up as soon as A reaches a terminal status.
  AC3. Worker crash clears the lock within TTL — no permanent deadlock.
"""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timedelta

import pytest
import pytest_asyncio

# Configure DB path BEFORE importing server modules (they read env at import).
_DB_FD, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="flowengine-test-")
os.close(_DB_FD)
os.environ["DATABASE_PATH"] = _DB_PATH

from server.db import job_store
from server.db.database import get_db, init_db
from server.models.job import Job, JobStatus, JobType, JobUpdate


PROJECT_A = "https://labs.google/fx/tools/flow/project/aaaa"
PROJECT_B = "https://labs.google/fx/tools/flow/project/bbbb"


def teardown_module(module):  # noqa: D401 - pytest hook
    try:
        os.unlink(_DB_PATH)
    except OSError:
        pass


@pytest_asyncio.fixture(autouse=True)
async def clean_db():
    await init_db()
    async with get_db() as db:
        await db.execute("DELETE FROM jobs")
        await db.commit()
    yield


async def _insert_parent(profile: str, project_url: str) -> Job:
    """Create a COMPLETED Level-1 job so a Level-2 child becomes claimable."""
    parent = Job(
        type=JobType.TEXT_TO_VIDEO,
        status=JobStatus.COMPLETED,
        job_level=1,
        profile=profile,
        project_url=project_url,
        media_id="media-1",
    )
    await job_store.create_job(parent)
    return parent


async def _insert_child(parent: Job, *, status: JobStatus = JobStatus.PENDING) -> Job:
    child = Job(
        type=JobType.EXTEND_VIDEO,
        status=status,
        job_level=2,
        parent_job_id=parent.id,
        profile=parent.profile if status != JobStatus.PENDING else None,
        project_url=parent.project_url,
        media_id=parent.media_id,
    )
    await job_store.create_job(child)
    return child


# ---------------------------------------------------------------------------
# AC1 — Two jobs on same project_url never run concurrently
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ac1_level2_child_blocked_by_active_sibling():
    """Child B on same project_url is not claimable while sibling A is running."""
    parent = await _insert_parent("alpha", PROJECT_A)
    running_sibling = await _insert_child(parent, status=JobStatus.RUNNING)
    assert running_sibling.status == JobStatus.RUNNING

    pending = await _insert_child(parent, status=JobStatus.PENDING)

    claimed = await job_store.claim_next_job("worker-1", ["alpha"])
    # No job should be claimable: Level-2 child is blocked by the active sibling.
    assert claimed is None or claimed.id != pending.id


@pytest.mark.asyncio
async def test_ac1_level1_with_explicit_project_url_blocked():
    """Two Level-1 jobs with the same explicit project_url can't both run."""
    # A Level-1 job already running on PROJECT_A (simulates an in-progress run
    # where the worker has stored project_url back on the job).
    running = Job(
        type=JobType.TEXT_TO_VIDEO,
        status=JobStatus.RUNNING,
        job_level=1,
        profile="alpha",
        project_url=PROJECT_A,
        worker_id="worker-0",
    )
    await job_store.create_job(running)

    # A second Level-1 job pinned to the same project_url (e.g. user submitted
    # another t2v pointing at the existing project).
    pending = Job(
        type=JobType.TEXT_TO_VIDEO,
        status=JobStatus.PENDING,
        job_level=1,
        profile="alpha",
        project_url=PROJECT_A,
    )
    await job_store.create_job(pending)

    claimed = await job_store.claim_next_job("worker-1", ["alpha"])
    assert claimed is None, (
        "Level-1 job on same project_url must not be claimed while another "
        "job is active on that project"
    )


@pytest.mark.asyncio
async def test_ac1_different_projects_do_not_block_each_other():
    """Jobs on distinct project_urls claim independently."""
    parent_a = await _insert_parent("alpha", PROJECT_A)
    _running_on_a = await _insert_child(parent_a, status=JobStatus.RUNNING)

    parent_b = await _insert_parent("beta", PROJECT_B)
    pending_on_b = await _insert_child(parent_b, status=JobStatus.PENDING)

    claimed = await job_store.claim_next_job("worker-1", ["alpha", "beta"])
    assert claimed is not None
    assert claimed.id == pending_on_b.id


# ---------------------------------------------------------------------------
# AC2 — B waits, then is picked up when A terminates
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", [JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED])
async def test_ac2_waiter_claimable_once_holder_terminal(terminal):
    parent = await _insert_parent("alpha", PROJECT_A)
    holder = await _insert_child(parent, status=JobStatus.RUNNING)
    waiter = await _insert_child(parent, status=JobStatus.PENDING)

    # Before terminal: not claimable
    claimed = await job_store.claim_next_job("worker-1", ["alpha"])
    assert claimed is None

    # Transition holder to a terminal status
    await job_store.update_job(holder.id, JobUpdate(status=terminal))

    claimed = await job_store.claim_next_job("worker-1", ["alpha"])
    assert claimed is not None
    assert claimed.id == waiter.id
    assert claimed.status == JobStatus.CLAIMED


# ---------------------------------------------------------------------------
# AC3 — TTL recovery releases the lock after worker crash
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ac3_stale_recovery_unblocks_same_project_job():
    parent = await _insert_parent("alpha", PROJECT_A)

    # Insert a stale RUNNING job whose worker died: updated_at in the past.
    stale = Job(
        type=JobType.EXTEND_VIDEO,
        status=JobStatus.RUNNING,
        job_level=2,
        parent_job_id=parent.id,
        profile="alpha",
        project_url=PROJECT_A,
        worker_id="dead-worker",
    )
    await job_store.create_job(stale)
    old = (datetime.utcnow() - timedelta(hours=2)).isoformat()
    async with get_db() as db:
        await db.execute(
            "UPDATE jobs SET updated_at = ? WHERE id = ?", (old, stale.id)
        )
        await db.commit()

    waiter = await _insert_child(parent, status=JobStatus.PENDING)

    # Before recovery: waiter is blocked.
    blocked = await job_store.claim_next_job("worker-1", ["alpha"])
    assert blocked is None

    # Recovery (30-min default TTL) flips stale job back to pending,
    # which releases the project lock.
    recovered = await job_store.recover_stale_jobs(stale_minutes=30)
    assert any(j.id == stale.id for j in recovered)

    # The dead-worker's job is now pending; the waiter or the recovered
    # job can be claimed (whichever is oldest). Both must not run together.
    first = await job_store.claim_next_job("worker-1", ["alpha"])
    assert first is not None
    # Try to claim a second concurrent one for the same project — must fail.
    second = await job_store.claim_next_job("worker-2", ["alpha"])
    assert second is None
    # Kind of a double-check: only one of {stale, waiter} was picked.
    assert first.id in {stale.id, waiter.id}
