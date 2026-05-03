"""PRD §4 — GET /api/jobs/l2-siblings + DB query."""

from datetime import UTC, datetime

import pytest

from server.db.job_store import (
    create_job,
    list_pending_l2_siblings,
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


def _pending_l2(
    job_id: str,
    *,
    job_type: JobType = JobType.EXTEND_VIDEO,
    parent_job_id: str = "L1",
    profile: str | None = "p",
    project_url: str | None = "https://labs.google/fx/tools/flow/project/proj-x",
    media_id: str | None = "parent-media",
) -> Job:
    now = datetime.now(UTC)
    return Job(
        id=job_id,
        type=job_type,
        status=JobStatus.PENDING,
        job_level=2,
        parent_job_id=parent_job_id,
        prompt=f"prompt {job_id}",
        profile=profile,
        project_url=project_url,
        media_id=media_id,
        created_at=now,
        updated_at=now,
    )


@pytest.mark.asyncio
async def test_list_pending_l2_siblings_filters_by_parent_and_profile(db):
    await create_profile(_make_profile("p"))
    await create_job(_l1_parent("L1"))
    await create_job(_l1_parent("OTHER"))
    # Two jobs share parent L1.
    await create_job(_pending_l2("a", job_type=JobType.EXTEND_VIDEO))
    await create_job(_pending_l2("b", job_type=JobType.CAMERA_MOVE))
    # Different parent — must NOT show up.
    await create_job(_pending_l2("c", parent_job_id="OTHER"))
    # Different profile — filtered out.
    await create_profile(_make_profile("other"))
    await create_job(_pending_l2("d", profile="other"))

    rows = await list_pending_l2_siblings(parent_job_id="L1", profile="p")
    ids = [r.id for r in rows]
    assert "a" in ids and "b" in ids
    assert "c" not in ids
    assert "d" not in ids


@pytest.mark.asyncio
async def test_list_pending_l2_siblings_respects_limit_and_order(db):
    await create_profile(_make_profile("p"))
    await create_job(_l1_parent("L1"))
    for i in range(5):
        await create_job(_pending_l2(f"j{i}"))
    rows = await list_pending_l2_siblings(parent_job_id="L1",
                                           profile="p", limit=2)
    assert len(rows) == 2
    assert [r.id for r in rows] == ["j0", "j1"]


@pytest.mark.asyncio
async def test_list_pending_l2_siblings_includes_all_four_l2_op_types(db):
    await create_profile(_make_profile("p"))
    await create_job(_l1_parent("L1"))
    await create_job(_pending_l2("ext", job_type=JobType.EXTEND_VIDEO))
    await create_job(_pending_l2("cam", job_type=JobType.CAMERA_MOVE))
    await create_job(_pending_l2("ins", job_type=JobType.INSERT_OBJECT))
    await create_job(_pending_l2("rm", job_type=JobType.REMOVE_OBJECT))

    rows = await list_pending_l2_siblings(parent_job_id="L1", profile="p")
    assert {r.id for r in rows} == {"ext", "cam", "ins", "rm"}


@pytest.mark.asyncio
async def test_list_pending_l2_siblings_empty_parent_returns_empty(db):
    rows = await list_pending_l2_siblings(parent_job_id="", profile="p")
    assert rows == []


@pytest.mark.asyncio
async def test_get_jobs_l2_siblings_endpoint(api_client):
    await create_profile(_make_profile("p"))
    await create_job(_l1_parent("L1"))
    await create_job(_pending_l2("aa", job_type=JobType.EXTEND_VIDEO))
    await create_job(_pending_l2("bb", job_type=JobType.CAMERA_MOVE))

    resp = await api_client.get(
        "/api/jobs/l2-siblings",
        params={"parent_job_id": "L1", "profile": "p"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert {row["id"] for row in data} == {"aa", "bb"}
    assert all(row["job_level"] == 2 for row in data)


@pytest.mark.asyncio
async def test_get_jobs_l2_siblings_endpoint_requires_parent(api_client):
    resp = await api_client.get("/api/jobs/l2-siblings", params={"profile": "p"})
    assert resp.status_code == 422  # FastAPI validation error
