"""B6 regression — `profiles.current_job_id` must mirror the live job state.

Before B6: `claim_next_job` wrote to the `jobs` row but never touched the
`profiles` row. `update_job` never cleared `current_job_id` either. The
column stayed NULL forever — the dashboard could not show which profile
was running which job.

After B6:
- `claim_next_job` stamps `profiles.current_job_id = <claimed job.id>` in
  the same transaction as the jobs UPDATE (so the two rows never diverge).
- `update_job` clears `profiles.current_job_id` when the job transitions
  to a terminal state (`completed` / `failed` / `cancelled`).
- Non-terminal transitions (e.g. `running`) must NOT clear the pointer —
  the profile is still busy, the job just moved between live states.
"""

from datetime import UTC, datetime
from typing import Optional

from server.db.job_store import claim_next_job, create_job, update_job
from server.db.profile_store import create_profile, get_profile
from server.models.job import Job, JobStatus, JobType, JobUpdate
from server.models.profile import Profile, ProfileStatus


def _make_profile(
    name: str,
    current_job_id: Optional[str] = None,
    worker_id: Optional[str] = None,
) -> Profile:
    return Profile(
        name=name,
        google_account=f"{name}@example.com",
        locale="en",
        tier="ultra",
        status=ProfileStatus.AVAILABLE,
        current_job_id=current_job_id,
        worker_id=worker_id,
        created_at=datetime.now(UTC),
    )


def _make_pending_l1_job(job_id: str) -> Job:
    now = datetime.now(UTC)
    return Job(
        id=job_id,
        type=JobType.TEXT_TO_VIDEO,
        status=JobStatus.PENDING,
        job_level=1,
        prompt="x",
        created_at=now,
        updated_at=now,
    )


async def test_profile_current_job_set_on_claim(db):
    """B6: claim_next_job must stamp profile.current_job_id with the job.id."""
    await create_profile(_make_profile("b6-prof-a"))
    await create_job(_make_pending_l1_job("b6-job-a"))

    assert (await get_profile("b6-prof-a")).current_job_id is None

    claimed = await claim_next_job("worker-1", ["b6-prof-a"])
    assert claimed is not None, "expected the pending L1 job to be claimable"
    assert claimed.id == "b6-job-a"
    assert claimed.profile == "b6-prof-a"

    profile = await get_profile("b6-prof-a")
    assert profile.current_job_id == "b6-job-a", (
        "claim must stamp profile.current_job_id so the dashboard can "
        "show which profile is running which job"
    )


async def test_profile_current_job_cleared_on_completion(db):
    """B6: update_job with a terminal status must clear profile.current_job_id."""
    await create_profile(
        _make_profile(
            "b6-prof-b",
            current_job_id="b6-job-b",
            worker_id="worker-1",
        )
    )
    await create_job(_make_pending_l1_job("b6-job-b"))
    assert (await get_profile("b6-prof-b")).current_job_id == "b6-job-b"

    await update_job("b6-job-b", JobUpdate(status=JobStatus.COMPLETED))

    profile = await get_profile("b6-prof-b")
    assert profile.current_job_id is None, (
        "terminal status must release the profile pointer so a new job "
        "can claim it"
    )
    assert profile.worker_id is None, (
        "terminal status must also clear the mirrored worker_id so the "
        "profile row no longer looks busy"
    )


async def test_profile_current_job_not_cleared_on_running(db):
    """B6: non-terminal transitions must leave current_job_id untouched."""
    await create_profile(_make_profile("b6-prof-c", current_job_id="b6-job-c"))
    await create_job(_make_pending_l1_job("b6-job-c"))

    await update_job("b6-job-c", JobUpdate(status=JobStatus.RUNNING))

    profile = await get_profile("b6-prof-c")
    assert profile.current_job_id == "b6-job-c", (
        "running is not terminal — the profile is still busy with this job"
    )
