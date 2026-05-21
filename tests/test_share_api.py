from contextlib import contextmanager
import asyncio
import importlib
from pathlib import Path

import aiosqlite
import pytest
from fastapi.testclient import TestClient

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
    assert public_body["job"]["prompt"] == "shareable clip"
    assert "profile" not in public_body["job"]
    assert "project_url" not in public_body["job"]
    assert "media_id" not in public_body["job"]
    assert "worker_id" not in public_body["job"]
    assert "generation_id" not in public_body["job"]
    assert public_body["share_url"] == first_body["share_url"]

    detail = await api_client.get(f"/api/jobs/{job['id']}")
    assert detail.status_code == 200
    detail_body = detail.json()
    assert detail_body["share_token"] == first_body["share_token"]
    assert detail_body["share_url"] == first_body["share_url"]
    assert detail_body["shared_at"]
    assert detail_body["revoked_at"] is None


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

    detail = await api_client.get(f"/api/jobs/{job['id']}")
    assert detail.status_code == 200
    detail_body = detail.json()
    assert "share_token" not in detail_body
    assert "share_url" not in detail_body


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


@contextmanager
def _auth_app(temp_db_path):
    import server.app  # noqa: WPS433
    import server.dashboard_auth  # noqa: WPS433

    patch = pytest.MonkeyPatch()
    patch.setenv("DASHBOARD_PASSWORD", "test")
    patch.setenv("DASHBOARD_AUTH_SECRET", "test-secret")
    patch.setenv("DATABASE_PATH", temp_db_path)
    patch.setenv("FLOW_DOWNLOAD_DIR", str(Path(temp_db_path).parent / "downloads"))
    patch.setenv("FLOW_UPLOAD_DIR", str(Path(temp_db_path).parent / "uploads"))

    importlib.reload(server.dashboard_auth)
    importlib.reload(server.app)
    from server.db.database import init_db

    asyncio.run(init_db())
    try:
        yield server.app.app
    finally:
        patch.undo()
        importlib.reload(server.dashboard_auth)
        importlib.reload(server.app)


def test_share_routes_with_dashboard_auth(temp_db_path):
    with _auth_app(temp_db_path) as app:
        with TestClient(app, base_url="http://testserver") as client:
            login = client.post("/api/auth/login", json={"password": "test"})
            assert login.status_code == 200
            job = client.post(
                "/api/jobs",
                json={
                    "type": "text-to-video",
                    "prompt": "auth shareable clip",
                    "model": "veo-3.1-fast",
                    "aspect_ratio": "16:9",
                },
                headers={"Origin": "http://testserver"},
            ).json()
            minted = client.post(
                f"/api/jobs/{job['id']}/share",
                headers={"Origin": "http://testserver"},
            )
            assert minted.status_code == 200, minted.text
            token = minted.json()["share_token"]

        with TestClient(app, base_url="http://testserver") as public_client:
            public = public_client.get(f"/api/shares/{token}")
            assert public.status_code == 200
            assert public.json()["job"]["id"] == job["id"]

            unauth_post = public_client.post(
                f"/api/jobs/{job['id']}/share",
                headers={"Origin": "http://testserver"},
            )
            unauth_delete = public_client.delete(
                f"/api/jobs/{job['id']}/share",
                headers={"Origin": "http://testserver"},
            )
            assert unauth_post.status_code == 401
            assert unauth_delete.status_code == 401


def test_share_mutations_require_same_origin_with_dashboard_auth(temp_db_path):
    with _auth_app(temp_db_path) as app:
        with TestClient(app, base_url="http://testserver") as client:
            login = client.post("/api/auth/login", json={"password": "test"})
            assert login.status_code == 200
            job = client.post(
                "/api/jobs",
                json={
                    "type": "text-to-video",
                    "prompt": "csrf shareable clip",
                    "model": "veo-3.1-fast",
                    "aspect_ratio": "16:9",
                },
                headers={"Origin": "http://testserver"},
            ).json()

            missing_origin = client.post(f"/api/jobs/{job['id']}/share")
            cross_origin = client.post(
                f"/api/jobs/{job['id']}/share",
                headers={"Origin": "https://evil.example"},
            )

            assert missing_origin.status_code == 403
            assert cross_origin.status_code == 403
