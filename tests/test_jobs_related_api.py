import aiosqlite
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
        profile="profile-a",
        project_url="https://flow.example/project/root-1",
        media_id=media_id,
        prompt=f"Prompt for {job_id}",
        output_files=output_files or [],
        created_at=created_at,
        updated_at=created_at,
    )


async def test_get_job_related_returns_lineage_stats_and_thumb_urls(api_client):
    chain_id = "chain-related-1"
    now = datetime.now(UTC)
    await create_chain(Chain(id=chain_id, profile="profile-a", created_at=now, updated_at=now))

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
    left_child = _make_job(
        job_id="left-l2",
        job_type=JobType.EXTEND_VIDEO,
        status=JobStatus.COMPLETED,
        level=2,
        created_at=now + timedelta(seconds=1),
        chain_id=chain_id,
        parent_job_id=root.id,
        media_id="media-left",
        output_files=[
            "downloads/chains/left.mp4",
            "downloads/chains/left-poster.png",
        ],
    )
    right_child = _make_job(
        job_id="right-l2",
        job_type=JobType.EXTEND_VIDEO,
        status=JobStatus.FAILED,
        level=2,
        created_at=now + timedelta(seconds=2),
        chain_id=chain_id,
        parent_job_id=root.id,
        media_id="media-right",
    )
    grandchild = _make_job(
        job_id="grandchild-l3",
        job_type=JobType.EXTEND_VIDEO,
        status=JobStatus.PENDING,
        level=3,
        created_at=now + timedelta(seconds=3),
        chain_id=chain_id,
        parent_job_id=left_child.id,
        media_id=None,
        output_files=["downloads/chains/grandchild.png"],
    )

    for job in (root, left_child, right_child, grandchild):
        await create_job(job)

    response = await api_client.get(f"/api/jobs/{grandchild.id}/related")

    assert response.status_code == 200
    body = response.json()
    assert body["self"]["id"] == grandchild.id
    assert body["self"]["thumb_url"] is None
    assert body["parent"]["id"] == left_child.id
    assert body["parent"]["thumb_url"] == "/downloads/chains/left-poster.png"
    assert [job["id"] for job in body["ancestors"]] == [root.id, left_child.id]
    assert body["ancestors"][0]["thumb_url"] == "/downloads/chains/root.png"
    assert body["siblings"] == []
    assert body["children"] == []
    assert body["chain_id"] == chain_id
    assert body["chain_root_id"] == root.id
    assert body["stats"] == {
        "total": 4,
        "completed": 2,
        "failed": 1,
        "pending": 1,
    }


async def test_get_job_related_returns_404_for_missing_job(api_client):
    response = await api_client.get("/api/jobs/missing-job/related")

    assert response.status_code == 404
    assert response.json()["detail"] == "Job missing-job not found"


async def test_get_job_related_ignores_missing_project_id_column(api_client, temp_db_path):
    async with aiosqlite.connect(temp_db_path) as db:
        await db.execute("DROP INDEX IF EXISTS idx_jobs_project_id")
        await db.execute("ALTER TABLE jobs DROP COLUMN project_id")
        await db.commit()

    chain_id = "chain-related-no-project-id"
    now = datetime.now(UTC)
    root = _make_job(
        job_id="root-no-project-id",
        job_type=JobType.TEXT_TO_VIDEO,
        status=JobStatus.COMPLETED,
        level=1,
        created_at=now,
        chain_id=chain_id,
        media_id="media-root-no-project-id",
    )
    child = _make_job(
        job_id="child-no-project-id",
        job_type=JobType.EXTEND_VIDEO,
        status=JobStatus.PENDING,
        level=2,
        created_at=now + timedelta(seconds=1),
        chain_id=chain_id,
        parent_job_id=root.id,
    )

    for job in (root, child):
        await create_job(job)

    response = await api_client.get(f"/api/jobs/{child.id}/related")

    assert response.status_code == 200
    body = response.json()
    assert body["self"]["id"] == child.id
    assert body["parent"]["id"] == root.id
    assert body["chain_root_id"] == root.id
    assert body["stats"] == {
        "total": 2,
        "completed": 1,
        "failed": 0,
        "pending": 1,
    }
