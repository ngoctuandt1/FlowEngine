from unittest.mock import AsyncMock


async def test_post_text_to_image_defaults_model(api_client):
    payload = {
        "type": "text-to-image",
        "prompt": "A glass teapot on a marble table",
        "aspect_ratio": "1:1",
    }

    response = await api_client.post("/api/jobs", json=payload)

    assert response.status_code == 201
    body = response.json()
    assert body["type"] == "text-to-image"
    assert body["model"] == "nano-banana-pro"
    assert body["aspect_ratio"] == "1:1"


async def test_post_text_to_image_keeps_ref_image_path(api_client):
    payload = {
        "type": "text-to-image",
        "prompt": "A product shot of a ceramic mug",
        "model": "imagen-4",
        "ref_image_path": "uploads/reference.png",
    }

    response = await api_client.post("/api/jobs", json=payload)

    assert response.status_code == 201
    body = response.json()
    assert body["model"] == "imagen-4"
    assert body["ref_image_path"] == "uploads/reference.png"


async def test_post_ingredients_to_video_round_trips_ingredient_paths(api_client):
    payload = {
        "type": "ingredients-to-video",
        "prompt": "A cinematic cooking reel with fresh herbs and bright produce",
        "model": "veo-3.1-fast-lp",
        "aspect_ratio": "16:9",
        "ingredient_image_paths": ["uploads/a.png", "uploads/b.png"],
    }

    response = await api_client.post("/api/jobs", json=payload)

    assert response.status_code == 201
    created = response.json()
    assert created["type"] == "ingredients-to-video"
    assert created["ingredient_image_paths"] == ["uploads/a.png", "uploads/b.png"]

    fetched = await api_client.get(f"/api/jobs/{created['id']}")

    assert fetched.status_code == 200
    body = fetched.json()
    assert body["ingredient_image_paths"] == ["uploads/a.png", "uploads/b.png"]


async def test_post_audio_to_video_keeps_audio_path(api_client):
    payload = {
        "type": "audio-to-video",
        "prompt": "Turn this piano riff into a moody neon city montage",
        "audio_path": "uploads/source-audio.wav",
    }

    response = await api_client.post("/api/jobs", json=payload)

    assert response.status_code == 201
    body = response.json()
    assert body["type"] == "audio-to-video"
    assert body["audio_path"] == "uploads/source-audio.wav"


async def test_post_audio_to_video_requires_audio_path(api_client):
    payload = {
        "type": "audio-to-video",
        "prompt": "Animate to the beat",
    }

    response = await api_client.post("/api/jobs", json=payload)

    assert response.status_code == 422
    assert response.json()["detail"] == "audio-to-video job requires 'audio_path'"


async def test_post_audio_to_video_requires_prompt(api_client):
    payload = {
        "type": "audio-to-video",
        "audio_path": "uploads/source-audio.wav",
    }

    response = await api_client.post("/api/jobs", json=payload)

    assert response.status_code == 422
    assert response.json()["detail"] == "audio-to-video job requires 'prompt'"


async def test_list_jobs_includes_audio_to_video(api_client):
    payload = {
        "type": "audio-to-video",
        "prompt": "Build a video around this synth loop",
        "audio_path": "uploads/loop.mp3",
    }

    create_response = await api_client.post("/api/jobs", json=payload)
    assert create_response.status_code == 201

    list_response = await api_client.get("/api/jobs")

    assert list_response.status_code == 200
    jobs = list_response.json()
    assert any(
        job["type"] == "audio-to-video" and job["audio_path"] == "uploads/loop.mp3"
        for job in jobs
    )


