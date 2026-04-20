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
