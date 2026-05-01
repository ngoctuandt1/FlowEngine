import io
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image


def _make_image_bytes(format_name: str) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (2, 2), color=(12, 34, 56)).save(buffer, format=format_name)
    return buffer.getvalue()


@pytest.fixture
def upload_client(temp_db_path, monkeypatch, tmp_path):
    import server.app
    import server.routes.uploads

    monkeypatch.setattr(server.app, "UPLOAD_DIR", tmp_path, raising=False)
    monkeypatch.setattr(server.routes.uploads, "UPLOAD_DIR", tmp_path, raising=False)

    with TestClient(server.app.app) as client:
        yield client, tmp_path


def test_upload_image_accepts_real_jpeg(upload_client):
    client, upload_dir = upload_client

    response = client.post(
        "/api/uploads",
        files={"file": ("frame.jpg", _make_image_bytes("JPEG"), "image/jpeg")},
    )

    assert response.status_code == 200
    rel_path = response.json()["path"]
    assert rel_path.startswith("uploads/")
    assert rel_path.endswith(".jpg")
    assert (upload_dir / Path(rel_path).name).is_file()


def test_upload_image_rejects_oversized_stream(upload_client):
    client, upload_dir = upload_client

    response = client.post(
        "/api/uploads",
        files={
            "file": (
                "big.bin",
                os.urandom(11 * 1024 * 1024),
                "application/octet-stream",
            )
        },
    )

    assert response.status_code == 413
    assert response.json()["detail"] == "File exceeds 10 MB limit"
    assert list(upload_dir.iterdir()) == []


def test_upload_image_rejects_non_image_payload(upload_client):
    client, upload_dir = upload_client

    response = client.post(
        "/api/uploads",
        files={
            "file": (
                "note.bin",
                b"hello from a text payload",
                "application/octet-stream",
            )
        },
    )

    assert response.status_code == 415
    assert response.json()["detail"] == "Unsupported file type"
    assert list(upload_dir.iterdir()) == []


def test_upload_image_rejects_spoofed_content_type(upload_client):
    client, upload_dir = upload_client
    pdf_bytes = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n%%EOF"

    response = client.post(
        "/api/uploads",
        files={"file": ("frame.png", pdf_bytes, "image/png")},
    )

    assert response.status_code == 415
    assert response.json()["detail"] == "Unsupported file type"
    assert list(upload_dir.iterdir()) == []


def test_upload_image_accepts_generic_content_type_when_magic_bytes_match(upload_client):
    client, upload_dir = upload_client

    response = client.post(
        "/api/uploads",
        files={
            "file": (
                "frame.bin",
                _make_image_bytes("PNG"),
                "application/octet-stream",
            )
        },
    )

    assert response.status_code == 200
    rel_path = response.json()["path"]
    assert rel_path.startswith("uploads/")
    assert rel_path.endswith(".png")
    assert (upload_dir / Path(rel_path).name).is_file()