async def test_post_job_with_safety_filter_round_trips(api_client):
    response = await api_client.post(
        "/api/jobs",
        json={
            "type": "text-to-video",
            "prompt": "A dramatic fashion film",
            "safety_filter": "block_some",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["safety_filter"] == "block_some"


async def test_post_job_without_safety_filter_defaults_null(api_client):
    response = await api_client.post(
        "/api/jobs",
        json={"type": "text-to-video", "prompt": "A clean product render"},
    )

    assert response.status_code == 201
    assert response.json()["safety_filter"] is None


async def test_list_jobs_includes_safety_filter(api_client):
    create_response = await api_client.post(
        "/api/jobs",
        json={
            "type": "text-to-video",
            "prompt": "A city skyline at dusk",
            "safety_filter": "block_few",
        },
    )
    assert create_response.status_code == 201

    list_response = await api_client.get("/api/jobs")

    assert list_response.status_code == 200
    assert any(
        job["type"] == "text-to-video" and job["safety_filter"] == "block_few"
        for job in list_response.json()
    )


async def test_post_job_with_missing_parent_returns_404(api_client):
    response = await api_client.post(
        "/api/jobs",
        json={
            "type": "extend-video",
            "prompt": "Continue the clip",
            "parent_job_id": "missing-parent",
        },
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Parent job missing-parent not found"


async def test_post_child_job_inherits_completed_parent_fields(api_client):
    parent_response = await api_client.post(
        "/api/jobs",
        json={
            "type": "text-to-video",
            "prompt": "Create a forest flythrough",
            "profile": "parent-profile",
        },
    )
    assert parent_response.status_code == 201
    parent_id = parent_response.json()["id"]

    patch_response = await api_client.put(
        f"/api/worker/jobs/{parent_id}",
        json={
            "status": "completed",
            "project_url": "https://flow.example/project/123",
            "media_id": "media-123",
            "profile": "parent-profile",
        },
    )
    assert patch_response.status_code == 200

    child_response = await api_client.post(
        "/api/jobs",
        json={
            "type": "extend-video",
            "prompt": "Add a second camera pass",
            "parent_job_id": parent_id,
            "profile": "wrong-profile",
        },
    )

    assert child_response.status_code == 201
    child = child_response.json()
    assert child["job_level"] == 2
    assert child["profile"] == "parent-profile"
    assert child["project_url"] == "https://flow.example/project/123"
    assert child["media_id"] == "media-123"


async def test_post_chain_creates_linked_jobs(api_client):
    response = await api_client.post(
        "/api/chains",
        json={
            "profile": "chain-profile",
            "jobs": [
                {"type": "text-to-video", "prompt": "Open with a sunrise shot"},
                {"type": "extend-video", "prompt": "Hold on the skyline"},
            ],
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["chain_id"]
    assert len(body["jobs"]) == 2
    assert body["jobs"][0]["job_level"] == 1
    assert body["jobs"][1]["job_level"] == 2
    assert body["jobs"][1]["parent_job_id"] == body["jobs"][0]["id"]
    assert body["jobs"][0]["chain_id"] == body["chain_id"]
    assert body["jobs"][1]["chain_id"] == body["chain_id"]


async def test_post_chain_rejects_empty_jobs(api_client):
    response = await api_client.post("/api/chains", json={"jobs": []})

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "jobs"]


async def test_get_chain_returns_404_for_unknown_chain(api_client):
    response = await api_client.get("/api/chains/missing-chain")

    assert response.status_code == 404
    assert response.json()["detail"] == "Chain missing-chain not found"


async def test_list_jobs_applies_filters(api_client):
    first = await api_client.post(
        "/api/jobs",
        json={
            "type": "text-to-video",
            "prompt": "Pending skyline shot",
            "profile": "filter-profile",
        },
    )
    second = await api_client.post(
        "/api/jobs",
        json={
            "type": "audio-to-video",
            "prompt": "Animate from audio",
            "audio_path": "uploads/filter.wav",
            "profile": "other-profile",
        },
    )
    assert first.status_code == 201
    assert second.status_code == 201

    filtered = await api_client.get(
        "/api/jobs",
        params={
            "status": "pending",
            "type": "text-to-video",
            "profile": "filter-profile",
        },
    )

    assert filtered.status_code == 200
    jobs = filtered.json()
    assert len(jobs) == 1
    assert jobs[0]["id"] == first.json()["id"]


async def test_get_job_counts_returns_pending_totals(api_client):
    first = await api_client.post(
        "/api/jobs",
        json={"type": "text-to-video", "prompt": "Count me in"},
    )
    second = await api_client.post(
        "/api/jobs",
        json={"type": "text-to-video", "prompt": "And me too"},
    )
    assert first.status_code == 201
    assert second.status_code == 201

    response = await api_client.get("/api/jobs/counts")

    assert response.status_code == 200
    assert response.json()["pending"] >= 2


async def test_get_single_job_returns_404_for_missing_job(api_client):
    response = await api_client.get("/api/jobs/missing-job")

    assert response.status_code == 404
    assert response.json()["detail"] == "Job missing-job not found"


async def test_get_job_children_returns_404_for_missing_parent(api_client):
    response = await api_client.get("/api/jobs/missing-parent/children")

    assert response.status_code == 404
    assert response.json()["detail"] == "Job missing-parent not found"


async def test_delete_job_broadcasts_cancelled_update(api_client, monkeypatch):
    import server.routes.jobs as jobs_route

    created = await api_client.post(
        "/api/jobs",
        json={"type": "text-to-video", "prompt": "Delete this job"},
    )
    assert created.status_code == 201
    job_id = created.json()["id"]

    broadcast = AsyncMock()
    monkeypatch.setattr(jobs_route, "broadcast_job_update", broadcast)

    response = await api_client.delete(f"/api/jobs/{job_id}")

    assert response.status_code == 200
    assert response.json() == {"deleted": job_id}
    assert broadcast.await_count == 1
    cancelled_job = broadcast.await_args.args[0]
    assert cancelled_job.id == job_id
    assert cancelled_job.status.value == "cancelled"


async def test_recover_jobs_broadcasts_each_recovered_job(api_client, monkeypatch):
    import server.routes.jobs as jobs_route
    from server.models.job import Job, JobStatus, JobType

    recovered_jobs = [
        Job(type=JobType.TEXT_TO_VIDEO, prompt="Recovered 1", status=JobStatus.PENDING),
        Job(type=JobType.EXTEND_VIDEO, prompt="Recovered 2", status=JobStatus.PENDING),
    ]
    broadcast = AsyncMock()

    async def fake_recover_stale_jobs():
        return recovered_jobs

    monkeypatch.setattr(jobs_route, "recover_stale_jobs", fake_recover_stale_jobs)
    monkeypatch.setattr(jobs_route, "broadcast_job_update", broadcast)

    response = await api_client.post("/api/jobs/recover")

    assert response.status_code == 200
    assert response.json()["recovered"] == 2
    assert [job["id"] for job in response.json()["jobs"]] == [job.id for job in recovered_jobs]
    assert broadcast.await_count == 2
    assert [call.args[0].id for call in broadcast.await_args_list] == [job.id for job in recovered_jobs]
