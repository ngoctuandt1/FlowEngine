"""PRD §5 — GET /api/jobs/l3-siblings + DB query."""

from datetime import UTC, datetime

import pytest

from server.db.job_store import (
    create_job,
    list_pending_l3_siblings,
)
from server.db.profile_store import create_profile
from server.models.job import Job, JobStatus, JobType
from server.models.profile import Profile, ProfileStatus


def _make_profile(name: str) -> Profile:
    return Profile(
        name=name,
        google_account=f"{name}@x.com",
        locale="en",
        tier="ultra",
        status=ProfileStatus.AVAILABLE,
        created_at=datetime.now(UTC),
    )


def _l1_parent(job_id: str = "L1", profile: str = "p") -> Job:
    now = datetime.now(UTC)
    return Job(
        id=job_id,
        type=JobType.TEXT_TO_VIDEO,
        status=JobStatus.COMPLETED,
        job_level=1,
        prompt="parent",
        profile=profile,
        project_url="https://labs.google/fx/tools/flow/project/proj-x",
        media_id="parent-media",
        created_at=now,
        updated_at=now,
    )


def _l2_parent(
    job_id: str = "L2",
    *,
    profile: str = "p",
    parent_job_id: str = "L1",
) -> Job:
    now = datetime.now(UTC)
    return Job(
        id=job_id,
        type=JobType.EXTEND_VIDEO,
        status=JobStatus.COMPLETED,
        job_level=2,
        parent_job_id=parent_job_id,
        prompt="l2 parent",
        profile=profile,
        project_url="https://labs.google/fx/tools/flow/project/proj-x",
        media_id="l2-media",
        created_at=now,
        updated_at=now,
    )


def _pending_l3(
    job_id: str,
    *,
    job_type: JobType = JobType.EXTEND_VIDEO,
    parent_job_id: str = "L2",
    profile: str | None = "p",
    job_level: int = 3,
) -> Job:
    now = datetime.now(UTC)
    return Job(
        id=job_id,
        type=job_type,
        status=JobStatus.PENDING,
        job_level=job_level,
        parent_job_id=parent_job_id,
        prompt=f"prompt {job_id}",
        profile=profile,
        project_url="https://labs.google/fx/tools/flow/project/proj-x",
        media_id="l2-media",
        created_at=now,
        updated_at=now,
    )


@pytest.mark.asyncio
async def test_list_pending_l3_siblings_filters_by_parent_and_profile(db):
    await create_profile(_make_profile("p"))
    await create_job(_l1_parent("L1"))
    await create_job(_l2_parent("L2"))
    await create_job(_l2_parent("OTHER_L2"))
    # Two L3 jobs share the same direct L2 parent.
    await create_job(_pending_l3("a", job_type=JobType.EXTEND_VIDEO))
    await create_job(_pending_l3("b", job_type=JobType.CAMERA_MOVE))
    # Different L2 parent — must NOT show up.
    await create_job(_pending_l3("c", parent_job_id="OTHER_L2"))
    # Different profile — filtered out.
    await create_profile(_make_profile("other"))
    await create_job(_pending_l3("d", profile="other"))

    rows = await list_pending_l3_siblings(parent_job_id="L2", profile="p")
    ids = [r.id for r in rows]
    assert "a" in ids and "b" in ids
    assert "c" not in ids
    assert "d" not in ids


@pytest.mark.asyncio
async def test_list_pending_l3_siblings_excludes_l2_jobs(db):
    """job_level=2 with the same parent must not be returned by L3 query."""
    await create_profile(_make_profile("p"))
    await create_job(_l1_parent("L1"))
    await create_job(_l2_parent("L2"))
    # Pending L2 with parent_job_id=L2 (a contrived edge case): excluded.
    await create_job(_pending_l3("l2-imposter", job_level=2))
    await create_job(_pending_l3("real-l3", job_level=3))

    rows = await list_pending_l3_siblings(parent_job_id="L2", profile="p")
    ids = [r.id for r in rows]
    assert "real-l3" in ids
    assert "l2-imposter" not in ids


@pytest.mark.asyncio
async def test_list_pending_l3_siblings_includes_deeper_levels(db):
    """job_level=4 (L4 sibling chain) is also covered by the query."""
    await create_profile(_make_profile("p"))
    await create_job(_l1_parent("L1"))
    await create_job(_l2_parent("L2"))
    await create_job(_pending_l3("deep", job_level=4))

    rows = await list_pending_l3_siblings(parent_job_id="L2", profile="p")
    assert any(r.id == "deep" for r in rows)


@pytest.mark.asyncio
async def test_list_pending_l3_siblings_respects_limit_and_order(db):
    await create_profile(_make_profile("p"))
    await create_job(_l1_parent("L1"))
    await create_job(_l2_parent("L2"))
    for i in range(5):
        await create_job(_pending_l3(f"j{i}"))
    rows = await list_pending_l3_siblings(parent_job_id="L2",
                                          profile="p", limit=2)
    assert len(rows) == 2
    assert [r.id for r in rows] == ["j0", "j1"]


@pytest.mark.asyncio
async def test_list_pending_l3_siblings_includes_all_four_op_types(db):
    await create_profile(_make_profile("p"))
    await create_job(_l1_parent("L1"))
    await create_job(_l2_parent("L2"))
    await create_job(_pending_l3("ext", job_type=JobType.EXTEND_VIDEO))
    await create_job(_pending_l3("cam", job_type=JobType.CAMERA_MOVE))
    await create_job(_pending_l3("ins", job_type=JobType.INSERT_OBJECT))
    await create_job(_pending_l3("rm", job_type=JobType.REMOVE_OBJECT))

    rows = await list_pending_l3_siblings(parent_job_id="L2", profile="p")
    assert {r.id for r in rows} == {"ext", "cam", "ins", "rm"}


@pytest.mark.asyncio
async def test_list_pending_l3_siblings_empty_parent_returns_empty(db):
    rows = await list_pending_l3_siblings(parent_job_id="", profile="p")
    assert rows == []


@pytest.mark.asyncio
async def test_get_jobs_l3_siblings_endpoint(api_client):
    await create_profile(_make_profile("p"))
    await create_job(_l1_parent("L1"))
    await create_job(_l2_parent("L2"))
    await create_job(_pending_l3("aa", job_type=JobType.EXTEND_VIDEO))
    await create_job(_pending_l3("bb", job_type=JobType.CAMERA_MOVE))

    resp = await api_client.get(
        "/api/jobs/l3-siblings",
        params={"parent_job_id": "L2", "profile": "p"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert {row["id"] for row in data} == {"aa", "bb"}
    assert all(row["job_level"] >= 3 for row in data)


@pytest.mark.asyncio
async def test_get_jobs_l3_siblings_endpoint_requires_parent(api_client):
    resp = await api_client.get("/api/jobs/l3-siblings", params={"profile": "p"})
    assert resp.status_code == 422  # FastAPI validation error
