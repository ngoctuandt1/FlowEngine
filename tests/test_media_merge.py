from pathlib import Path
from subprocess import CompletedProcess
import subprocess

import pytest
from fastapi.testclient import TestClient


def _build_client(monkeypatch, tmp_path):
    import server.app
    import server.routes.media_merge as media_merge

    download_dir = tmp_path / "downloads"
    upload_dir = tmp_path / "uploads"
    merge_dir = download_dir / "merges"
    download_dir.mkdir(parents=True)
    upload_dir.mkdir(parents=True)

    monkeypatch.setattr(server.app, "DOWNLOAD_DIR", download_dir, raising=False)
    monkeypatch.setattr(server.app, "UPLOAD_DIR", upload_dir, raising=False)
    monkeypatch.setattr(media_merge, "DOWNLOAD_DIR", download_dir.resolve(), raising=False)
    monkeypatch.setattr(media_merge, "UPLOAD_DIR", upload_dir.resolve(), raising=False)
    monkeypatch.setattr(media_merge, "MERGE_DIR", merge_dir.resolve(), raising=False)

    return TestClient(server.app.app), download_dir, upload_dir, merge_dir


def test_media_merge_happy_path(temp_db_path, monkeypatch, tmp_path):
    import server.routes.media_merge as media_merge

    client, download_dir, _, merge_dir = _build_client(monkeypatch, tmp_path)
    first = download_dir / "a.mp4"
    second = download_dir / "b.mp4"
    first.write_bytes(b"a")
    second.write_bytes(b"b")

    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        if command[0] == "ffprobe":
            return CompletedProcess(command, 0, stdout="15.5\n", stderr="")
        output_path = Path(command[-1])
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"merged")
        return CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(media_merge.shutil, "which", lambda name: "ffprobe" if name == "ffprobe" else None)
    monkeypatch.setattr(media_merge.subprocess, "run", fake_run)

    response = client.post(
        "/api/media/merge",
        json={"input_paths": ["downloads/a.mp4", "downloads/b.mp4"]},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["source_count"] == 2
    assert body["duration_seconds"] == 31.0
    assert body["output_path"].startswith("merges/merge_")
    assert Path(body["output_path"]).suffix == ".mp4"
    assert len(calls) == 3
    ffmpeg_command, ffmpeg_kwargs = calls[-1]
    assert ffmpeg_command[:6] == ["ffmpeg", "-f", "concat", "-safe", "0", "-i"]
    assert ffmpeg_kwargs["timeout"] == media_merge.SUBPROCESS_TIMEOUT_SECONDS


def test_media_merge_rejects_traversal(temp_db_path, monkeypatch, tmp_path):
    import server.routes.media_merge as media_merge

    client, download_dir, _, _ = _build_client(monkeypatch, tmp_path)
    (download_dir / "ok.mp4").write_bytes(b"ok")
    monkeypatch.setattr(media_merge.shutil, "which", lambda name: None)

    response = client.post(
        "/api/media/merge",
        json={"input_paths": ["downloads/ok.mp4", "../../escape.mp4"]},
    )

    assert response.status_code == 400
    assert "resolve under downloads/ or uploads/" in response.json()["detail"]


def test_media_merge_rejects_too_few_paths(temp_db_path, monkeypatch, tmp_path):
    import server.routes.media_merge as media_merge

    client, download_dir, _, _ = _build_client(monkeypatch, tmp_path)
    (download_dir / "solo.mp4").write_bytes(b"solo")
    monkeypatch.setattr(media_merge.shutil, "which", lambda name: None)

    response = client.post(
        "/api/media/merge",
        json={"input_paths": ["downloads/solo.mp4"]},
    )

    assert response.status_code == 400
    assert "between 2 and 20" in response.json()["detail"]


def test_media_merge_rejects_too_many_paths(temp_db_path, monkeypatch, tmp_path):
    import server.routes.media_merge as media_merge

    client, download_dir, _, _ = _build_client(monkeypatch, tmp_path)
    for index in range(21):
        (download_dir / f"{index}.mp4").write_bytes(b"x")
    monkeypatch.setattr(media_merge.shutil, "which", lambda name: None)

    response = client.post(
        "/api/media/merge",
        json={"input_paths": [f"downloads/{index}.mp4" for index in range(21)]},
    )

    assert response.status_code == 400
    assert "between 2 and 20" in response.json()["detail"]


def test_media_merge_rejects_total_duration_over_30_minutes(temp_db_path, monkeypatch, tmp_path):
    import server.routes.media_merge as media_merge

    client, download_dir, _, _ = _build_client(monkeypatch, tmp_path)
    (download_dir / "a.mp4").write_bytes(b"a")
    (download_dir / "b.mp4").write_bytes(b"b")

    def fake_run(command, **kwargs):
        if command[0] == "ffprobe":
            return CompletedProcess(command, 0, stdout="901\n", stderr="")
        raise AssertionError("ffmpeg should not run when duration validation fails")

    monkeypatch.setattr(media_merge.shutil, "which", lambda name: "ffprobe")
    monkeypatch.setattr(media_merge.subprocess, "run", fake_run)

    response = client.post(
        "/api/media/merge",
        json={"input_paths": ["downloads/a.mp4", "downloads/b.mp4"]},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "total duration exceeds 30 minutes"


def test_media_merge_returns_500_on_ffmpeg_failure(temp_db_path, monkeypatch, tmp_path):
    import server.routes.media_merge as media_merge

    client, download_dir, _, _ = _build_client(monkeypatch, tmp_path)
    (download_dir / "a.mp4").write_bytes(b"a")
    (download_dir / "b.mp4").write_bytes(b"b")

    def fake_run(command, **kwargs):
        if command[0] == "ffprobe":
            return CompletedProcess(command, 0, stdout="10\n", stderr="")
        return CompletedProcess(command, 1, stdout="", stderr="concat failed")

    monkeypatch.setattr(media_merge.shutil, "which", lambda name: "ffprobe")
    monkeypatch.setattr(media_merge.subprocess, "run", fake_run)

    response = client.post(
        "/api/media/merge",
        json={"input_paths": ["downloads/a.mp4", "downloads/b.mp4"]},
    )

    assert response.status_code == 500
    assert "ffmpeg merge failed" in response.json()["detail"]


def test_media_merge_response_contains_output_path(temp_db_path, monkeypatch, tmp_path):
    import server.routes.media_merge as media_merge

    client, download_dir, _, merge_dir = _build_client(monkeypatch, tmp_path)
    (download_dir / "a.mp4").write_bytes(b"a")
    (download_dir / "b.mp4").write_bytes(b"b")

    def fake_run(command, **kwargs):
        output_path = Path(command[-1])
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"merged")
        return CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(media_merge.shutil, "which", lambda name: None)
    monkeypatch.setattr(media_merge.subprocess, "run", fake_run)

    response = client.post(
        "/api/media/merge",
        json={"input_paths": ["downloads/a.mp4", "downloads/b.mp4"]},
    )

    assert response.status_code == 200
    assert response.json()["output_path"].startswith("merges/")


def test_media_merge_accepts_absolute_paths_under_allowed_roots(temp_db_path, monkeypatch, tmp_path):
    import server.routes.media_merge as media_merge

    client, download_dir, upload_dir, _ = _build_client(monkeypatch, tmp_path)
    first = download_dir / "a.mp4"
    second = upload_dir / "b.mp4"
    first.write_bytes(b"a")
    second.write_bytes(b"b")

    def fake_run(command, **kwargs):
        output_path = Path(command[-1])
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"merged")
        return CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(media_merge.shutil, "which", lambda name: None)
    monkeypatch.setattr(media_merge.subprocess, "run", fake_run)

    response = client.post(
        "/api/media/merge",
        json={"input_paths": [str(first.resolve()), str(second.resolve())]},
    )

    assert response.status_code == 200
    assert response.json()["source_count"] == 2


