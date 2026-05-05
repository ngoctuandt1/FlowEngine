"""Unit tests for claim_next_batch and the /api/worker/claim batch HTTP endpoint.

PRD: docs/PRD_CLAIM_BATCH_DISPATCH.md §3.2 + §3.1
"""

import importlib
from datetime import UTC, datetime
from typing import Optional

import pytest

from server.db.job_store import claim_next_batch, create_job, get_job
import server.routes.worker as worker_module
from server.db.profile_store import create_profile
from server.models.job import Job, JobStatus, JobType
from server.models.profile import Profile, ProfileStatus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _profile(name: str) -> Profile:
    return Profile(
        name=name,
        google_account=f"{name}@example.com",
        locale="en",
        tier="ultra",
        status=ProfileStatus.AVAILABLE,
        created_at=datetime.now(UTC),
    )


def _l1_job(job_id: str, *, profile: Optional[str] = None) -> Job:
    now = datetime.now(UTC)
    return Job(
        id=job_id,
        type=JobType.TEXT_TO_VIDEO,
        status=JobStatus.PENDING,
        job_level=1,
        profile=profile,
        prompt="test prompt",
        created_at=now,
        updated_at=now,
    )


def _completed_l1(job_id: str, profile: str, project_url: str, media_id: str) -> Job:
    now = datetime.now(UTC)
    return Job(
        id=job_id,
        type=JobType.TEXT_TO_VIDEO,
        status=JobStatus.COMPLETED,
        job_level=1,
        profile=profile,
        project_url=project_url,
        media_id=media_id,
        edit_url=f"{project_url}/edit/{media_id}",
        prompt="parent",
        created_at=now,
        updated_at=now,
        completed_at=now,
    )


def _l2_job(
    job_id: str,
    parent_id: str,
    *,
    job_type: JobType = JobType.EXTEND_VIDEO,
) -> Job:
    now = datetime.now(UTC)
    return Job(
        id=job_id,
        type=job_type,
        status=JobStatus.PENDING,
        job_level=2,
        parent_job_id=parent_id,
        created_at=now,
        updated_at=now,
    )


# ---------------------------------------------------------------------------
# Test 1 — returns up to N jobs in one transaction
# ---------------------------------------------------------------------------


async def test_claim_batch_returns_up_to_n_jobs(db):
    """claim_next_batch with batch_size=3 returns exactly 3 of 5 pending L1 jobs."""
    await create_profile(_profile("prof-batch-1"))
    for i in range(5):
        await create_job(_l1_job(f"b1-job-{i}", profile="prof-batch-1"))

    result = await claim_next_batch("worker-1", ["prof-batch-1"], batch_size=3)

    assert len(result) == 3
    for job in result:
        assert job.status == JobStatus.CLAIMED
        assert job.worker_id == "worker-1"

    # Remaining 2 must stay pending.
    all_ids = {f"b1-job-{i}" for i in range(5)}
    claimed_ids = {j.id for j in result}
    remaining_ids = all_ids - claimed_ids
    for jid in remaining_ids:
        remaining = await get_job(jid)
        assert remaining.status == JobStatus.PENDING, (
            f"{jid} should still be pending"
        )


# ---------------------------------------------------------------------------
# Test 2 — profile-coherent: first claim locks profile
# ---------------------------------------------------------------------------


async def test_claim_batch_profile_coherent(db):
    """All claimed jobs must share the same profile; cross-profile rows stay pending."""
    await create_profile(_profile("prof-x"))
    await create_profile(_profile("prof-y"))

    # profile=None L1 jobs — claim algo assigns from available_profiles[0].
    for i in range(3):
        await create_job(_l1_job(f"b2-job-{i}"))

    result = await claim_next_batch("worker-1", ["prof-x", "prof-y"], batch_size=3)

    assert len(result) >= 1
    profiles_used = {j.profile for j in result}
    assert len(profiles_used) == 1, (
        f"All jobs in a batch must share one profile; got {profiles_used}"
    )


# ---------------------------------------------------------------------------
# Test 3 — L2+ priority over L1
# ---------------------------------------------------------------------------


async def test_claim_batch_l2_priority_over_l1(db):
    """Step 1 fills L2+ first; step 2 (L1) is skipped if step 1 returned ≥1 row."""
    await create_profile(_profile("prof-pri-1"))

    # 1 L1 fresh job.
    await create_job(_l1_job("b3-l1", profile="prof-pri-1"))

    # 2 L2 jobs with completed parent on same profile.
    await create_job(
        _completed_l1(
            "b3-parent",
            profile="prof-pri-1",
            project_url="https://labs.google/fx/tools/flow/project/p-b3",
            media_id="media-b3-001",
        )
    )
    await create_job(_l2_job("b3-l2-a", "b3-parent"))
    await create_job(_l2_job("b3-l2-b", "b3-parent"))

    result = await claim_next_batch("worker-1", ["prof-pri-1"], batch_size=3)

    claimed_ids = {j.id for j in result}
    assert "b3-l2-a" in claimed_ids or "b3-l2-b" in claimed_ids, (
        "At least one L2 job must be claimed"
    )
    assert "b3-l1" not in claimed_ids, (
        "L1 must NOT be claimed when L2+ rows exist (step 2 skipped)"
    )

    l1 = await get_job("b3-l1")
    assert l1.status == JobStatus.PENDING


