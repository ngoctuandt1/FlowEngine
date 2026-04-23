"""Batch-claim algorithm (PR-2 of batch-mode epic).

``claim_next_batch`` extends the single-job claim with sibling fan-out: after
picking a head L2 job (same Priority-1 criteria as ``claim_next_job``), the
call also drains pending L2 jobs sharing the head's ``parent.project_url``,
up to ``max_size``. Each claimed row inherits from its own direct parent
(B22 per-row), not from the head — so a batch that spans chain branches
still lands correct ``media_id`` / ``edit_url`` on each child.

Fallback: when no L2 is available the head pick degrades to a single L1
wrapped in a one-item list so callers can keep a uniform batch-shape.
"""

from datetime import UTC, datetime, timedelta
from typing import Optional

from server.db.job_store import (
    claim_next_batch,
    claim_next_job,
    create_job,
    get_job,
    update_job,
)
from server.db.profile_store import create_profile, get_profile
from server.models.job import Job, JobStatus, JobType, JobUpdate
from server.models.profile import Profile, ProfileStatus


# -- Fixtures mirrored from test_claim_algorithm.py ---------------------------

def _make_profile(name: str) -> Profile:
    return Profile(
        name=name,
        google_account=f"{name}@example.com",
        locale="en",
        tier="ultra",
        status=ProfileStatus.AVAILABLE,
        created_at=datetime.now(UTC),
    )


def _make_completed_parent(
    job_id: str,
    profile: str,
    project_url: str,
    media_id: str,
    edit_url: Optional[str] = None,
    *,
    job_type: JobType = JobType.TEXT_TO_VIDEO,
    job_level: int = 1,
    parent_job_id: Optional[str] = None,
    created_at: Optional[datetime] = None,
) -> Job:
    now = created_at or datetime.now(UTC)
    return Job(
        id=job_id,
        type=job_type,
        status=JobStatus.COMPLETED,
        job_level=job_level,
        parent_job_id=parent_job_id,
        profile=profile,
        project_url=project_url,
        media_id=media_id,
        edit_url=edit_url,
        prompt="parent prompt" if job_type == JobType.TEXT_TO_VIDEO else None,
        direction="Dolly in" if job_type == JobType.CAMERA_MOVE else None,
        created_at=now,
        updated_at=now,
        completed_at=now,
    )


def _make_pending_child(
    job_id: str,
    parent_id: str,
    *,
    project_url: Optional[str] = None,
    media_id: Optional[str] = None,
    edit_url: Optional[str] = None,
    job_type: JobType = JobType.CAMERA_MOVE,
    job_level: int = 2,
    created_at: Optional[datetime] = None,
) -> Job:
    now = created_at or datetime.now(UTC)
    return Job(
        id=job_id,
        type=job_type,
        status=JobStatus.PENDING,
        job_level=job_level,
        parent_job_id=parent_id,
        direction="Dolly in" if job_type == JobType.CAMERA_MOVE else None,
        project_url=project_url,
        media_id=media_id,
        edit_url=edit_url,
        created_at=now,
        updated_at=now,
    )


async def _seed_one_chain(profile_name: str, project_url: str, media_id: str,
                          n_children: int, *, chain_suffix: str = "a",
                          base_time: Optional[datetime] = None) -> list[str]:
    """Create 1 completed parent + n pending L2 children on the same project.

    Returns the list of child ids in creation order.
    """
    await create_profile(_make_profile(profile_name))
    parent_id = f"par-{chain_suffix}"
    t0 = base_time or datetime.now(UTC)
    await create_job(
        _make_completed_parent(
            parent_id,
            profile=profile_name,
            project_url=project_url,
            media_id=media_id,
            edit_url=f"{project_url}/edit/{media_id}",
            created_at=t0,
        )
    )

    child_ids: list[str] = []
    for i in range(n_children):
        cid = f"child-{chain_suffix}-{i}"
        await create_job(
            _make_pending_child(
                cid,
                parent_id,
                created_at=t0 + timedelta(seconds=i + 1),
            )
        )
        child_ids.append(cid)
    return child_ids


