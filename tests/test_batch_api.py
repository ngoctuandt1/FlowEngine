"""REST routes for batch-mode (PR-2 of batch-mode epic).

- ``POST /api/worker/claim-batch`` — worker endpoint that wraps
  ``claim_next_batch``.
- ``POST /api/batches`` — fan-out creator: N sibling L2 jobs off one
  completed parent.

The job_store layer is covered by ``test_batch_claim.py``; these tests
exercise the thin HTTP layer + its request-shape / status-code contract.
"""

from datetime import UTC, datetime

from server.db.job_store import create_job
from server.db.profile_store import create_profile
from server.models.job import Job, JobStatus, JobType
from server.models.profile import Profile, ProfileStatus


def _profile(name: str) -> Profile:
    return Profile(
        name=name,
        google_account=f"{name}@example.com",
        locale="en",
        tier="ultra",
        status=ProfileStatus.AVAILABLE,
        created_at=datetime.now(UTC),
    )


def _parent(job_id: str, profile: str, project_url: str,
            media_id: str, *, status: JobStatus = JobStatus.COMPLETED,
            job_level: int = 1) -> Job:
    now = datetime.now(UTC)
    return Job(
        id=job_id,
        type=JobType.TEXT_TO_VIDEO,
        status=status,
        job_level=job_level,
        profile=profile,
        project_url=project_url,
        media_id=media_id,
        edit_url=f"{project_url}/edit/{media_id}",
        prompt="parent prompt",
        created_at=now,
        updated_at=now,
        completed_at=now if status == JobStatus.COMPLETED else None,
    )


async def test_claim_batch_empty_when_nothing_matches(api_client):
    response = await api_client.post(
        "/api/worker/claim-batch",
        json={"worker_id": "w-1", "profiles": ["p-none"]},
    )
    assert response.status_code == 200
    assert response.json() == {"jobs": []}


async def test_claim_batch_returns_siblings_on_happy_path(api_client):
    await create_profile(_profile("p-batch"))
    await create_job(_parent("par-1", "p-batch", "https://f/p/x", "mid-x"))
    # Two L2 siblings on the same project.
    for i in range(2):
        await api_client.post(
            "/api/jobs",
            json={
                "type": "camera-move",
                "direction": "Dolly in",
                "parent_job_id": "par-1",
            },
        )

    response = await api_client.post(
        "/api/worker/claim-batch",
        json={"worker_id": "w-1", "profiles": ["p-batch"], "max_size": 5},
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body["jobs"]) == 2
    assert all(j["status"] == "claimed" for j in body["jobs"])
    assert all(j["project_url"] == "https://f/p/x" for j in body["jobs"])


async def test_post_batches_fans_out_siblings(api_client):
    await create_profile(_profile("p-fan"))
    await create_job(_parent("par-fan", "p-fan", "https://f/p/fan", "mid-fan"))

    response = await api_client.post(
        "/api/batches",
        json={
            "parent_job_id": "par-fan",
            "jobs": [
                {"type": "camera-move", "direction": "Dolly in"},
                {"type": "camera-move", "direction": "Pan left"},
                {"type": "camera-move", "direction": "Tilt up"},
            ],
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["parent_job_id"] == "par-fan"
    assert len(body["jobs"]) == 3
    for job in body["jobs"]:
        assert job["parent_job_id"] == "par-fan"
        assert job["job_level"] == 2
        assert job["profile"] == "p-fan"
        assert job["project_url"] == "https://f/p/fan"
        assert job["media_id"] == "mid-fan"
        assert job["status"] == "pending"
    directions = [j["direction"] for j in body["jobs"]]
    assert directions == ["Dolly in", "Pan left", "Tilt up"]


async def test_post_batches_rejects_non_completed_parent(api_client):
    await create_profile(_profile("p-partial"))
    await create_job(_parent(
        "par-running", "p-partial", "https://f/p/run", "mid-run",
        status=JobStatus.RUNNING,
    ))

    response = await api_client.post(
        "/api/batches",
        json={
            "parent_job_id": "par-running",
            "jobs": [{"type": "camera-move", "direction": "Dolly in"}],
        },
    )

    assert response.status_code == 400
    assert "completed" in response.json()["detail"].lower()


async def test_post_batches_404_on_missing_parent(api_client):
    response = await api_client.post(
        "/api/batches",
        json={
            "parent_job_id": "does-not-exist",
            "jobs": [{"type": "camera-move", "direction": "Dolly in"}],
        },
    )

    assert response.status_code == 404


async def test_post_batches_rejects_empty_jobs_list(api_client):
    await create_profile(_profile("p-empty"))
    await create_job(_parent("par-empty", "p-empty", "https://f/p/e", "mid-e"))

    response = await api_client.post(
        "/api/batches",
        json={"parent_job_id": "par-empty", "jobs": []},
    )

    assert response.status_code == 400


async def test_post_batches_then_claim_batch_end_to_end(api_client):
    """Fan-out 3 via /api/batches, then one worker claims the whole group."""
    await create_profile(_profile("p-e2e"))
    await create_job(_parent("par-e2e", "p-e2e", "https://f/p/e2e", "mid-e2e"))

    await api_client.post(
        "/api/batches",
        json={
            "parent_job_id": "par-e2e",
            "jobs": [
                {"type": "camera-move", "direction": "Dolly in"},
                {"type": "camera-move", "direction": "Pan left"},
                {"type": "camera-move", "direction": "Tilt up"},
            ],
        },
    )

    claim = await api_client.post(
        "/api/worker/claim-batch",
        json={"worker_id": "w-e2e", "profiles": ["p-e2e"]},
    )

    assert claim.status_code == 200
    jobs = claim.json()["jobs"]
    assert len(jobs) == 3
    assert {j["direction"] for j in jobs} == {"Dolly in", "Pan left", "Tilt up"}
    assert all(j["worker_id"] == "w-e2e" for j in jobs)
