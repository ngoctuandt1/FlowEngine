import asyncio
from pathlib import Path
from subprocess import CompletedProcess

import pytest


@pytest.mark.asyncio
async def test_render_endpoint_202_on_post(api_client, monkeypatch):
    import server.routes.render as render_route

    async def fake_process_render_job(render_id, payload):
        return None

    monkeypatch.setattr(render_route, "_process_render_job", fake_process_render_job)
    response = await api_client.post(
        "/api/render/timeline",
        json={
            "ratio": "9:16",
            "tracks": [
                {
                    "kind": "video",
                    "clips": [
                        {
                            "asset_id": "clip-1",
                            "start_sec": 0.0,
                            "duration_sec": 1.0,
                        }
                    ],
                }
            ],
            "total_duration_sec": 1.0,
        },
    )

    assert response.status_code == 202
    assert response.json()["render_id"]


@pytest.mark.asyncio
async def test_render_endpoint_404_on_unknown_id(api_client):
    response = await api_client.get("/api/render/missing-render")

    assert response.status_code == 404


def test_render_compose_runs_ffmpeg(monkeypatch, tmp_path):
    import server.services.render_compose as render_compose
    from server.models.render import TimelinePayload

    download_dir = (tmp_path / "downloads").resolve()
    upload_dir = (tmp_path / "uploads").resolve()
    output_path = download_dir / "renders" / "final.mp4"
    source_path = download_dir / "clip.mp4"

    download_dir.mkdir(parents=True)
    upload_dir.mkdir(parents=True)
    source_path.write_bytes(b"clip")

    monkeypatch.setattr(render_compose, "DOWNLOAD_DIR", download_dir, raising=False)
    monkeypatch.setattr(render_compose, "UPLOAD_DIR", upload_dir, raising=False)

    commands = []

    def fake_run(command, **kwargs):
        commands.append(command)
        if str(output_path) in command:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"mp4")
        return CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(render_compose.subprocess, "run", fake_run)

    payload = TimelinePayload.model_validate(
        {
            "ratio": "16:9",
            "tracks": [
                {
                    "kind": "video",
                    "clips": [
                        {
                            "asset_id": "downloads/clip.mp4",
                            "start_sec": 0.0,
                            "duration_sec": 1.0,
                            "trim_in": 0.0,
                        }
                    ],
                }
            ],
            "total_duration_sec": 1.0,
        }
    )

    render_compose.compose_timeline(payload, output_path)

    assert any(
        command[0] == "ffmpeg" and "-movflags" in command and "+faststart" in command
        for command in commands
    )


@pytest.mark.asyncio
async def test_render_endpoint_rejects_too_many_tracks(api_client):
    response = await api_client.post(
        "/api/render/timeline",
        json={
            "ratio": "9:16",
            "tracks": [
                {"kind": "video", "clips": [{"asset_id": f"c{i}", "start_sec": 0.0, "duration_sec": 1.0}]}
                for i in range(11)
            ],
            "total_duration_sec": 1.0,
        },
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_render_endpoint_rejects_excessive_duration(api_client):
    response = await api_client.post(
        "/api/render/timeline",
        json={
            "ratio": "9:16",
            "tracks": [
                {"kind": "video", "clips": [{"asset_id": "c1", "start_sec": 0.0, "duration_sec": 1.0}]}
            ],
            "total_duration_sec": 601.0,
        },
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_render_endpoint_rejects_too_many_clips_per_track(api_client):
    response = await api_client.post(
        "/api/render/timeline",
        json={
            "ratio": "9:16",
            "tracks": [
                {
                    "kind": "video",
                    "clips": [
                        {"asset_id": f"c{i}", "start_sec": 0.0, "duration_sec": 0.5}
                        for i in range(101)
                    ],
                }
            ],
            "total_duration_sec": 60.0,
        },
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_render_endpoint_concurrency_cap_returns_429(api_client, monkeypatch):
    import server.routes.render as render_route

    monkeypatch.setenv("FLOW_RENDER_CONCURRENT_MAX", "1")

    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_process(render_id, payload):
        started.set()
        await release.wait()

    monkeypatch.setattr(render_route, "_process_render_job", slow_process)
    # Reset the module-level counter in case prior tests left it nonzero.
    render_route._active_renders = 0

    body = {
        "ratio": "9:16",
        "tracks": [
            {"kind": "video", "clips": [{"asset_id": "c1", "start_sec": 0.0, "duration_sec": 1.0}]}
        ],
        "total_duration_sec": 1.0,
    }
    # Starlette runs BackgroundTasks BEFORE returning the response, so a
    # `slow_process` that waits on `release.set()` would deadlock the first
    # POST. Run the first POST in a background task, wait for `started` to
    # fire, then issue the second POST and release.
    first_task = asyncio.create_task(api_client.post("/api/render/timeline", json=body))
    try:
        await asyncio.wait_for(started.wait(), timeout=5.0)
        second = await api_client.post("/api/render/timeline", json=body)
        assert second.status_code == 429
        assert "concurrency" in second.json()["detail"].lower()
    finally:
        release.set()
        first = await first_task
        render_route._active_renders = 0

    assert first.status_code == 202
