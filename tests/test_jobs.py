import aiosqlite


async def _create_job(api_client, **overrides):
    payload = {
        "type": "text-to-video",
        "prompt": "Create a clip",
        "profile": "test-profile",
    }
    payload.update(overrides)
    response = await api_client.post("/api/jobs", json=payload)
    assert response.status_code == 201
    return response.json()


async def _update_job(api_client, job_id: str, **fields):
    response = await api_client.put(f"/api/worker/jobs/{job_id}", json=fields)
    assert response.status_code == 200
    return response.json()


async def _mark_claimed(temp_db_path: str, job_id: str) -> None:
    async with aiosqlite.connect(temp_db_path) as db:
        await db.execute(
            """
            UPDATE jobs
            SET worker_id = ?, claimed_at = ?
            WHERE id = ?
            """,
            ("worker-requeue", "2026-05-17T00:00:00+00:00", job_id),
        )
        await db.commit()


async def _get_job_row(temp_db_path: str, job_id: str) -> dict:
    async with aiosqlite.connect(temp_db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
        row = await cursor.fetchone()
    assert row is not None
    return dict(row)


async def _clear_profile_pin(temp_db_path: str, job_id: str) -> None:
    async with aiosqlite.connect(temp_db_path) as db:
        await db.execute("UPDATE jobs SET profile = NULL WHERE id = ?", (job_id,))
        await db.commit()


async def test_requeue_failed_job_clears_runtime_fields(api_client, temp_db_path):
    created = await _create_job(api_client, prompt="A job that failed")
    await _mark_claimed(temp_db_path, created["id"])
    failed = await _update_job(
        api_client,
        created["id"],
        status="failed",
        error="browser crashed",
        output_files=["downloads/partial.mp4"],
    )
    assert failed["status"] == "failed"
    assert failed["error"] == "browser crashed"
    assert failed["output_files"] == ["downloads/partial.mp4"]
    assert failed["worker_id"] == "worker-requeue"
    assert failed["claimed_at"] is not None
    assert failed["completed_at"] is not None

    response = await api_client.post(f"/api/jobs/{created['id']}/requeue")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "pending"
    assert body["error"] is None
    assert body["completed_at"] is None
    assert body["output_files"] == []
    assert body["worker_id"] is None
    assert body["claimed_at"] is None


async def test_requeue_cancelled_job_clears_runtime_fields(api_client, temp_db_path):
    created = await _create_job(api_client, prompt="A job that was cancelled")
    await _mark_claimed(temp_db_path, created["id"])
    cancelled = await _update_job(
        api_client,
        created["id"],
        status="cancelled",
        error="operator cancelled",
        output_files=["downloads/cancelled.mp4"],
    )
    assert cancelled["status"] == "cancelled"
    assert cancelled["completed_at"] is not None

    response = await api_client.post(f"/api/jobs/{created['id']}/requeue")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "pending"
    assert body["error"] is None
    assert body["completed_at"] is None
    assert body["output_files"] == []
    assert body["worker_id"] is None
    assert body["claimed_at"] is None


async def test_requeue_when_parent_failed_returns_400(api_client):
    parent = await _create_job(api_client, profile="parent-profile")
    child = await _create_job(
        api_client,
        type="extend-video",
        prompt="Extend failed parent",
        parent_job_id=parent["id"],
        profile="parent-profile",
    )
    failed_parent = await _update_job(
        api_client,
        parent["id"],
        status="failed",
        error="parent failed",
    )
    assert failed_parent["status"] == "failed"

    response = await api_client.post(f"/api/jobs/{child['id']}/requeue")

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail.startswith(
        f"parent chain invalid: parent_id={parent['id']} parent_status=failed"
    )
    assert "orphan" in detail


async def test_requeue_completed_job_returns_400(api_client):
    created = await _create_job(
        api_client,
        type="extend-video",
        prompt="Extend this clip",
        project_url="https://flow.example/project/123",
        media_id="parent-media-id",
        parent_job_id=None,
    )
    completed = await _update_job(
        api_client,
        created["id"],
        status="completed",
        output_files=["downloads/wrong-output.mp4"],
        error="wrong duration",
    )
    assert completed["status"] == "completed"
    assert completed["output_files"] == ["downloads/wrong-output.mp4"]
    assert completed["completed_at"] is not None

    response = await api_client.post(f"/api/jobs/{created['id']}/requeue")

    assert response.status_code == 400
    assert response.json()["detail"] == "Only failed or cancelled jobs can be requeued"


async def test_requeue_pending_job_returns_200_without_db_write(api_client, temp_db_path):
    created = await _create_job(api_client, prompt="Still pending")
    before = await _get_job_row(temp_db_path, created["id"])

    response = await api_client.post(f"/api/jobs/{created['id']}/requeue")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "pending"
    after = await _get_job_row(temp_db_path, created["id"])
    assert after == before


async def test_requeue_l2_without_profile_returns_400(api_client, temp_db_path):
    parent = await _create_job(api_client, profile="parent-profile")
    completed_parent = await _update_job(
        api_client,
        parent["id"],
        status="completed",
        project_url="https://flow.example/project/parent",
        media_id="parent-media-id",
    )
    assert completed_parent["status"] == "completed"
    child = await _create_job(
        api_client,
        type="extend-video",
        prompt="Legacy unpinned child",
        parent_job_id=parent["id"],
        profile="parent-profile",
    )
    await _clear_profile_pin(temp_db_path, child["id"])
    failed_child = await _update_job(
        api_client,
        child["id"],
        status="failed",
        error="legacy failure",
    )
    assert failed_child["status"] == "failed"

    response = await api_client.post(f"/api/jobs/{child['id']}/requeue")

    assert response.status_code == 400
    assert response.json()["detail"] == (
        "L2+ submit requires explicit profile or a parent with profile pinned (INV-A)"
    )


async def test_requeue_parent_restores_cascade_cancelled_descendants(
    api_client,
    temp_db_path,
):
    parent = await _create_job(api_client, profile="parent-profile")
    completed_parent = await _update_job(
        api_client,
        parent["id"],
        status="completed",
        project_url="https://flow.example/project/chain",
        media_id="parent-media-id",
    )
    assert completed_parent["status"] == "completed"
    child = await _create_job(
        api_client,
        type="extend-video",
        prompt="Extend after parent",
        parent_job_id=parent["id"],
        profile="parent-profile",
    )
    grandchild = await _create_job(
        api_client,
        type="extend-video",
        prompt="Extend after child",
        parent_job_id=child["id"],
        profile="parent-profile",
    )
    await _mark_claimed(temp_db_path, child["id"])
    running_child = await _update_job(
        api_client,
        child["id"],
        status="running",
        output_files=["downloads/partial-child.mp4"],
    )
    assert running_child["status"] == "running"

    failed_parent = await _update_job(
        api_client,
        parent["id"],
        status="failed",
        error="parent failed after descendants queued",
    )
    assert failed_parent["status"] == "failed"
    cancelled_child = await api_client.get(f"/api/jobs/{child['id']}")
    cancelled_grandchild = await api_client.get(f"/api/jobs/{grandchild['id']}")
    assert cancelled_child.status_code == 200
    assert cancelled_grandchild.status_code == 200
    assert cancelled_child.json()["status"] == "cancelled"
    assert cancelled_child.json()["error"].startswith("parent_failed:")
    assert cancelled_grandchild.json()["status"] == "cancelled"
    assert cancelled_grandchild.json()["error"].startswith("parent_failed:")

    response = await api_client.post(f"/api/jobs/{parent['id']}/requeue")

    assert response.status_code == 200
    assert response.json()["status"] == "pending"
    restored_child = (await api_client.get(f"/api/jobs/{child['id']}")).json()
    restored_grandchild = (await api_client.get(f"/api/jobs/{grandchild['id']}")).json()
    assert restored_child["status"] == "pending"
    assert restored_child["error"] is None
    assert restored_child["completed_at"] is None
    assert restored_child["output_files"] == []
    assert restored_child["worker_id"] is None
    assert restored_child["claimed_at"] is None
    assert restored_grandchild["status"] == "pending"
    assert restored_grandchild["error"] is None
    assert restored_grandchild["completed_at"] is None