# -- Tests --------------------------------------------------------------------

async def test_head_only_returned_when_no_siblings(db):
    [cid] = await _seed_one_chain("p-solo", "https://f/p/solo", "mid-solo", 1)

    batch = await claim_next_batch("worker-1", ["p-solo"])

    assert [j.id for j in batch] == [cid]
    assert batch[0].status == JobStatus.CLAIMED
    assert batch[0].project_url == "https://f/p/solo"
    assert batch[0].media_id == "mid-solo"


async def test_sibling_fan_out_on_same_project_url(db):
    child_ids = await _seed_one_chain("p-fan", "https://f/p/fan", "mid-fan", 3)

    batch = await claim_next_batch("worker-1", ["p-fan"])

    assert [j.id for j in batch] == child_ids  # created_at ASC
    assert all(j.status == JobStatus.CLAIMED for j in batch)


async def test_max_size_caps_batch(db):
    child_ids = await _seed_one_chain("p-cap", "https://f/p/cap", "mid-cap", 10)

    batch = await claim_next_batch("worker-1", ["p-cap"], max_size=3)

    assert [j.id for j in batch] == child_ids[:3]
    # Untouched leftovers stay pending and claimable on a follow-up.
    for cid in child_ids[3:]:
        job = await get_job(cid)
        assert job.status == JobStatus.PENDING


async def test_different_project_urls_not_batched(db):
    t0 = datetime.now(UTC)
    await create_profile(_make_profile("p-dual"))
    # Chain A on project A — created first so it becomes the head.
    await create_job(_make_completed_parent(
        "par-A", "p-dual", "https://f/p/A", "mid-A",
        created_at=t0,
    ))
    await create_job(_make_pending_child(
        "child-A1", "par-A", created_at=t0 + timedelta(seconds=1),
    ))
    # Chain B on project B — pending child created later.
    await create_job(_make_completed_parent(
        "par-B", "p-dual", "https://f/p/B", "mid-B",
        created_at=t0 + timedelta(seconds=2),
    ))
    await create_job(_make_pending_child(
        "child-B1", "par-B", created_at=t0 + timedelta(seconds=3),
    ))

    batch = await claim_next_batch("worker-1", ["p-dual"])

    # Only chain A returned. Chain B stays pending; its project_url is
    # different so it is not a batch-sibling.
    assert [j.id for j in batch] == ["child-A1"]
    b1 = await get_job("child-B1")
    assert b1.status == JobStatus.PENDING


async def test_b22_inheritance_per_row(db):
    """Each claimed row inherits from its OWN direct parent, not the head's.

    Chain A and chain B share project_url (same Flow project) but have
    different media_ids / edit_urls (different generations in the project).
    Without per-row B22 binding, every child would land with the head's
    media_id — wrong for non-head siblings.
    """
    t0 = datetime.now(UTC)
    proj = "https://f/p/shared"
    await create_profile(_make_profile("p-b22"))
    await create_job(_make_completed_parent(
        "par-A", "p-b22", proj, "mid-A",
        edit_url=f"{proj}/edit/mid-A", created_at=t0,
    ))
    await create_job(_make_completed_parent(
        "par-B", "p-b22", proj, "mid-B",
        edit_url=f"{proj}/edit/mid-B",
        created_at=t0 + timedelta(seconds=1),
    ))
    await create_job(_make_pending_child(
        "child-A", "par-A", created_at=t0 + timedelta(seconds=2),
    ))
    await create_job(_make_pending_child(
        "child-B", "par-B", created_at=t0 + timedelta(seconds=3),
    ))

    batch = await claim_next_batch("worker-1", ["p-b22"])

    assert [j.id for j in batch] == ["child-A", "child-B"]
    by_id = {j.id: j for j in batch}
    assert by_id["child-A"].media_id == "mid-A"
    assert by_id["child-A"].edit_url == f"{proj}/edit/mid-A"
    assert by_id["child-B"].media_id == "mid-B"
    assert by_id["child-B"].edit_url == f"{proj}/edit/mid-B"


