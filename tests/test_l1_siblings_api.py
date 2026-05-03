"""PRD §3.3 — GET /api/jobs/l1-siblings + claim-by-id endpoints + DB query."""

from datetime import UTC, datetime

import pytest

from server.db.job_store import (
    claim_specific_pending_job,
    create_job,
    list_pending_l1_siblings,
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


def _pending_l1_t2v(
    job_id: str,
    *,
    profile: str | None = None,
    project_url: str | None = None,
    created_offset: int = 0,
) -> Job:
    now = datetime.now(UTC)
    return Job(
        id=job_id,
        type=JobType.TEXT_TO_VIDEO,
        status=JobStatus.PENDING,
        job_level=1,
        prompt=f"prompt {job_id}",
        profile=profile,
        project_url=project_url,
        created_at=now,
        updated_at=now,
    )


@pytest.mark.asyncio
async def test_list_pending_l1_siblings_filters_by_profile_and_project_url(db):
    await create_profile(_make_profile("ngoctuandt20"))
    # Same profile, no project bound — eligible for batch on first claim.
    await create_job(_pending_l1_t2v("a", profile="ngoctuandt20"))
    await create_job(_pending_l1_t2v("b", profile="ngoctuandt20"))
    # Already bound to a different project — must NOT show up under None.
    await create_job(_pending_l1_t2v(
        "c", profile="ngoctuandt20",
        project_url="https://labs.google/fx/tools/flow/project/already-bound",
    ))
    # Different profile — filtered out.
    await create_profile(_make_profile("other-profile"))
    await create_job(_pending_l1_t2v("d", profile="other-profile"))

    rows = await list_pending_l1_siblings(profile="ngoctuandt20")
    ids = [r.id for r in rows]
    assert "a" in ids and "b" in ids
    assert "c" not in ids, "project_url-bound jobs are not unbound siblings"
    assert "d" not in ids, "other profile filtered out"


@pytest.mark.asyncio
async def test_list_pending_l1_siblings_respects_limit_and_order(db):
    await create_profile(_make_profile("p"))
    for i in range(7):
        await create_job(_pending_l1_t2v(f"j{i}", profile="p"))

    rows = await list_pending_l1_siblings(profile="p", limit=3)
    assert len(rows) == 3
    # FIFO order — oldest first.
    assert [r.id for r in rows] == ["j0", "j1", "j2"]


@pytest.mark.asyncio
async def test_list_pending_l1_siblings_skips_non_t2v(db):
    await create_profile(_make_profile("p"))
    await create_job(_pending_l1_t2v("ok", profile="p"))
    img = _pending_l1_t2v("img", profile="p")
    img.type = JobType.TEXT_TO_IMAGE
    await create_job(img)

    ids = [r.id for r in await list_pending_l1_siblings(profile="p")]
    assert ids == ["ok"], "Phase 1 only batches text-to-video"


@pytest.mark.asyncio
async def test_get_jobs_l1_siblings_endpoint(api_client):
    # Create a profile + 2 pending L1 t2v jobs; expect both back.
    await create_profile(_make_profile("p"))
    await create_job(_pending_l1_t2v("aa", profile="p"))
    await create_job(_pending_l1_t2v("bb", profile="p"))

    resp = await api_client.get("/api/jobs/l1-siblings", params={"profile": "p"})
    assert resp.status_code == 200
    data = resp.json()
    assert {row["id"] for row in data} == {"aa", "bb"}
    assert all(row["type"] == "text-to-video" for row in data)


@pytest.mark.asyncio
async def test_claim_specific_pending_job_atomically(db):
    await create_profile(_make_profile("p"))
    await create_job(_pending_l1_t2v("only", profile="p"))

    first = await claim_specific_pending_job("worker-A", "only", profile="p")
    assert first is not None
    assert first.status == JobStatus.CLAIMED
    assert first.worker_id == "worker-A"

    # Second attempt against an already-claimed job returns None.
    second = await claim_specific_pending_job("worker-B", "only", profile="p")
    assert second is None


@pytest.mark.asyncio
async def test_claim_specific_pending_job_returns_none_for_missing_id(db):
    await create_profile(_make_profile("p"))
    out = await claim_specific_pending_job("w", "does-not-exist")
    assert out is None
