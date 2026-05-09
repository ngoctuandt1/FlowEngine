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


async def test_requeue_completed_job(api_client):
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

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "pending"
    assert body["error"] is None
    assert body["completed_at"] is None
    assert body["output_files"] == []
    assert body["type"] == "extend-video"
    assert body["profile"] == created["profile"]
    assert body["project_url"] == created["project_url"]
    assert body["media_id"] == created["media_id"]
    assert body["prompt"] == created["prompt"]
    assert body["chain_id"] == created["chain_id"]
    assert body["parent_job_id"] == created["parent_job_id"]
    assert body["job_level"] == created["job_level"]


async def test_requeue_failed_job(api_client):
    created = await _create_job(api_client, prompt="A job that failed")
    failed = await _update_job(
        api_client,
        created["id"],
        status="failed",
        error="browser crashed",
    )
    assert failed["status"] == "failed"
    assert failed["error"] == "browser crashed"
    assert failed["completed_at"] is not None

    response = await api_client.post(f"/api/jobs/{created['id']}/requeue")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "pending"
    assert body["error"] is None
    assert body["completed_at"] is None


async def test_requeue_pending_job_returns_400(api_client):
    created = await _create_job(api_client, prompt="Still pending")

    response = await api_client.post(f"/api/jobs/{created['id']}/requeue")

    assert response.status_code == 400
    assert response.json()["detail"] == "Job is not in a terminal state"
