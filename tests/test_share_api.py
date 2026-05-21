import aiosqlite

from server.db.share_store import get_job_by_share_token, get_job_share


async def _create_job(api_client) -> dict:
    response = await api_client.post(
        "/api/jobs",
        json={
            "type": "text-to-video",
            "prompt": "shareable clip",
            "model": "veo-3.1-fast",
            "aspect_ratio": "16:9",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_get_job_does_not_mint_share_metadata(api_client):
    job = await _create_job(api_client)

    response = await api_client.get(f"/api/jobs/{job['id']}")

    assert response.status_code == 200
    body = response.json()
    assert "share_token" not in body
    assert "share_url" not in body
    assert await get_job_share(job["id"]) is None


async def test_share_mint_repeat_and_public_read(api_client):
    job = await _create_job(api_client)

    first = await api_client.post(f"/api/jobs/{job['id']}/share")
    second = await api_client.post(f"/api/jobs/{job['id']}/share")

    assert first.status_code == 200
    assert second.status_code == 200
    first_body = first.json()
    second_body = second.json()
    assert first_body["job_id"] == job["id"]
    assert first_body["share_token"]
    assert first_body["share_url"].endswith(f"/api/shares/{first_body['share_token']}")
    assert second_body["share_token"] == first_body["share_token"]
    assert second_body["share_url"] == first_body["share_url"]
    assert first_body["shared_at"]
    assert first_body["revoked_at"] is None

    public = await api_client.get(f"/api/shares/{first_body['share_token']}")

    assert public.status_code == 200
    public_body = public.json()
    assert public_body["job"]["id"] == job["id"]
    assert public_body["share_url"] == first_body["share_url"]


async def test_share_missing_job_returns_404(api_client):
    response = await api_client.post("/api/jobs/missing-job/share")

    assert response.status_code == 404


async def test_share_revoke_is_idempotent_and_blocks_public_read(api_client):
    job = await _create_job(api_client)
    minted = await api_client.post(f"/api/jobs/{job['id']}/share")
    token = minted.json()["share_token"]

    first = await api_client.delete(f"/api/jobs/{job['id']}/share")
    second = await api_client.delete(f"/api/jobs/{job['id']}/share")

    assert first.status_code == 200
    assert second.status_code == 200
    first_body = first.json()
    second_body = second.json()
    assert first_body["share_token"] is None
    assert first_body["share_url"] is None
    assert first_body["revoked_at"]
    assert second_body["revoked_at"] == first_body["revoked_at"]
    assert await get_job_by_share_token(token) is None

    public = await api_client.get(f"/api/shares/{token}")
    assert public.status_code == 404


async def test_share_store_columns_are_nullable(temp_db_path, api_client):
    job = await _create_job(api_client)
    response = await api_client.delete(f"/api/jobs/{job['id']}/share")
    assert response.status_code == 200

    async with aiosqlite.connect(temp_db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT share_token, share_url, shared_at, revoked_at FROM job_shares WHERE job_id = ?",
            (job["id"],),
        )
        row = await cursor.fetchone()

    assert row["share_token"] is None
    assert row["share_url"] is None
    assert row["shared_at"] is None
    assert row["revoked_at"] is not None

