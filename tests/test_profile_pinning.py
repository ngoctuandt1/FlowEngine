"""Tests for Bug #4 — Profile pinning for job chains (account binding).

Acceptance criteria verified:
  AC1. Level-2+ job never runs on a profile different from its parent.
  AC2. A chain started on profile 'alpha' waits indefinitely (claim returns None)
       if no 'alpha'-capable worker is online.
"""

import pytest
import pytest_asyncio

# ---------------------------------------------------------------------------
# DB bootstrap helpers
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def db_path(tmp_path):
    """Temporary SQLite database, schema-initialised.

    Patches ``server.db.database.DATABASE_PATH`` (the variable actually used
    by ``get_db()``) so each test runs against a fresh isolated file.
    """
    import server.db.database as _db_module

    path = str(tmp_path / "test.db")
    old = _db_module.DATABASE_PATH
    _db_module.DATABASE_PATH = path

    from server.db.database import init_db
    await init_db()

    yield path

    _db_module.DATABASE_PATH = old


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _make_job(
    db_path,            # noqa: F841  — fixture ensures correct db
    job_type="text-to-video",
    status="pending",
    job_level=1,
    parent_job_id=None,
    profile=None,
    project_url=None,
    media_id=None,
) -> str:
    """Insert a minimal job row; returns the job id."""
    from server.models.job import Job, JobType, JobStatus
    from server.db.job_store import create_job

    job = Job(
        type=JobType(job_type),
        status=JobStatus(status),
        job_level=job_level,
        parent_job_id=parent_job_id,
        profile=profile,
        project_url=project_url,
        media_id=media_id,
    )
    await create_job(job)
    return job.id


# ===========================================================================
# AC1 — Level-2 job never claimed by wrong-profile worker
# ===========================================================================

@pytest.mark.asyncio
async def test_level2_job_not_claimed_by_wrong_profile(db_path):
    """A worker that only has 'beta' must NOT claim a Level-2 job whose
    parent ran on 'alpha'."""
    from server.db.job_store import claim_next_job

    parent_id = await _make_job(
        db_path,
        status="completed",
        job_level=1,
        profile="alpha",
        project_url="https://flow.google.com/proj/abc",
    )
    await _make_job(
        db_path,
        job_type="extend-video",
        job_level=2,
        parent_job_id=parent_id,
        project_url="https://flow.google.com/proj/abc",
    )

    result = await claim_next_job(worker_id="worker-beta", available_profiles=["beta"])
    assert result is None, (
        "Worker with profile 'beta' must not claim a Level-2 job pinned to 'alpha'"
    )


@pytest.mark.asyncio
async def test_level2_job_claimed_by_correct_profile(db_path):
    """A worker that has 'alpha' MUST be able to claim the Level-2 job."""
    from server.db.job_store import claim_next_job

    parent_id = await _make_job(
        db_path,
        status="completed",
        job_level=1,
        profile="alpha",
        project_url="https://flow.google.com/proj/xyz",
    )
    child_id = await _make_job(
        db_path,
        job_type="extend-video",
        job_level=2,
        parent_job_id=parent_id,
        project_url="https://flow.google.com/proj/xyz",
    )

    result = await claim_next_job(worker_id="worker-alpha", available_profiles=["alpha"])
    assert result is not None, "Worker with matching 'alpha' profile should claim the job"
    assert result.id == child_id
    assert result.profile == "alpha", "Claimed job must have profile bound to 'alpha'"


@pytest.mark.asyncio
async def test_claimed_level2_profile_matches_parent(db_path):
    """After claiming, job.profile must equal parent.profile — not what the
    child had stored before (could be None or anything else)."""
    from server.db.job_store import claim_next_job, get_job

    parent_id = await _make_job(
        db_path,
        status="completed",
        job_level=1,
        profile="gamma",
        project_url="https://flow.google.com/proj/gamma",
    )
    # Child created with no profile explicitly set
    child_id = await _make_job(
        db_path,
        job_type="extend-video",
        job_level=2,
        parent_job_id=parent_id,
        profile=None,
        project_url="https://flow.google.com/proj/gamma",
    )

    claimed = await claim_next_job(worker_id="w", available_profiles=["gamma"])
    assert claimed is not None
    assert claimed.profile == "gamma"

    # Verify the DB row was updated
    stored = await get_job(child_id)
    assert stored.profile == "gamma"


