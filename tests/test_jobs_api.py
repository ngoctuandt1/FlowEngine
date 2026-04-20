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
