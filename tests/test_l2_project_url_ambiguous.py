"""L2-without-parent must resolve project_url to EXACTLY ONE completed ancestor.

Round-1 (commit 6dc559d) accepted up to 5 completed candidates and bound the
most-recent one as long as they shared a profile. That silently mis-targeted
L1 siblings or prior L2 outputs (all "same profile, multiple completed") onto
the wrong ancestor — the caller never asked for media_id X, but the server
picked it because it sorted highest by completed_at.

The contract is now: 0 / 1 / >=2 completed candidates → 422 / 200 / 422.
"""
from __future__ import annotations

from datetime import UTC, datetime


async def _seed_completed_l1(
    *,
    project_url: str,
    profile: str,
    media_id: str,
) -> str:
    from server.db.job_store import create_job, update_job
    from server.models.job import Job, JobStatus, JobType, JobUpdate

    job = Job(
        type=JobType.TEXT_TO_VIDEO,
        prompt=f"seed {media_id}",
        profile=profile,
        job_level=1,
        project_url=project_url,
        status=JobStatus.PENDING,
    )
    await create_job(job)
    await update_job(
        job.id,
        JobUpdate(
            status=JobStatus.COMPLETED,
            media_id=media_id,
            completed_at=datetime.now(UTC),
        ),
    )
    return job.id


def _l2_payload(project_url: str) -> dict:
    return {
        "type": "extend-video",
        "prompt": "L2 op via project_url",
        "project_url": project_url,
    }


async def test_zero_completed_candidates_rejected(api_client):
    """No completed ancestor on the project → 422 require-ancestor."""
    payload = _l2_payload("https://labs.google/fx/tools/flow/project/none")
    resp = await api_client.post("/api/jobs", json=payload)
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert "require parent_job_id" in detail or "L2 op types" in detail


async def test_exactly_one_completed_candidate_binds(api_client):
    """Single completed L1 → bind as parent (job_level=2, profile inherited)."""
    project_url = "https://labs.google/fx/tools/flow/project/single"
    parent_id = await _seed_completed_l1(
        project_url=project_url,
        profile="profile-solo",
        media_id="m-solo",
    )

    resp = await api_client.post("/api/jobs", json=_l2_payload(project_url))

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["job_level"] == 2
    assert body["parent_job_id"] == parent_id
    assert body["profile"] == "profile-solo"


async def test_two_completed_same_profile_rejected_as_ambiguous(api_client):
    """L1 siblings on the same profile must NOT silently pick latest.

    This is the round-1 regression: round-1 only rejected when profiles
    differed, so two completed L1 on profile-A would bind to whichever
    `completed_at DESC` happened to win — wrong media_id silently.
    """
    project_url = "https://labs.google/fx/tools/flow/project/siblings"
    await _seed_completed_l1(
        project_url=project_url, profile="profile-A", media_id="m-1",
    )
    await _seed_completed_l1(
        project_url=project_url, profile="profile-A", media_id="m-2",
    )

    resp = await api_client.post("/api/jobs", json=_l2_payload(project_url))

    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"].lower()
    assert "ambiguous" in detail or "multiple" in detail


async def test_many_completed_rejected_as_ambiguous(api_client):
    """N>=2 completed jobs (regardless of mix) → 422 ambiguous."""
    project_url = "https://labs.google/fx/tools/flow/project/many"
    for i in range(4):
        await _seed_completed_l1(
            project_url=project_url,
            profile=f"profile-{i % 2}",
            media_id=f"m-{i}",
        )

    resp = await api_client.post("/api/jobs", json=_l2_payload(project_url))

    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"].lower()
    assert "ambiguous" in detail or "multiple" in detail