async def test_l1_fallback_wraps_in_list(db):
    await create_profile(_make_profile("p-l1"))
    l1 = Job(
        id="l1-solo",
        type=JobType.TEXT_TO_VIDEO,
        status=JobStatus.PENDING,
        job_level=1,
        prompt="solo l1",
    )
    await create_job(l1)

    batch = await claim_next_batch("worker-1", ["p-l1"])

    assert len(batch) == 1
    assert batch[0].id == "l1-solo"
    assert batch[0].status == JobStatus.CLAIMED
    assert batch[0].profile == "p-l1"


async def test_profile_filter_excludes_unmatched(db):
    # Parent on profile A; worker only offers profile B — nothing claimable.
    await _seed_one_chain("p-match", "https://f/p/m", "mid-m", 2)

    batch = await claim_next_batch("worker-1", ["p-other"])

    assert batch == []


async def test_active_project_blocks_batch(db):
    """A claimed/running job on the same project_url blocks a new batch."""
    t0 = datetime.now(UTC)
    await create_profile(_make_profile("p-block"))
    await create_job(_make_completed_parent(
        "par-x", "p-block", "https://f/p/x", "mid-x",
        edit_url="https://f/p/x/edit/mid-x", created_at=t0,
    ))
    # Existing claimed job on the same project — simulates another worker
    # already handling this project.
    await create_job(Job(
        id="busy",
        type=JobType.EXTEND_VIDEO,
        status=JobStatus.CLAIMED,
        job_level=2,
        parent_job_id="par-x",
        profile="p-block",
        project_url="https://f/p/x",
        media_id="mid-x",
        worker_id="other-worker",
        created_at=t0 + timedelta(seconds=1),
        updated_at=t0 + timedelta(seconds=1),
    ))
    await create_job(_make_pending_child(
        "child-blocked", "par-x",
        created_at=t0 + timedelta(seconds=2),
    ))

    batch = await claim_next_batch("worker-1", ["p-block"])

    assert batch == []
    still_pending = await get_job("child-blocked")
    assert still_pending.status == JobStatus.PENDING


async def test_empty_when_nothing_eligible(db):
    batch = await claim_next_batch("worker-1", ["p-absent"])
    assert batch == []


async def test_claimed_rows_marked_claimed(db):
    child_ids = await _seed_one_chain("p-mark", "https://f/p/m", "mid-m", 2)

    batch = await claim_next_batch("worker-1", ["p-mark"])

    assert {j.id for j in batch} == set(child_ids)
    for j in batch:
        assert j.status == JobStatus.CLAIMED
        assert j.worker_id == "worker-1"
        assert j.claimed_at is not None


async def test_profiles_table_mirrored(db):
    child_ids = await _seed_one_chain("p-mirror", "https://f/p/mir", "mid-mir", 3)

    batch = await claim_next_batch("worker-1", ["p-mirror"])
    assert [j.id for j in batch] == child_ids

    prof = await get_profile("p-mirror")
    # B6 mirror: last claim wins.
    assert prof.current_job_id == child_ids[-1]
    assert prof.worker_id == "worker-1"


async def test_max_size_one_degenerates_to_single_pick(db):
    child_ids = await _seed_one_chain("p-one", "https://f/p/one", "mid-one", 3)

    batch = await claim_next_batch("worker-1", ["p-one"], max_size=1)

    assert [j.id for j in batch] == [child_ids[0]]
    # The other two stay pending for a second worker/tick.
    for cid in child_ids[1:]:
        assert (await get_job(cid)).status == JobStatus.PENDING


async def test_max_size_zero_returns_empty(db):
    await _seed_one_chain("p-zero", "https://f/p/zero", "mid-zero", 2)

    batch = await claim_next_batch("worker-1", ["p-zero"], max_size=0)

    assert batch == []
