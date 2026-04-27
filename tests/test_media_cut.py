from pathlib import Path
from subprocess import CompletedProcess

from fastapi.testclient import TestClient


def _make_client(temp_db_path, monkeypatch, tmp_path):
    import server.app
    import server.routes.media_cut

    download_dir = (tmp_path / "downloads").resolve()
    upload_dir = (tmp_path / "uploads").resolve()
    cuts_dir = (tmp_path / "data" / "cuts").resolve()
    download_dir.mkdir(parents=True, exist_ok=True)
    upload_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(server.app, "DOWNLOAD_DIR", download_dir, raising=False)
    monkeypatch.setattr(server.app, "UPLOAD_DIR", upload_dir, raising=False)
    monkeypatch.setattr(server.routes.media_cut, "DOWNLOAD_DIR", download_dir, raising=False)
    monkeypatch.setattr(server.routes.media_cut, "UPLOAD_DIR", upload_dir, raising=False)
    monkeypatch.setattr(server.routes.media_cut, "CUTS_DIR", cuts_dir, raising=False)

    return TestClient(server.app.app), download_dir, upload_dir, cuts_dir


def test_media_cut_happy_path_runs_ffmpeg_and_returns_duration(temp_db_path, monkeypatch, tmp_path):
    client, download_dir, _upload_dir, cuts_dir = _make_client(temp_db_path, monkeypatch, tmp_path)
    source = download_dir / "clip.mp4"
    source.write_bytes(b"fake-video")

    calls = {}

    def fake_run(cmd, capture_output, text, timeout):
        calls["cmd"] = cmd
        calls["capture_output"] = capture_output
        calls["text"] = text
        calls["timeout"] = timeout
        return CompletedProcess(cmd, 0, stdout="", stderr="")

    import server.routes.media_cut

    monkeypatch.setattr(server.routes.media_cut.subprocess, "run", fake_run)

    response = client.post(
        "/api/media/cut",
        json={
            "input_path": str(source),
            "start_seconds": 1.5,
            "end_seconds": 5.0,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["duration_seconds"] == 3.5
    assert Path(payload["output_path"]).parent == cuts_dir
    assert calls["cmd"][:7] == [
        "ffmpeg",
        "-ss",
        "1.5",
        "-to",
        "5.0",
        "-i",
        str(source),
    ]
    assert calls["cmd"][-2:] == ["copy", str(Path(payload["output_path"]))]
    assert calls["capture_output"] is True
    assert calls["text"] is True
    assert calls["timeout"] == 120


def test_media_cut_rejects_traversal_outside_allowed_dirs(temp_db_path, monkeypatch, tmp_path):
    client, download_dir, _upload_dir, _cuts_dir = _make_client(temp_db_path, monkeypatch, tmp_path)
    outside = download_dir.parent / "escape.mp4"

    response = client.post(
        "/api/media/cut",
        json={
            "input_path": str(outside),
            "start_seconds": 0,
            "end_seconds": 1,
        },
    )

    assert response.status_code == 400
    assert "FLOW_DOWNLOAD_DIR or FLOW_UPLOAD_DIR" in response.json()["detail"]


def test_media_cut_rejects_start_greater_than_or_equal_end(temp_db_path, monkeypatch, tmp_path):
    client, download_dir, _upload_dir, _cuts_dir = _make_client(temp_db_path, monkeypatch, tmp_path)
    source = download_dir / "clip.mp4"
    source.write_bytes(b"fake-video")

    response = client.post(
        "/api/media/cut",
        json={
            "input_path": str(source),
            "start_seconds": 10,
            "end_seconds": 10,
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "start_seconds must be < end_seconds"


def test_media_cut_rejects_slice_longer_than_ten_minutes(temp_db_path, monkeypatch, tmp_path):
    client, download_dir, _upload_dir, _cuts_dir = _make_client(temp_db_path, monkeypatch, tmp_path)
    source = download_dir / "clip.mp4"
    source.write_bytes(b"fake-video")

    response = client.post(
        "/api/media/cut",
        json={
            "input_path": str(source),
            "start_seconds": 0,
            "end_seconds": 601,
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Cut duration must be <= 600 seconds"


def test_media_cut_returns_500_when_ffmpeg_fails(temp_db_path, monkeypatch, tmp_path):
    client, download_dir, _upload_dir, _cuts_dir = _make_client(temp_db_path, monkeypatch, tmp_path)
    source = download_dir / "clip.mp4"
    source.write_bytes(b"fake-video")

    import server.routes.media_cut

    monkeypatch.setattr(
        server.routes.media_cut.subprocess,
        "run",
        lambda *args, **kwargs: CompletedProcess(args[0], 1, stdout="", stderr="boom"),
    )

    response = client.post(
        "/api/media/cut",
        json={
            "input_path": str(source),
            "start_seconds": 1,
            "end_seconds": 2,
        },
    )

    assert response.status_code == 500
    assert response.json()["detail"] == "ffmpeg failed to cut media"


def test_media_cut_output_path_resolves_under_data_dir(temp_db_path, monkeypatch, tmp_path):
    client, _download_dir, upload_dir, cuts_dir = _make_client(temp_db_path, monkeypatch, tmp_path)
    source = upload_dir / "clip.mp4"
    source.write_bytes(b"fake-video")

    import server.routes.media_cut

    monkeypatch.setattr(
        server.routes.media_cut.subprocess,
        "run",
        lambda *args, **kwargs: CompletedProcess(args[0], 0, stdout="", stderr=""),
    )

    response = client.post(
        "/api/media/cut",
        json={
            "input_path": "uploads/clip.mp4",
            "start_seconds": 0,
            "end_seconds": 3,
        },
    )

    assert response.status_code == 200
    output_path = Path(response.json()["output_path"]).resolve()
    assert output_path.is_relative_to(cuts_dir.parent)
    assert output_path.is_relative_to(cuts_dir)