# ---------------------------------------------------------------------------
# Test 4 — L1 fallback when no L2+ ready
# ---------------------------------------------------------------------------


async def test_claim_batch_l1_fallback_when_no_l2(db):
    """When no L2+ candidates exist, step 2 fills with L1 fresh jobs."""
    await create_profile(_profile("prof-fb-1"))
    for i in range(3):
        await create_job(_l1_job(f"b4-l1-{i}", profile="prof-fb-1"))

    result = await claim_next_batch("worker-1", ["prof-fb-1"], batch_size=3)

    assert len(result) == 3
    for job in result:
        assert job.job_level == 1
        assert job.status == JobStatus.CLAIMED


# ---------------------------------------------------------------------------
# Test 5 — project-inflight cap respected in-transaction
# ---------------------------------------------------------------------------


async def test_claim_batch_project_inflight_cap_1(monkeypatch, db):
    """With FLOW_PROJECT_INFLIGHT=1, only 1 of 3 L2 jobs sharing a project_url is claimed."""
    monkeypatch.setenv("FLOW_PROJECT_INFLIGHT", "1")

    await create_profile(_profile("prof-inf-1"))
    await create_job(
        _completed_l1(
            "b5-parent",
            profile="prof-inf-1",
            project_url="https://labs.google/fx/tools/flow/project/p-b5",
            media_id="media-b5-001",
        )
    )
    for i in range(3):
        await create_job(_l2_job(f"b5-l2-{i}", "b5-parent"))

    result = await claim_next_batch("worker-1", ["prof-inf-1"], batch_size=3)

    assert len(result) == 1, (
        f"FLOW_PROJECT_INFLIGHT=1 must yield exactly 1 claim; got {len(result)}"
    )


async def test_claim_batch_project_inflight_cap_2(monkeypatch, db):
    """With FLOW_PROJECT_INFLIGHT=2, exactly 2 of 3 L2 jobs sharing a project_url are claimed."""
    monkeypatch.setenv("FLOW_PROJECT_INFLIGHT", "2")

    await create_profile(_profile("prof-inf-2"))
    await create_job(
        _completed_l1(
            "b5b-parent",
            profile="prof-inf-2",
            project_url="https://labs.google/fx/tools/flow/project/p-b5b",
            media_id="media-b5b-001",
        )
    )
    for i in range(3):
        await create_job(_l2_job(f"b5b-l2-{i}", "b5b-parent"))

    result = await claim_next_batch("worker-1", ["prof-inf-2"], batch_size=3)

    assert len(result) == 2, (
        f"FLOW_PROJECT_INFLIGHT=2 must yield exactly 2 claims; got {len(result)}"
    )


# ---------------------------------------------------------------------------
# Test 6 — empty queue returns empty list
# ---------------------------------------------------------------------------


async def test_claim_batch_empty_queue(db):
    """No pending jobs → claim_next_batch returns []."""
    await create_profile(_profile("prof-empty-1"))

    result = await claim_next_batch("worker-1", ["prof-empty-1"], batch_size=3)

    assert result == []


# ---------------------------------------------------------------------------
# Test 7 — batch_size=1 returns at most 1
# ---------------------------------------------------------------------------


async def test_claim_batch_size_1_returns_one(db):
    """batch_size=1 must return exactly 1 job even if 5 are pending."""
    await create_profile(_profile("prof-sz1-1"))
    for i in range(5):
        await create_job(_l1_job(f"b7-job-{i}", profile="prof-sz1-1"))

    result = await claim_next_batch("worker-1", ["prof-sz1-1"], batch_size=1)

    assert len(result) == 1


# ---------------------------------------------------------------------------
# Test 8 — HTTP /api/worker/claim batch endpoint
# ---------------------------------------------------------------------------


async def _reload_app(monkeypatch):
    """Reload server modules so the API_KEY env var takes effect."""
    monkeypatch.setenv("API_KEY", "test-key-batch")
    for mod in ("server.config", "server.auth", "server.routes.worker", "server.app"):
        importlib.reload(importlib.import_module(mod))
    import server.app as sa
    return sa.app


