import pytest

from server.db.database import get_db
from server.db.job_store import list_jobs


async def test_post_chain_rolls_back_when_job_insert_fails(api_client, monkeypatch):
    import server.db.job_store as job_store

    real_create_job = job_store.create_job
    calls = 0

    async def flaky_create_job(job, *, db=None, commit=True):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("boom at step 2")
        return await real_create_job(job, db=db, commit=commit)

    monkeypatch.setattr(job_store, "create_job", flaky_create_job)

    with pytest.raises(RuntimeError, match="boom at step 2"):
        await api_client.post(
            "/api/chains",
            json={
                "profile": "chain-profile",
                "jobs": [
                    {"type": "text-to-video", "prompt": "Step 1"},
                    {"type": "extend-video", "prompt": "Step 2"},
                    {"type": "camera-move", "prompt": "Step 3", "direction": "Dolly in"},
                ],
            },
        )

    assert calls == 2
    assert await list_jobs() == []

    async with get_db() as db:
        cursor = await db.execute("SELECT COUNT(*) FROM chains")
        row = await cursor.fetchone()
        chain_count = row[0]

    assert chain_count == 0
