import asyncio
from datetime import UTC, datetime

from server.db.database import get_db
from server.db.trash_store import permanently_delete_trash, restore_trash


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


async def _create_project(api_client, *, name: str = "Trash Project") -> dict:
    response = await api_client.post("/api/projects", json={"name": name})
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


async def test_restore_preserves_completed_job_status(api_client):
    job = await _create_job(api_client, prompt="completed before trash")
    patch = await api_client.put(
        f"/api/worker/jobs/{job['id']}",
        json={"status": "completed", "output_files": ["downloads/done.mp4"]},
    )
    assert patch.status_code == 200
    assert patch.json()["status"] == "completed"

    assert (await api_client.delete(f"/api/jobs/{job['id']}")).status_code == 200
    restore = await api_client.post("/api/trash/restore", json={"job_ids": [job["id"]]})

    assert restore.status_code == 200
    restored = await api_client.get(f"/api/jobs/{job['id']}")
    assert restored.status_code == 200
    assert restored.json()["status"] == "completed"
    assert restored.json()["output_files"] == ["downloads/done.mp4"]


async def test_soft_delete_parent_with_active_child_is_rejected(api_client):
    chain_response = await api_client.post(
        "/api/chains",
        json={
            "profile": "trash-chain-profile",
            "jobs": [
                {"type": "text-to-video", "prompt": "parent"},
                {"type": "extend-video", "prompt": "child"},
            ],
        },
    )
    assert chain_response.status_code == 201
    parent_id = chain_response.json()["jobs"][0]["id"]
    child_id = chain_response.json()["jobs"][1]["id"]

    delete_parent = await api_client.delete(f"/api/jobs/{parent_id}")

    assert delete_parent.status_code == 409
    assert (await api_client.get(f"/api/jobs/{parent_id}")).status_code == 200
    assert (await api_client.get(f"/api/jobs/{child_id}")).status_code == 200
    assert (await api_client.get("/api/trash")).json()["items"] == []


async def test_permanent_delete_all_removes_trashed_parent_child_tree(api_client):
    chain_response = await api_client.post(
        "/api/chains",
        json={
            "profile": "trash-tree-profile",
            "jobs": [
                {"type": "text-to-video", "prompt": "tree parent"},
                {"type": "extend-video", "prompt": "tree child"},
            ],
        },
    )
    assert chain_response.status_code == 201
    parent_id = chain_response.json()["jobs"][0]["id"]
    child_id = chain_response.json()["jobs"][1]["id"]

    assert (await api_client.delete(f"/api/jobs/{child_id}")).status_code == 200
    assert (await api_client.delete(f"/api/jobs/{parent_id}")).status_code == 200

    response = await api_client.request(
        "DELETE",
        "/api/trash/permanent",
        json={"all": True},
    )

    assert response.status_code == 200
    assert response.json()["deleted"] == {"jobs": 2, "projects": 0}
    assert (await api_client.get("/api/trash")).json()["items"] == []
    assert (await api_client.get(f"/api/jobs/{parent_id}")).status_code == 404
    assert (await api_client.get(f"/api/jobs/{child_id}")).status_code == 404


async def test_deleted_project_id_rejected_for_job_and_chain_creation(api_client):
    project = await _create_project(api_client, name="Deleted Binding Project")
    project_id = project["id"]
    assert (await api_client.delete(f"/api/projects/{project_id}")).status_code == 204

    job_response = await api_client.post(
        "/api/jobs",
        json={
            "type": "text-to-video",
            "prompt": "should reject deleted project",
            "profile": "trash-profile",
            "project_id": project_id,
        },
    )
    chain_response = await api_client.post(
        "/api/chains",
        json={
            "profile": "trash-profile",
            "jobs": [
                {
                    "type": "text-to-video",
                    "prompt": "should reject deleted project in chain",
                    "project_id": project_id,
                },
                {"type": "extend-video", "prompt": "child"},
            ],
        },
    )

    assert job_response.status_code == 404
    assert chain_response.status_code == 404


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


async def test_concurrent_restore_and_permanent_delete_is_stable(api_client):
    job = await _create_job(api_client, prompt="race prompt")
    assert (await api_client.delete(f"/api/jobs/{job['id']}")).status_code == 200

    restore_counts, delete_counts = await asyncio.gather(
        restore_trash(job_ids=[job["id"]]),
        permanently_delete_trash(job_ids=[job["id"]]),
    )

    assert restore_counts["jobs"] + delete_counts["jobs"] == 1
    response = await api_client.get(f"/api/jobs/{job['id']}")
    if restore_counts["jobs"] == 1:
        assert response.status_code == 200
    else:
        assert response.status_code == 404
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
