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
