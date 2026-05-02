import json
from datetime import UTC, datetime

from server.db.database import get_db


async def test_create_project_201(api_client):
    response = await api_client.post(
        "/api/projects",
        json={"name": "Project X"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["id"]
    assert body["name"] == "Project X"
    assert body["description"] is None
    assert body["cover_thumb_url"] is None


async def test_list_projects_empty(api_client):
    response = await api_client.get("/api/projects")

    assert response.status_code == 200
    assert response.json() == []


async def test_list_projects_after_create(api_client):
    created = await api_client.post(
        "/api/projects",
        json={"name": "Project X", "description": "Demo"},
    )
    assert created.status_code == 201

    response = await api_client.get("/api/projects")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["id"] == created.json()["id"]
    assert body[0]["name"] == "Project X"
    assert body[0]["description"] == "Demo"
    assert body[0]["cover_thumb_url"] is None


async def test_project_cover_thumb_resolves_from_latest_job(api_client):
    now = datetime.now(UTC).isoformat()
    async with get_db() as db:
        await db.execute(
            """
            INSERT INTO projects (
                id, name, description, cover_chain_id, cover_job_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            ("project-1", "Project X", None, None, None, now, now),
        )
        await db.execute(
            """
            INSERT INTO jobs (
                id, type, status, chain_id, project_id, output_files_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "job-1",
                "text-to-video",
                "completed",
                "chain-1",
                "project-1",
                json.dumps(["thumb.png"]),
                now,
                now,
            ),
        )
        await db.commit()

    response = await api_client.get("/api/projects")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["id"] == "project-1"
    assert body[0]["cover_thumb_url"] == "/downloads/thumb.png"
