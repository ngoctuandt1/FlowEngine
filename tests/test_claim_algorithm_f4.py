from datetime import UTC, datetime

from server.db.job_store import claim_next_job, create_job
from server.db.profile_store import create_profile
from server.models.job import Job, JobStatus, JobType
from server.models.profile import Profile, ProfileStatus


async def test_l2_claim_rejects_parent_missing_target_context(db):
    await create_profile(
        Profile(
            name="f4-prof",
            google_account="f4@example.com",
            locale="en",
            tier="ultra",
            status=ProfileStatus.AVAILABLE,
            created_at=datetime.now(UTC),
        )
    )

    now = datetime.now(UTC)
    await create_job(
        Job(
            id="f4-parent",
            type=JobType.TEXT_TO_VIDEO,
            status=JobStatus.COMPLETED,
            job_level=1,
            profile="f4-prof",
            project_url=None,
            media_id=None,
            edit_url=None,
            prompt="parent",
            created_at=now,
            updated_at=now,
            completed_at=now,
        )
    )
    await create_job(
        Job(
            id="f4-child",
            type=JobType.CAMERA_MOVE,
            status=JobStatus.PENDING,
            job_level=2,
            parent_job_id="f4-parent",
            direction="Dolly in",
            created_at=now,
            updated_at=now,
        )
    )

    claimed = await claim_next_job("worker-1", ["f4-prof"])

    assert claimed is None
