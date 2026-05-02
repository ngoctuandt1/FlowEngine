from datetime import UTC, datetime, timedelta

from server.db.chain_store import create_chain
from server.db.job_store import create_job
from server.models.chain import Chain
from server.models.job import Job, JobStatus, JobType


def _make_job(
    *,
    job_id: str,
    job_type: JobType,
    status: JobStatus,
    level: int,
    created_at: datetime,
    chain_id: str,
    parent_job_id: str | None = None,
    media_id: str | None = None,
    output_files: list[str] | None = None,
) -> Job:
    return Job(
        id=job_id,
        type=job_type,
        status=status,
        job_level=level,
        parent_job_id=parent_job_id,
        chain_id=chain_id,
        profile="profile-dag",
        project_url="https://flow.example/project/dag-root",
        media_id=media_id,
        prompt=f"Prompt for {job_id}",
        output_files=output_files or [],
        created_at=created_at,
        updated_at=created_at,
    )


async def test_get_chain_returns_bulk_dag_payload(api_client):
    chain_id = "chain-dag-1"
    now = datetime.now(UTC)
    await create_chain(Chain(id=chain_id, profile="profile-dag", created_at=now, updated_at=now))

    root = _make_job(
        job_id="root-l1",
        job_type=JobType.TEXT_TO_VIDEO,
        status=JobStatus.COMPLETED,
        level=1,
        created_at=now,
        chain_id=chain_id,
        media_id="media-root",
        output_files=["downloads/chains/root.png"],
    )
    child_a = _make_job(
        job_id="child-a-l2",
        job_type=JobType.EXTEND_VIDEO,
        status=JobStatus.RUNNING,
        level=2,
        created_at=now + timedelta(seconds=1),
        chain_id=chain_id,
        parent_job_id=root.id,
        media_id="media-child-a",
        output_files=[
            "downloads/chains/child-a.mp4",
            "downloads/chains/child-a-poster.png",
        ],
    )
    child_b = _make_job(
        job_id="child-b-l2",
        job_type=JobType.INSERT_OBJECT,
        status=JobStatus.FAILED,
        level=2,
        created_at=now + timedelta(seconds=2),
        chain_id=chain_id,
        parent_job_id=root.id,
    )
    child_c = _make_job(
        job_id="child-c-l2",
        job_type=JobType.REMOVE_OBJECT,
        status=JobStatus.PENDING,
        level=2,
        created_at=now + timedelta(seconds=3),
        chain_id=chain_id,
        parent_job_id=root.id,
    )
    grandchild = _make_job(
        job_id="grandchild-l3",
        job_type=JobType.CAMERA_MOVE,
        status=JobStatus.PENDING,
        level=3,
        created_at=now + timedelta(seconds=4),
        chain_id=chain_id,
        parent_job_id=child_a.id,
    )

    for job in (root, child_a, child_b, child_c, grandchild):
        await create_job(job)

    response = await api_client.get(f"/api/chains/{chain_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == chain_id
    assert body["chain_id"] == chain_id
    assert body["root_id"] == root.id
    assert body["status"] == "failed"
    assert body["progress"] == {"completed": 1, "total": 5}
    assert body["stats"] == {
        "total": 5,
        "completed": 1,
        "failed": 1,
        "pending": 2,
        "running": 1,
    }

    jobs = body["jobs"]
    assert [job["id"] for job in jobs] == [
        root.id,
        child_a.id,
        child_b.id,
        child_c.id,
        grandchild.id,
    ]
    assert all("thumb_url" in job for job in jobs)

    jobs_by_id = {job["id"]: job for job in jobs}
    assert jobs_by_id[root.id]["thumb_url"] == "/downloads/chains/root.png"
    assert jobs_by_id[child_a.id]["thumb_url"] == "/downloads/chains/child-a-poster.png"
    assert jobs_by_id[child_b.id]["thumb_url"] is None
    assert jobs_by_id[grandchild.id]["thumb_url"] is None

    assert body["edges"] == [
        {"parent": root.id, "child": child_a.id},
        {"parent": root.id, "child": child_b.id},
        {"parent": root.id, "child": child_c.id},
        {"parent": child_a.id, "child": grandchild.id},
    ]