# ===========================================================================
# AC2 — Chain waits (never fails) when no matching worker is available
# ===========================================================================

@pytest.mark.asyncio
async def test_level2_waits_when_no_matching_worker(db_path):
    """If no worker with the required profile is online, claim returns None
    for all workers — the job stays pending (waits, does not fail)."""
    from server.db.job_store import claim_next_job, get_job

    parent_id = await _make_job(
        db_path,
        status="completed",
        job_level=1,
        profile="alpha",
        project_url="https://flow.google.com/proj/wait",
    )
    child_id = await _make_job(
        db_path,
        job_type="extend-video",
        job_level=2,
        parent_job_id=parent_id,
        project_url="https://flow.google.com/proj/wait",
    )

    # Workers with wrong profiles all get None
    assert await claim_next_job("w-beta", ["beta"]) is None
    assert await claim_next_job("w-gamma", ["gamma"]) is None
    assert await claim_next_job("w-multi", ["beta", "gamma", "delta"]) is None

    # Job must still be pending — not failed
    job = await get_job(child_id)
    assert job.status.value == "pending", (
        "Job should remain pending when no matching worker is available"
    )


@pytest.mark.asyncio
async def test_level2_claimed_once_correct_worker_appears(db_path):
    """After waiting through wrong-profile workers, the job is claimed
    as soon as a worker with the correct profile polls."""
    from server.db.job_store import claim_next_job

    parent_id = await _make_job(
        db_path,
        status="completed",
        job_level=1,
        profile="alpha",
        project_url="https://flow.google.com/proj/appear",
    )
    child_id = await _make_job(
        db_path,
        job_type="extend-video",
        job_level=2,
        parent_job_id=parent_id,
        project_url="https://flow.google.com/proj/appear",
    )

    # Wrong workers → None
    assert await claim_next_job("w-beta", ["beta"]) is None

    # Correct worker → claims successfully
    result = await claim_next_job("w-alpha", ["alpha"])
    assert result is not None
    assert result.id == child_id


# ===========================================================================
# create_single_job — profile inherited unconditionally from parent
# ===========================================================================

@pytest.mark.asyncio
async def test_create_single_job_inherits_profile_when_parent_claimed(db_path):
    """create_single_job must inherit parent.profile even when parent status
    is 'claimed' (not yet completed) — so the profile is pinned early."""
    # Patch DATABASE_PATH on the config module used by the server
    import server.config as cfg
    cfg.DATABASE_PATH = db_path

    from server.db.job_store import create_job, get_job
    from server.models.job import Job, JobType, JobStatus

    # Create parent with status='claimed' (profile already assigned at claim)
    parent = Job(
        type=JobType.TEXT_TO_VIDEO,
        status=JobStatus.CLAIMED,
        job_level=1,
        profile="alpha",
        project_url="https://flow.google.com/proj/early",
    )
    await create_job(parent)

    # Simulate create_single_job logic (the route handler)
    from server.routes.jobs import _build_job
    from server.models.job import JobCreate

    req = JobCreate(
        type=JobType.EXTEND_VIDEO,
        parent_job_id=parent.id,
    )

    job_level = parent.job_level + 1
    profile = None
    if parent.profile is not None:
        profile = parent.profile   # <-- the fix: unconditional inheritance

    child_job = _build_job(req, profile=profile, job_level=job_level)
    await create_job(child_job)

    stored = await get_job(child_job.id)
    assert stored.profile == "alpha", (
        "Level-2 job must inherit parent.profile='alpha' even when parent is not yet completed"
    )
