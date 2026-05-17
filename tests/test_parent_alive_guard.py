from server.db.job_store import create_job
from server.models.job import Job, JobStatus, JobType


def _parent(
    job_id: str,
    status: JobStatus,
    *,
    parent_job_id: str | None = None,
    job_level: int = 1,
) -> Job:
    return Job(
        id=job_id,
        type=JobType.TEXT_TO_VIDEO if job_level == 1 else JobType.EXTEND_VIDEO,
        status=status,
        job_level=job_level,
        parent_job_id=parent_job_id,
        chain_id="parent-alive-chain",
        profile="parent-profile",
        project_url="https://labs.google/fx/tools/flow/project/parent-alive",
        media_id=f"media-{job_id}",
        prompt=f"parent {job_id}",
    )


async def _insert_parent(
    job_id: str,
    status: JobStatus,
    *,
    parent_job_id: str | None = None,
    job_level: int = 1,
) -> Job:
    parent = _parent(
        job_id,
        status,
        parent_job_id=parent_job_id,
        job_level=job_level,
    )
    await create_job(parent)
    return parent


def _child_payload(parent_job_id: str) -> dict[str, str]:
    return {
        "type": "extend-video",
        "prompt": "child should only queue under live ancestry",
        "parent_job_id": parent_job_id,
    }


async def test_post_job_rejects_failed_parent(api_client):
    parent = await _insert_parent("failed-parent", JobStatus.FAILED)

    response = await api_client.post("/api/jobs", json=_child_payload(parent.id))

    assert response.status_code == 400
    assert f"parent_id={parent.id}" in response.json()["detail"]


async def test_post_job_rejects_cancelled_parent(api_client):
    parent = await _insert_parent("cancelled-parent", JobStatus.CANCELLED)

    response = await api_client.post("/api/jobs", json=_child_payload(parent.id))

    assert response.status_code == 400


async def test_post_job_allows_completed_parent(api_client):
    parent = await _insert_parent("completed-parent", JobStatus.COMPLETED)

    response = await api_client.post("/api/jobs", json=_child_payload(parent.id))

    assert response.status_code == 201


async def test_post_job_allows_running_parent(api_client):
    parent = await _insert_parent("running-parent", JobStatus.RUNNING)

    response = await api_client.post("/api/jobs", json=_child_payload(parent.id))

    assert response.status_code == 201


async def test_post_job_allows_claimed_parent(api_client):
    parent = await _insert_parent("claimed-parent", JobStatus.CLAIMED)

    response = await api_client.post("/api/jobs", json=_child_payload(parent.id))

    assert response.status_code == 201


async def test_post_job_allows_pending_parent(api_client):
    parent = await _insert_parent("pending-parent", JobStatus.PENDING)

    response = await api_client.post("/api/jobs", json=_child_payload(parent.id))

    assert response.status_code == 201


async def test_post_job_rejects_failed_grandparent(api_client):
    l1 = await _insert_parent("failed-grandparent", JobStatus.FAILED)
    l2 = await _insert_parent(
        "completed-parent",
        JobStatus.COMPLETED,
        parent_job_id=l1.id,
        job_level=2,
    )

    response = await api_client.post("/api/jobs", json=_child_payload(l2.id))

    detail = response.json()["detail"]

    assert response.status_code == 400
    assert f"parent_id={l1.id}" in detail
    assert "parent_status=failed" in detail


async def test_post_chain_continuation_rejects_dead_parent(api_client):
    parent = await _insert_parent("dead-chain-parent", JobStatus.FAILED)

    response = await api_client.post(
        "/api/chains",
        json={
            "profile": "parent-profile",
            "jobs": [
                {
                    "type": "extend-video",
                    "prompt": "continue dead chain",
                    "parent_job_id": parent.id,
                },
                {"type": "extend-video", "prompt": "next step"},
            ],
        },
    )

    assert response.status_code == 400
    assert f"parent_id={parent.id}" in response.json()["detail"]


async def test_parent_alive_error_mentions_id_and_status(api_client):
    parent = await _insert_parent("message-parent", JobStatus.CANCELLED)

    response = await api_client.post("/api/jobs", json=_child_payload(parent.id))
    detail = response.json()["detail"]

    assert response.status_code == 400
    assert f"parent_id={parent.id}" in detail
    assert "parent_status=cancelled" in detail
    assert "Re-root this as a new L1-rooted chain" in detail