def test_media_merge_resolves_existing_relative_download_path(temp_db_path, monkeypatch, tmp_path):
    import server.routes.media_merge as media_merge

    client, download_dir, _, _ = _build_client(monkeypatch, tmp_path)
    (download_dir / "x.mp4").write_bytes(b"x")
    (download_dir / "y.mp4").write_bytes(b"y")

    def fake_run(command, **kwargs):
        output_path = Path(command[-1])
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"merged")
        return CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(media_merge.shutil, "which", lambda name: None)
    monkeypatch.setattr(media_merge.subprocess, "run", fake_run)

    response = client.post(
        "/api/media/merge",
        json={"input_paths": ["x.mp4", "y.mp4"]},
    )

    assert response.status_code == 200
    assert response.json()["source_count"] == 2


def test_media_merge_returns_504_and_cleans_partial_output_on_ffmpeg_timeout(
    temp_db_path, monkeypatch, tmp_path
):
    import server.routes.media_merge as media_merge

    client, download_dir, _, merge_dir = _build_client(monkeypatch, tmp_path)
    (download_dir / "a.mp4").write_bytes(b"a")
    (download_dir / "b.mp4").write_bytes(b"b")

    def fake_run(command, **kwargs):
        if command[0] == "ffprobe":
            return CompletedProcess(command, 0, stdout="10\n", stderr="")
        output_path = Path(command[-1])
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"partial")
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(media_merge.shutil, "which", lambda name: "ffprobe")
    monkeypatch.setattr(media_merge.subprocess, "run", fake_run)

    response = client.post(
        "/api/media/merge",
        json={"input_paths": ["downloads/a.mp4", "downloads/b.mp4"]},
    )

    assert response.status_code == 504
    assert response.json()["detail"] == "ffmpeg merge timed out"
    assert list(merge_dir.glob("*.mp4")) == []


