from datetime import UTC, datetime

from server.db.database import get_db


async def _create_job(api_client, *, prompt: str = "trash prompt") -> dict:
    response = await api_client.post(
        "/api/jobs",
        json={
            "type": "text-to-video",
            "prompt": prompt,
            "profile": "trash-profile",
        },
    )
    assert response.status_code == 201
    return response.json()


async def test_soft_deleted_jobs_are_filtered_and_listed_in_trash(api_client):
    active = await _create_job(api_client, prompt="active prompt")
    deleted = await _create_job(api_client, prompt="deleted prompt")

    delete_response = await api_client.delete(f"/api/jobs/{deleted['id']}")
    assert delete_response.status_code == 200

    list_response = await api_client.get("/api/jobs")
    assert list_response.status_code == 200
    listed_ids = {job["id"] for job in list_response.json()["items"]}
    assert active["id"] in listed_ids
    assert deleted["id"] not in listed_ids

    trash_response = await api_client.get("/api/trash")
    assert trash_response.status_code == 200
    items = trash_response.json()["items"]
    assert items == [
        {
            "type": "job",
            "job_id": deleted["id"],
            "project_id": None,
            "name": None,
            "prompt": "deleted prompt",
            "deleted_at": items[0]["deleted_at"],
        }
    ]
    assert items[0]["deleted_at"]


async def test_restore_is_idempotent_and_returns_counts(api_client):
    job = await _create_job(api_client)
    delete_response = await api_client.delete(f"/api/jobs/{job['id']}")
    assert delete_response.status_code == 200

    first_restore = await api_client.post(
        "/api/trash/restore",
        json={"job_ids": [job["id"]]},
    )
    assert first_restore.status_code == 200
    assert first_restore.json()["restored"] == {"jobs": 1, "projects": 0}
    assert first_restore.json()["restored_jobs"] == 1

    second_restore = await api_client.post(
        "/api/trash/restore",
        json={"job_ids": [job["id"]]},
    )
    assert second_restore.status_code == 200
    assert second_restore.json()["restored"] == {"jobs": 0, "projects": 0}

    get_response = await api_client.get(f"/api/jobs/{job['id']}")
    assert get_response.status_code == 200


async def test_permanent_delete_requires_selection(api_client):
    response = await api_client.request("DELETE", "/api/trash/permanent", json={})

    assert response.status_code == 422


async def test_permanent_delete_never_deletes_active_rows(api_client):
    active = await _create_job(api_client, prompt="active prompt")
    trashed = await _create_job(api_client, prompt="trashed prompt")
    assert (await api_client.delete(f"/api/jobs/{trashed['id']}")).status_code == 200

    response = await api_client.request(
        "DELETE",
        "/api/trash/permanent",
        json={"job_ids": [active["id"], trashed["id"]]},
    )

    assert response.status_code == 200
    assert response.json()["deleted"] == {"jobs": 1, "projects": 0}
    assert (await api_client.get(f"/api/jobs/{active['id']}")).status_code == 200
    assert (await api_client.get(f"/api/jobs/{trashed['id']}")).status_code == 404


async def test_trash_restore_all_handles_projects_and_jobs(api_client):
    project = await api_client.post("/api/projects", json={"name": "Trash Project"})
    assert project.status_code == 201
    job = await _create_job(api_client)

    assert (await api_client.delete(f"/api/projects/{project.json()['id']}")).status_code == 204
    assert (await api_client.delete(f"/api/jobs/{job['id']}")).status_code == 200

    restore = await api_client.post("/api/trash/restore", json={"all": True})

    assert restore.status_code == 200
    assert restore.json()["restored"] == {"jobs": 1, "projects": 1}
    assert (await api_client.get("/api/trash")).json()["items"] == []


async def test_deleted_jobs_do_not_break_legacy_created_rows(api_client):
    now = datetime.now(UTC).isoformat()
    async with get_db() as db:
        await db.execute(
            """
            INSERT INTO jobs (id, type, status, prompt, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("legacy-job", "text-to-video", "completed", "legacy", now, now),
        )
        await db.commit()

    response = await api_client.get("/api/jobs")

    assert response.status_code == 200
    assert {job["id"] for job in response.json()["items"]} == {"legacy-job"}