async def test_http_claim_batch_size_gt1_returns_jobs_wrapper(monkeypatch, api_client, db):
    """POST /api/worker/claim with batch_size=3 and 3 pending jobs → {"jobs": [...]}."""
    await create_profile(_profile("prof-http-1"))
    for i in range(3):
        await create_job(_l1_job(f"b8-job-{i}", profile="prof-http-1"))

    resp = await api_client.post(
        "/api/worker/claim",
        json={"worker_id": "w-http-1", "profiles": ["prof-http-1"], "batch_size": 3},
        headers={"Authorization": "Bearer dev-key"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert "jobs" in body, f"Expected {{\"jobs\": [...]}} wrapper; got {body}"
    assert len(body["jobs"]) == 3


async def test_http_claim_batch_size_1_returns_bare_job(monkeypatch, api_client, db):
    """POST with batch_size=1 and 1 pending job → bare Job dict (not wrapped)."""
    await create_profile(_profile("prof-http-2"))
    await create_job(_l1_job("b8-single", profile="prof-http-2"))

    resp = await api_client.post(
        "/api/worker/claim",
        json={"worker_id": "w-http-2", "profiles": ["prof-http-2"], "batch_size": 1},
        headers={"Authorization": "Bearer dev-key"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert "jobs" not in body, (
        "batch_size=1 must return bare Job, not {\"jobs\": [...]} wrapper"
    )
    assert body.get("id") == "b8-single"


async def test_http_claim_batch_empty_queue_returns_204(api_client, db):
    """POST with batch_size=3 and empty queue → 204."""
    await create_profile(_profile("prof-http-3"))

    resp = await api_client.post(
        "/api/worker/claim",
        json={"worker_id": "w-http-3", "profiles": ["prof-http-3"], "batch_size": 3},
        headers={"Authorization": "Bearer dev-key"},
    )

    assert resp.status_code == 204


# ---------------------------------------------------------------------------
# Test 9 — back-compat: omitted batch_size → bare Job
# ---------------------------------------------------------------------------


async def test_http_claim_no_batch_size_returns_bare_job(api_client, db):
    """POST without batch_size field → bare Job dict (legacy shape preserved)."""
    await create_profile(_profile("prof-compat-1"))
    await create_job(_l1_job("b9-compat", profile="prof-compat-1"))

    resp = await api_client.post(
        "/api/worker/claim",
        json={"worker_id": "w-compat-1", "profiles": ["prof-compat-1"]},
        headers={"Authorization": "Bearer dev-key"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert "jobs" not in body, (
        "Omitting batch_size must preserve legacy bare-Job response shape"
    )
    assert body.get("id") == "b9-compat"


# ---------------------------------------------------------------------------
# Test 10 — _effective_batch_cap() honours FLOW_CLAIM_BATCH_MAX env var
# ---------------------------------------------------------------------------


def test_effective_batch_cap_env_unset(monkeypatch):
    """FLOW_CLAIM_BATCH_MAX unset → cap equals the hardcoded ceiling (16)."""
    monkeypatch.delenv("FLOW_CLAIM_BATCH_MAX", raising=False)
    assert worker_module._effective_batch_cap() == worker_module._CLAIM_BATCH_HARD_CAP


def test_effective_batch_cap_env_smaller_than_hard_cap(monkeypatch):
    """FLOW_CLAIM_BATCH_MAX=3 → cap = 3 (env wins because 3 < 16)."""
    monkeypatch.setenv("FLOW_CLAIM_BATCH_MAX", "3")
    assert worker_module._effective_batch_cap() == 3


def test_effective_batch_cap_env_larger_than_hard_cap(monkeypatch):
    """FLOW_CLAIM_BATCH_MAX=20 → cap = 16 (hard ceiling prevails)."""
    monkeypatch.setenv("FLOW_CLAIM_BATCH_MAX", "20")
    assert worker_module._effective_batch_cap() == worker_module._CLAIM_BATCH_HARD_CAP


def test_effective_batch_cap_env_invalid_string(monkeypatch):
    """FLOW_CLAIM_BATCH_MAX=abc → cap = 16 (invalid value ignored)."""
    monkeypatch.setenv("FLOW_CLAIM_BATCH_MAX", "abc")
    assert worker_module._effective_batch_cap() == worker_module._CLAIM_BATCH_HARD_CAP


def test_effective_batch_cap_env_zero(monkeypatch):
    """FLOW_CLAIM_BATCH_MAX=0 → cap = 16 (zero rejected, must be ≥1)."""
    monkeypatch.setenv("FLOW_CLAIM_BATCH_MAX", "0")
    assert worker_module._effective_batch_cap() == worker_module._CLAIM_BATCH_HARD_CAP


def test_effective_batch_cap_env_negative(monkeypatch):
    """FLOW_CLAIM_BATCH_MAX=-5 → cap = 16 (negative rejected, must be ≥1)."""
    monkeypatch.setenv("FLOW_CLAIM_BATCH_MAX", "-5")
    assert worker_module._effective_batch_cap() == worker_module._CLAIM_BATCH_HARD_CAP