def test_media_merge_returns_500_and_cleans_partial_output_on_ffmpeg_oserror(
    temp_db_path, monkeypatch, tmp_path
):
    import server.routes.media_merge as media_merge

    client, download_dir, _, merge_dir = _build_client(monkeypatch, tmp_path)
    (download_dir / "a.mp4").write_bytes(b"a")
    (download_dir / "b.mp4").write_bytes(b"b")

    def fake_run(command, **kwargs):
        if command[0] == "ffprobe":
            return CompletedProcess(command, 0, stdout="10\n", stderr="")
        output_path = Path(command[-1])
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"partial")
        raise OSError("ffmpeg missing")

    monkeypatch.setattr(media_merge.shutil, "which", lambda name: "ffprobe")
    monkeypatch.setattr(media_merge.subprocess, "run", fake_run)

    response = client.post(
        "/api/media/merge",
        json={"input_paths": ["downloads/a.mp4", "downloads/b.mp4"]},
    )

    assert response.status_code == 500
    assert "ffmpeg unavailable" in response.json()["detail"]
    assert list(merge_dir.glob("*.mp4")) == []


def test_probe_duration_seconds_returns_504_on_timeout(monkeypatch):
    import server.routes.media_merge as media_merge

    def fake_run(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(media_merge.subprocess, "run", fake_run)

    with pytest.raises(media_merge.HTTPException) as exc_info:
        media_merge._probe_duration_seconds(Path("clip.mp4"))

    assert exc_info.value.status_code == 504
    assert exc_info.value.detail == "ffprobe timed out for clip.mp4"


def test_probe_duration_seconds_rejects_invalid_duration(monkeypatch):
    import server.routes.media_merge as media_merge

    monkeypatch.setattr(
        media_merge.subprocess,
        "run",
        lambda command, **kwargs: CompletedProcess(command, 0, stdout="not-a-number", stderr=""),
    )

    with pytest.raises(media_merge.HTTPException) as exc_info:
        media_merge._probe_duration_seconds(Path("clip.mp4"))

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "invalid ffprobe duration for clip.mp4"


def test_media_merge_surfaces_filelist_write_failure(temp_db_path, monkeypatch, tmp_path):
    import server.app
    import server.routes.media_merge as media_merge

    client, download_dir, upload_dir, merge_dir = _build_client(monkeypatch, tmp_path)
    (download_dir / "a.mp4").write_bytes(b"a")
    (download_dir / "b.mp4").write_bytes(b"b")

    def fail_write_text(self, *args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(media_merge.shutil, "which", lambda name: None)
    monkeypatch.setattr(media_merge.Path, "write_text", fail_write_text)
    client = TestClient(server.app.app, raise_server_exceptions=False)

    response = client.post(
        "/api/media/merge",
        json={"input_paths": ["downloads/a.mp4", "downloads/b.mp4"]},
    )

    assert response.status_code == 500
