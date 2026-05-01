def _detail_text(response) -> str:
    try:
        body = response.json()
    except Exception:
        return response.text
    return str(body.get("detail", body))


async def test_post_frames_to_video_without_start_image_path_returns_422(api_client):
    response = await api_client.post(
        "/api/jobs",
        json={
            "type": "frames-to-video",
            "prompt": "Animate this storyboard frame into motion",
        },
    )

    assert response.status_code == 422
    assert "frames-to-video requires start_image_path" in _detail_text(response)


async def test_post_frames_to_video_with_start_image_path_returns_201(api_client):
    response = await api_client.post(
        "/api/jobs",
        json={
            "type": "frames-to-video",
            "prompt": "Animate this storyboard frame into motion",
            "start_image_path": "uploads/start-frame.png",
        },
    )

    assert response.status_code == 201
    assert response.json()["start_image_path"] == "uploads/start-frame.png"


async def test_post_ingredients_to_video_without_ingredients_returns_422(api_client):
    response = await api_client.post(
        "/api/jobs",
        json={
            "type": "ingredients-to-video",
            "prompt": "Turn these references into a branded product reel",
        },
    )

    assert response.status_code == 422
    assert "ingredients-to-video requires ingredient_image_paths" in _detail_text(response)


async def test_post_ingredients_to_video_with_ingredients_returns_201(api_client):
    response = await api_client.post(
        "/api/jobs",
        json={
            "type": "ingredients-to-video",
            "prompt": "Turn these references into a branded product reel",
            "ingredient_image_paths": ["uploads/a.png", "uploads/b.png"],
        },
    )

    assert response.status_code == 201
    assert response.json()["ingredient_image_paths"] == ["uploads/a.png", "uploads/b.png"]


async def test_post_insert_object_without_bbox_returns_422(api_client):
    response = await api_client.post(
        "/api/jobs",
        json={
            "type": "insert-object",
            "prompt": "Add a branded soda can to the table",
        },
    )

    assert response.status_code == 422
    assert "insert-object requires bbox" in _detail_text(response)


async def test_post_remove_object_without_bbox_returns_422(api_client):
    response = await api_client.post(
        "/api/jobs",
        json={
            "type": "remove-object",
        },
    )

    assert response.status_code == 422
    assert "remove-object requires bbox" in _detail_text(response)


async def test_post_camera_move_without_direction_returns_422(api_client):
    response = await api_client.post(
        "/api/jobs",
        json={
            "type": "camera-move",
        },
    )

    assert response.status_code == 422
    assert "camera-move requires 'direction'" in _detail_text(response)


async def test_post_camera_move_with_unknown_direction_returns_422(api_client):
    response = await api_client.post(
        "/api/jobs",
        json={
            "type": "camera-move",
            "direction": "Pan left",
        },
    )

    assert response.status_code == 422
    detail = _detail_text(response)
    assert "camera preset" in detail or "Valid presets" in detail
