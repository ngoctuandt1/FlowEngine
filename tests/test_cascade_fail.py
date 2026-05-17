from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

from server.db.job_store import create_job, get_job, update_job
from server.db.profile_store import create_profile, get_profile
from server.models.job import Job, JobStatus, JobType, JobUpdate
from server.models.profile import Profile, ProfileStatus


def _job(
    job_id: str,
    *,
    parent_job_id: str | None = None,
    job_level: int = 1,
    status: JobStatus = JobStatus.PENDING,
    job_type: JobType = JobType.TEXT_TO_VIDEO,
    seconds: int = 0,
) -> Job:
    now = datetime.now(UTC) + timedelta(seconds=seconds)
    return Job(
        id=job_id,
        type=job_type,
        status=status,
        job_level=job_level,
        parent_job_id=parent_job_id,
        chain_id="cascade-chain",
        prompt=f"prompt {job_id}",
        created_at=now,
        updated_at=now,
        completed_at=now if status in {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED} else None,
    )


async def _create_jobs(*jobs: Job) -> None:
    for job in jobs:
        await create_job(job)


async def test_single_child_cascades_when_parent_fails(db):
    await _create_jobs(
        _job("parent", seconds=1),
        _job("child", parent_job_id="parent", job_level=2, job_type=JobType.EXTEND_VIDEO, seconds=2),
    )

    await update_job("parent", JobUpdate(status=JobStatus.FAILED, error="boom"))

    child = await get_job("child")
    assert child.status == JobStatus.CANCELLED
    assert child.error == "parent_failed: parent (failed)"
    assert child.completed_at is not None


async def test_deep_descendants_cascade_from_failed_l2(db):
    await _create_jobs(
        _job("l1", status=JobStatus.COMPLETED, seconds=1),
        _job("l2", parent_job_id="l1", job_level=2, job_type=JobType.EXTEND_VIDEO, seconds=2),
        _job("l3", parent_job_id="l2", job_level=3, job_type=JobType.EXTEND_VIDEO, seconds=3),
        _job("l4", parent_job_id="l3", job_level=4, status=JobStatus.RUNNING, job_type=JobType.CAMERA_MOVE, seconds=4),
    )

    await update_job("l2", JobUpdate(status=JobStatus.FAILED, error="l2 failed"))

    assert (await get_job("l1")).status == JobStatus.COMPLETED
    assert (await get_job("l2")).status == JobStatus.FAILED
    assert (await get_job("l3")).status == JobStatus.CANCELLED
    assert (await get_job("l4")).status == JobStatus.CANCELLED
    assert (await get_job("l4")).error == "parent_failed: l2 (failed)"


async def test_sibling_branches_untouched(db):
    await _create_jobs(
        _job("l1", seconds=1),
        _job("bad-l2", parent_job_id="l1", job_level=2, job_type=JobType.EXTEND_VIDEO, seconds=2),
        _job("bad-l3", parent_job_id="bad-l2", job_level=3, job_type=JobType.EXTEND_VIDEO, seconds=3),
        _job("pending-l2", parent_job_id="l1", job_level=2, job_type=JobType.CAMERA_MOVE, seconds=4),
        _job("pending-branch-l3", parent_job_id="pending-l2", job_level=3, job_type=JobType.EXTEND_VIDEO, seconds=5),
        _job("completed-l2", parent_job_id="l1", job_level=2, status=JobStatus.COMPLETED, job_type=JobType.REMOVE_OBJECT, seconds=6),
    )

    await update_job("bad-l2", JobUpdate(status=JobStatus.FAILED, error="bad branch"))

    assert (await get_job("bad-l3")).status == JobStatus.CANCELLED
    assert (await get_job("pending-l2")).status == JobStatus.PENDING
    assert (await get_job("pending-branch-l3")).status == JobStatus.PENDING
    assert (await get_job("completed-l2")).status == JobStatus.COMPLETED


async def test_already_terminal_descendant_untouched_on_refail(db):
    completed_at = datetime.now(UTC) - timedelta(hours=1)
    completed_l3 = _job(
        "completed-l3",
        parent_job_id="l2",
        job_level=3,
        status=JobStatus.COMPLETED,
        job_type=JobType.EXTEND_VIDEO,
        seconds=3,
    )
    completed_l3.completed_at = completed_at
    completed_l3.error = "keep me"
    await _create_jobs(
        _job("l1", seconds=1),
        _job("l2", parent_job_id="l1", job_level=2, job_type=JobType.EXTEND_VIDEO, seconds=2),
        completed_l3,
    )

    await update_job("l2", JobUpdate(status=JobStatus.FAILED, error="first fail"))
    await update_job("l2", JobUpdate(status=JobStatus.FAILED, error="second fail"))

    l3 = await get_job("completed-l3")
    assert l3.status == JobStatus.COMPLETED
    assert l3.error == "keep me"
    assert l3.completed_at == completed_at


async def test_cascade_emits_ws_broadcast_for_cancelled_child(db, monkeypatch):
    import server.routes.ws as ws_route

    await _create_jobs(
        _job("parent", seconds=1),
        _job("child", parent_job_id="parent", job_level=2, job_type=JobType.EXTEND_VIDEO, seconds=2),
    )
    broadcast = AsyncMock()
    monkeypatch.setattr(ws_route, "broadcast_job_update", broadcast)

    await update_job("parent", JobUpdate(status=JobStatus.FAILED, error="boom"))

    assert broadcast.await_count == 1
    broadcasted = broadcast.await_args.args[0]
    assert broadcasted.id == "child"
    assert broadcasted.status == JobStatus.CANCELLED
    assert broadcasted.error == "parent_failed: parent (failed)"


async def test_profile_claims_cleared_for_each_cascaded_job(db):
    await create_profile(
        Profile(
            name="profile-child-a",
            status=ProfileStatus.AVAILABLE,
            current_job_id="child-a",
            worker_id="worker-a",
        )
    )
    await create_profile(
        Profile(
            name="profile-child-b",
            status=ProfileStatus.AVAILABLE,
            current_job_id="child-b",
            worker_id="worker-b",
        )
    )
    await _create_jobs(
        _job("parent", seconds=1),
        _job("child-a", parent_job_id="parent", job_level=2, job_type=JobType.EXTEND_VIDEO, seconds=2),
        _job("child-b", parent_job_id="child-a", job_level=3, status=JobStatus.RUNNING, job_type=JobType.EXTEND_VIDEO, seconds=3),
    )

    await update_job("parent", JobUpdate(status=JobStatus.CANCELLED, error="user cancelled"))

    profile_a = await get_profile("profile-child-a")
    profile_b = await get_profile("profile-child-b")
    assert profile_a.current_job_id is None
    assert profile_a.worker_id is None
    assert profile_b.current_job_id is None
    assert profile_b.worker_id is None


async def test_cascade_is_idempotent_on_repeated_parent_failure(db):
    await _create_jobs(
        _job("parent", seconds=1),
        _job("child", parent_job_id="parent", job_level=2, job_type=JobType.EXTEND_VIDEO, seconds=2),
    )

    await update_job("parent", JobUpdate(status=JobStatus.FAILED, error="first"))
    first = await get_job("child")

    await update_job("parent", JobUpdate(status=JobStatus.FAILED, error="second"))
    second = await get_job("child")

    assert second.status == JobStatus.CANCELLED
    assert second.error == first.error
    assert second.completed_at == first.completed_at
    assert second.updated_at == first.updated_at
