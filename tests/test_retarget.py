from pathlib import Path
import subprocess

import pytest

import server.routes.retarget as retarget


def _patch_dirs(monkeypatch, tmp_path):
    download_dir = (tmp_path / "downloads").resolve()
    upload_dir = (tmp_path / "uploads").resolve()
    data_dir = (tmp_path / "data").resolve()
    download_dir.mkdir(parents=True, exist_ok=True)
    upload_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("FLOW_DOWNLOAD_DIR", str(download_dir))
    monkeypatch.setenv("FLOW_UPLOAD_DIR", str(upload_dir))
    monkeypatch.setattr(retarget, "DATA_DIR", data_dir, raising=False)
    return download_dir, upload_dir, data_dir


@pytest.mark.asyncio
async def test_post_retarget_happy_path_queues_frames_job(api_client, monkeypatch, tmp_path):
    download_dir, _, data_dir = _patch_dirs(monkeypatch, tmp_path)
    ref_video = download_dir / "reference.mp4"
    ref_video.write_text("video", encoding="utf-8")

    ffmpeg_calls = []
    created_jobs = []

    def fake_run(cmd, **kwargs):
        ffmpeg_calls.append((cmd, kwargs))
        output_path = Path(cmd[-1])
        output_path.write_text("frame", encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    async def fake_create_job(job):
        created_jobs.append(job)
        return job

    monkeypatch.setattr(retarget.subprocess, "run", fake_run)
    monkeypatch.setattr(retarget, "create_job", fake_create_job)

    response = await api_client.post(
        "/api/retarget",
        json={
            "reference_video_path": str(ref_video),
            "new_prompt": "Turn this into a rainy cyberpunk street scene",
            "profile": "acct-a",
            "aspect_ratio": "9:16",
            "model": "veo-3.1-fast-lp",
            "frame_seconds": 2.5,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["job_id"] == created_jobs[0].id
    assert Path(body["frame_path"]).parent == data_dir / "retarget"
    assert "queued" in body["message"].lower()

    assert len(ffmpeg_calls) == 1
    command, kwargs = ffmpeg_calls[0]
    assert command[:6] == ["ffmpeg", "-ss", "2.5", "-i", str(ref_video), "-frames:v"]
    assert kwargs["timeout"] == 30

    assert len(created_jobs) == 1
    job = created_jobs[0]
    assert job.type.value == "frames-to-video"
    assert job.prompt == "Turn this into a rainy cyberpunk street scene"
    assert job.profile == "acct-a"
    assert job.aspect_ratio == "9:16"
    assert job.model == "veo-3.1-fast-lp"
    assert job.start_image_path == body["frame_path"]


@pytest.mark.asyncio
async def test_post_retarget_rejects_path_traversal(api_client, monkeypatch, tmp_path):
    _patch_dirs(monkeypatch, tmp_path)

    response = await api_client.post(
        "/api/retarget",
        json={
            "reference_video_path": "../../secret.mp4",
            "new_prompt": "Retarget it",
        },
    )

    assert response.status_code == 400
    assert "FLOW_DOWNLOAD_DIR or FLOW_UPLOAD_DIR" in response.json()["detail"]


@pytest.mark.asyncio
async def test_post_retarget_rejects_absolute_path_outside_allowed_dirs(api_client, monkeypatch, tmp_path):
    _patch_dirs(monkeypatch, tmp_path)
    outside = (tmp_path / "elsewhere" / "video.mp4").resolve()
    outside.parent.mkdir(parents=True, exist_ok=True)
    outside.write_text("video", encoding="utf-8")

    response = await api_client.post(
        "/api/retarget",
        json={
            "reference_video_path": str(outside),
            "new_prompt": "Retarget it",
        },
    )

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_post_retarget_returns_500_when_ffmpeg_fails(api_client, monkeypatch, tmp_path):
    _, upload_dir, _ = _patch_dirs(monkeypatch, tmp_path)
    ref_video = upload_dir / "reference.mp4"
    ref_video.write_text("video", encoding="utf-8")

    def fake_run(cmd, **kwargs):
        raise subprocess.CalledProcessError(1, cmd, stderr="bad ffmpeg run")

    async def fake_create_job(job):
        raise AssertionError("create_job should not be called when ffmpeg fails")

    monkeypatch.setattr(retarget.subprocess, "run", fake_run)
    monkeypatch.setattr(retarget, "create_job", fake_create_job)

    response = await api_client.post(
        "/api/retarget",
        json={
            "reference_video_path": str(ref_video),
            "new_prompt": "Retarget it",
        },
    )

    assert response.status_code == 500
    assert "ffmpeg frame extraction failed" in response.json()["detail"]


@pytest.mark.asyncio
async def test_post_retarget_missing_new_prompt_returns_422(api_client, monkeypatch, tmp_path):
    download_dir, _, _ = _patch_dirs(monkeypatch, tmp_path)
    ref_video = download_dir / "reference.mp4"
    ref_video.write_text("video", encoding="utf-8")

    response = await api_client.post(
        "/api/retarget",
        json={"reference_video_path": str(ref_video)},
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_post_retarget_rejects_negative_frame_seconds(api_client, monkeypatch, tmp_path):
    download_dir, _, _ = _patch_dirs(monkeypatch, tmp_path)
    ref_video = download_dir / "reference.mp4"
    ref_video.write_text("video", encoding="utf-8")

    response = await api_client.post(
        "/api/retarget",
        json={
            "reference_video_path": str(ref_video),
            "new_prompt": "Retarget it",
            "frame_seconds": -0.1,
        },
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_post_retarget_returns_job_id_from_created_job(api_client, monkeypatch, tmp_path):
    download_dir, _, _ = _patch_dirs(monkeypatch, tmp_path)
    ref_video = download_dir / "reference.mp4"
    ref_video.write_text("video", encoding="utf-8")

    def fake_run(cmd, **kwargs):
        Path(cmd[-1]).write_text("frame", encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    captured = {}

    async def fake_create_job(job):
        captured["job"] = job
        return job

    monkeypatch.setattr(retarget.subprocess, "run", fake_run)
    monkeypatch.setattr(retarget, "create_job", fake_create_job)

    response = await api_client.post(
        "/api/retarget",
        json={
            "reference_video_path": str(ref_video),
            "new_prompt": "Retarget it",
        },
    )

    assert response.status_code == 200
    assert response.json()["job_id"] == captured["job"].id


@pytest.mark.asyncio
async def test_post_retarget_returns_502_when_job_submission_fails(api_client, monkeypatch, tmp_path):
    download_dir, _, _ = _patch_dirs(monkeypatch, tmp_path)
    ref_video = download_dir / "reference.mp4"
    ref_video.write_text("video", encoding="utf-8")

    def fake_run(cmd, **kwargs):
        Path(cmd[-1]).write_text("frame", encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    async def fake_create_job(job):
        raise RuntimeError("db unavailable")

    monkeypatch.setattr(retarget.subprocess, "run", fake_run)
    monkeypatch.setattr(retarget, "create_job", fake_create_job)

    response = await api_client.post(
        "/api/retarget",
        json={
            "reference_video_path": str(ref_video),
            "new_prompt": "Retarget it",
        },
    )

    assert response.status_code == 502
    assert "job submission failed" in response.json()["detail"]
