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
                None,
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


async def test_get_project_detail_includes_real_api_created_chain(api_client):
    created_project = await api_client.post(
        "/api/projects",
        json={"name": "Project API Chain"},
    )
    assert created_project.status_code == 201
    project_id = created_project.json()["id"]

    chain_response = await api_client.post(
        "/api/chains",
        json={
            "profile": "project-detail-profile",
            "jobs": [
                {
                    "type": "text-to-video",
                    "prompt": "Open on project detail",
                    "project_id": project_id,
                },
                {
                    "type": "extend-video",
                    "prompt": "Continue on project detail",
                },
            ],
        },
    )
    assert chain_response.status_code == 201
    chain = chain_response.json()

    first_job_id = chain["jobs"][0]["id"]
    second_job_id = chain["jobs"][1]["id"]

    first_patch = await api_client.put(
        f"/api/worker/jobs/{first_job_id}",
        json={
            "status": "completed",
            "project_url": "https://flow.example/project/detail-001",
            "media_id": "media-detail-001",
            "output_files": ["downloads/detail-thumb-001.png"],
            "profile": "project-detail-profile",
        },
    )
    assert first_patch.status_code == 200

    second_patch = await api_client.put(
        f"/api/worker/jobs/{second_job_id}",
        json={
            "status": "completed",
            "project_url": "https://flow.example/project/detail-001",
            "media_id": "media-detail-002",
            "output_files": ["downloads/detail-thumb-002.png"],
            "profile": "project-detail-profile",
        },
    )
    assert second_patch.status_code == 200

    detail = await api_client.get(f"/api/projects/{project_id}")

    assert detail.status_code == 200
    body = detail.json()
    assert body["id"] == project_id
    assert len(body["chains"]) == 1
    assert body["chains"][0]["id"] == chain["chain_id"]
    assert body["chains"][0]["job_count"] == 2
    assert body["chains"][0]["completed_jobs"] == 2
    assert body["chains"][0]["status"] == "completed"
    assert body["cover_thumb_url"] == "/downloads/detail-thumb-002.png"
