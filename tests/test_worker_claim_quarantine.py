import importlib
import logging
from datetime import UTC, datetime

from httpx import ASGITransport, AsyncClient

from server.db.job_store import create_job
from server.db.profile_store import create_profile
from server.models.job import Job, JobStatus, JobType
from server.models.profile import Profile, ProfileStatus


def _reload_worker_app(monkeypatch, api_key: str):
    monkeypatch.setenv("API_KEY", api_key)

    import server.config
    import server.auth
    import server.routes.worker
    import server.app

    importlib.reload(server.config)
    importlib.reload(server.auth)
    importlib.reload(server.routes.worker)
    importlib.reload(server.app)
    return server.app.app


async def test_worker_claim_skips_quarantined_profile(monkeypatch, db, caplog):
    app = _reload_worker_app(monkeypatch, "quarantine-test-key")

    await create_profile(
        Profile(
            name="quarantined-prof",
            google_account="quarantined@example.com",
            locale="en",
            tier="ultra",
            status=ProfileStatus.QUARANTINED,
            created_at=datetime.now(UTC),
        )
    )
    await create_job(
        Job(
            id="quarantine-job",
            type=JobType.TEXT_TO_VIDEO,
            status=JobStatus.PENDING,
            job_level=1,
            profile="quarantined-prof",
            prompt="Should not claim",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with caplog.at_level(logging.WARNING):
            response = await client.post(
                "/api/worker/claim",
                headers={"Authorization": "Bearer quarantine-test-key"},
                json={
                    "worker_id": "worker-1",
                    "profiles": ["quarantined-prof"],
                },
            )

    assert response.status_code == 204
    assert "quarantined-prof" in caplog.text
