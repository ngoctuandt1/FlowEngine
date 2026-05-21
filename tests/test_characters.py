from datetime import datetime
from pathlib import Path

import pytest

from server.db.database import get_db


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
    project = await api_client.post("/api/projects", json={"name": "Character Project"})
    project_id = project.json()["id"]
    payload = {
        "project_id": project_id,
        "name": "Astra",
        "voice_id": "achernar",
        "description": "Lead explorer",
        "image_paths": ["portrait.png", "uploads/profile.png"],
    }

    response = await api_client.post("/api/characters", json=payload)

    assert response.status_code == 201
    body = response.json()
    assert set(body) >= {"id", "project_id", "name", "ref_image_url", "voice_id", "created_at"}
    assert body["project_id"] == project_id
    assert body["name"] == "Astra"
    assert body["ref_image_url"] == "uploads/portrait.png"
    assert body["voice_id"] == "achernar"
    assert body["description"] == "Lead explorer"
    assert body["image_paths"] == ["uploads/portrait.png", "uploads/profile.png"]
    datetime.fromisoformat(body["created_at"])


async def test_create_character_accepts_wave5_ref_image_url(api_client, character_upload_dir):
    _write_upload(character_upload_dir, "wave5.png")

    response = await api_client.post(
        "/api/characters",
        json={"name": "Wave Five", "ref_image_url": "wave5.png"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["ref_image_url"] == "uploads/wave5.png"
    assert body["image_paths"] == ["uploads/wave5.png"]


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


async def test_list_characters_filters_by_valid_project(api_client, character_upload_dir):
    _write_upload(character_upload_dir, "project-alpha.png")
    _write_upload(character_upload_dir, "global-alpha.png")
    project = await api_client.post("/api/projects", json={"name": "Scoped Project"})
    project_id = project.json()["id"]
    await api_client.post(
        "/api/characters",
        json={"project_id": project_id, "name": "Scoped", "image_paths": ["project-alpha.png"]},
    )
    await api_client.post(
        "/api/characters",
        json={"name": "Global", "image_paths": ["global-alpha.png"]},
    )

    response = await api_client.get(f"/api/characters?project_id={project_id}")

    assert response.status_code == 200
    assert [item["name"] for item in response.json()] == ["Scoped"]


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
    project = await api_client.post("/api/projects", json={"name": "Updated Project"})
    project_id = project.json()["id"]
    payload = {
        "project_id": project_id,
        "name": "Delta Prime",
        "voice_id": "achird",
        "description": "Updated notes",
        "image_paths": [str(nested)],
    }

    response = await api_client.put(f"/api/characters/{character_id}", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["project_id"] == project_id
    assert body["name"] == "Delta Prime"
    assert body["ref_image_url"] == "uploads/nested/delta-2.png"
    assert body["voice_id"] == "achird"
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


async def test_create_character_rejects_blank_name(api_client, character_upload_dir):
    _write_upload(character_upload_dir, "blank.png")

    response = await api_client.post(
        "/api/characters",
        json={"name": "   ", "image_paths": ["blank.png"]},
    )

    assert response.status_code == 422


async def test_create_character_rejects_missing_project_binding(api_client, character_upload_dir):
    _write_upload(character_upload_dir, "missing-project.png")

    response = await api_client.post(
        "/api/characters",
        json={
            "project_id": "missing-project",
            "name": "Missing Project",
            "image_paths": ["missing-project.png"],
        },
    )

    assert response.status_code == 404
    assert "Project missing-project not found" in response.json()["detail"]


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


async def test_legacy_character_rows_migrate_to_wave5_schema(api_client):
    async with get_db() as db:
        await db.execute(
            """
            INSERT INTO characters (id, name, description, image_paths, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-character",
                "Legacy",
                "Old local shape",
                '["uploads/legacy.png"]',
                "2026-05-20T00:00:00+00:00",
                "2026-05-20T00:00:00+00:00",
            ),
        )
        await db.commit()

    response = await api_client.get("/api/characters/legacy-character")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == "legacy-character"
    assert body["project_id"] is None
    assert body["name"] == "Legacy"
    assert body["ref_image_url"] == "uploads/legacy.png"
    assert body["voice_id"] is None
    assert body["created_at"] == "2026-05-20T00:00:00Z"
