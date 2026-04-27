from pathlib import Path

import pytest


@pytest.fixture
def character_upload_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOW_UPLOAD_DIR", str(tmp_path))
    import server.routes.characters
    monkeypatch.setattr(
        server.routes.characters, "UPLOAD_DIR", tmp_path.resolve(), raising=False
    )
    return tmp_path.resolve()


def _write_upload(upload_dir: Path, relative_path: str) -> Path:
    path = upload_dir / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"test-image")
    return path


async def test_create_character(api_client, character_upload_dir):
    _write_upload(character_upload_dir, "portrait.png")
    _write_upload(character_upload_dir, "profile.png")
    payload = {
        "name": "Astra",
        "description": "Lead explorer",
        "image_paths": ["portrait.png", "uploads/profile.png"],
    }

    response = await api_client.post("/api/characters", json=payload)

    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "Astra"
    assert body["description"] == "Lead explorer"
    assert body["image_paths"] == ["uploads/portrait.png", "uploads/profile.png"]


async def test_list_characters(api_client, character_upload_dir):
    _write_upload(character_upload_dir, "bravo.png")
    _write_upload(character_upload_dir, "alpha.png")
    await api_client.post(
        "/api/characters",
        json={"name": "Bravo", "image_paths": ["bravo.png"]},
    )
    await api_client.post(
        "/api/characters",
        json={"name": "Alpha", "image_paths": ["alpha.png"]},
    )

    response = await api_client.get("/api/characters")

    assert response.status_code == 200
    body = response.json()
    assert [item["name"] for item in body] == ["Alpha", "Bravo"]


async def test_get_character(api_client, character_upload_dir):
    _write_upload(character_upload_dir, "cipher.png")
    created = await api_client.post(
        "/api/characters",
        json={"name": "Cipher", "image_paths": ["cipher.png"]},
    )

    character_id = created.json()["id"]
    response = await api_client.get(f"/api/characters/{character_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == character_id
    assert body["image_paths"] == ["uploads/cipher.png"]


async def test_update_character(api_client, character_upload_dir):
    _write_upload(character_upload_dir, "delta.png")
    created = await api_client.post(
        "/api/characters",
        json={"name": "Delta", "image_paths": ["delta.png"]},
    )
    character_id = created.json()["id"]

    nested = character_upload_dir / "nested" / "delta-2.png"
    nested.parent.mkdir(parents=True, exist_ok=True)
    nested.write_bytes(b"updated-image")
    payload = {
        "name": "Delta Prime",
        "description": "Updated notes",
        "image_paths": [str(nested)],
    }

    response = await api_client.put(f"/api/characters/{character_id}", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Delta Prime"
    assert body["description"] == "Updated notes"
    assert body["image_paths"] == ["uploads/nested/delta-2.png"]


async def test_delete_character(api_client, character_upload_dir):
    _write_upload(character_upload_dir, "echo.png")
    created = await api_client.post(
        "/api/characters",
        json={"name": "Echo", "image_paths": ["echo.png"]},
    )
    character_id = created.json()["id"]

    response = await api_client.delete(f"/api/characters/{character_id}")

    assert response.status_code == 200
    assert response.json() == {"deleted": character_id}

    missing = await api_client.get(f"/api/characters/{character_id}")
    assert missing.status_code == 404


async def test_create_character_name_uniqueness_returns_409(
    api_client, character_upload_dir
):
    _write_upload(character_upload_dir, "foxtrot.png")
    payload = {"name": "Foxtrot", "image_paths": ["foxtrot.png"]}
    await api_client.post("/api/characters", json=payload)

    response = await api_client.post("/api/characters", json=payload)

    assert response.status_code == 409
    assert "already exists" in response.json()["detail"]


async def test_create_character_rejects_path_outside_upload_dir(
    api_client, character_upload_dir, tmp_path
):
    outside = tmp_path.parent / "escape.png"
    payload = {
        "name": "Ghost",
        "image_paths": [str(outside)],
    }

    response = await api_client.post("/api/characters", json=payload)

    assert response.status_code == 400
    assert "escapes FLOW_UPLOAD_DIR" in response.json()["detail"]


async def test_create_character_rejects_nonexistent_path_under_upload_dir(
    api_client, character_upload_dir
):
    response = await api_client.post(
        "/api/characters",
        json={"name": "India", "image_paths": ["uploads/test.png"]},
    )

    assert response.status_code == 400
    assert "does not exist under FLOW_UPLOAD_DIR" in response.json()["detail"]


@pytest.mark.parametrize(
    "image_paths",
    [
        [],
        [f"image-{index}.png" for index in range(11)],
    ],
)
async def test_create_character_rejects_image_path_bounds(
    api_client, character_upload_dir, image_paths
):
    response = await api_client.post(
        "/api/characters",
        json={"name": "Hotel", "image_paths": image_paths},
    )

    assert response.status_code == 422
