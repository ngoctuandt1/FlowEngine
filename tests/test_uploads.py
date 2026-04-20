from pathlib import Path

from fastapi.testclient import TestClient


def test_upload_image_accepts_valid_png(temp_db_path, monkeypatch, tmp_path):
    import server.app
    import server.routes.uploads

    monkeypatch.setattr(server.app, "UPLOAD_DIR", tmp_path, raising=False)
    monkeypatch.setattr(server.routes.uploads, "UPLOAD_DIR", tmp_path, raising=False)

    client = TestClient(server.app.app)
    png_bytes = b"\x89PNG\r\n\x1a\nfake-png"

    response = client.post(
        "/api/uploads",
        files={"file": ("frame.png", png_bytes, "image/png")},
    )

    assert response.status_code == 200
    rel_path = response.json()["path"]
    assert rel_path.startswith("uploads/")
    assert (tmp_path / Path(rel_path).name).is_file()


def test_upload_image_rejects_oversized_file(temp_db_path, monkeypatch, tmp_path):
    import server.app
    import server.routes.uploads

    monkeypatch.setattr(server.app, "UPLOAD_DIR", tmp_path, raising=False)
    monkeypatch.setattr(server.routes.uploads, "UPLOAD_DIR", tmp_path, raising=False)

    client = TestClient(server.app.app)
    payload = b"a" * ((10 * 1024 * 1024) + 1)

    response = client.post(
        "/api/uploads",
        files={"file": ("big.png", payload, "image/png")},
    )

    assert response.status_code == 400


def test_upload_image_rejects_text_plain(temp_db_path, monkeypatch, tmp_path):
    import server.app
    import server.routes.uploads

    monkeypatch.setattr(server.app, "UPLOAD_DIR", tmp_path, raising=False)
    monkeypatch.setattr(server.routes.uploads, "UPLOAD_DIR", tmp_path, raising=False)

    client = TestClient(server.app.app)

    response = client.post(
        "/api/uploads",
        files={"file": ("note.txt", b"hello", "text/plain")},
    )

    assert response.status_code == 415
