from unittest.mock import AsyncMock, MagicMock

import flow.operations.camera as camera_mod


def _client():
    client = MagicMock()
    client.page = MagicMock()
    client.page.url = "https://labs.google/fx/tools/flow/project/p/edit/parent-mid"
    client.profile_name = "profile-a"
    client._gen_id = None
    client._media_id_events = []
    client._record_media_id = lambda media_id, source="", url="": client._media_id_events.append(
        {"mid": media_id, "source": source, "url": url}
    )
    client.clear_captures = MagicMock()
    client.download_dir = "downloads"
    return client


def _patch_common(monkeypatch, *, tmp_path=None):
    saved_path = "cam_replay_api-mid.mp4"
    if tmp_path is not None:
        saved_path = str(tmp_path / saved_path)
    mocks = {
        "navigate_to_edit": AsyncMock(return_value=("edit-url", "project-id", "en")),
        "wait_for_video_loaded": AsyncMock(),
        "click_action_button": AsyncMock(return_value=True),
        "_click_preset": AsyncMock(return_value=True),
        "count_visible_cards": AsyncMock(return_value=0),
        "submit_with_confirmation": AsyncMock(return_value=True),
        "finalize_operation": AsyncMock(return_value={"ok": True, "media_id": "final-mid"}),
        "poll_status_via_api": AsyncMock(
            return_value={
                "api-mid": {
                    "status": "completed",
                    "media_id": "api-mid",
                    "media_url": "https://storage.googleapis.com/cam.mp4",
                }
            }
        ),
        "download_via_url": AsyncMock(return_value=saved_path),
    }
    for name, mock in mocks.items():
        monkeypatch.setattr(camera_mod, name, mock)
    monkeypatch.setattr(camera_mod.asyncio, "sleep", AsyncMock())
    return mocks


async def test_env_off_uses_ui_path_without_capture(monkeypatch):
    monkeypatch.setenv("FLOW_CAMERA_VIA_REVERSE", "0")
    client = _client()
    mocks = _patch_common(monkeypatch)
    install = MagicMock()
    get_template = MagicMock(return_value={"template": True})
    replay = AsyncMock(return_value="api-mid")
    monkeypatch.setattr(camera_mod, "install_camera_request_capture", install)
    monkeypatch.setattr(camera_mod, "get_camera_request_template", get_template)
    monkeypatch.setattr(camera_mod, "replay_camera_via_api", replay)

    result = await camera_mod.camera_move(
        client,
        {"media_id": "parent-mid", "edit_url": "edit-url", "job_level": 3},
        direction="Dolly in",
    )

    assert result == {"ok": True, "media_id": "final-mid"}
    install.assert_not_called()
    get_template.assert_not_called()
    replay.assert_not_awaited()
    mocks["submit_with_confirmation"].assert_awaited_once()
    mocks["poll_status_via_api"].assert_not_awaited()
    mocks["download_via_url"].assert_not_awaited()


async def test_env_on_l2_uses_ui_path_with_capture(monkeypatch):
    monkeypatch.setenv("FLOW_CAMERA_VIA_REVERSE", "1")
    client = _client()
    mocks = _patch_common(monkeypatch)
    install = MagicMock()
    get_template = MagicMock(return_value={"template": True})
    replay = AsyncMock(return_value="api-mid")
    monkeypatch.setattr(camera_mod, "install_camera_request_capture", install)
    monkeypatch.setattr(camera_mod, "get_camera_request_template", get_template)
    monkeypatch.setattr(camera_mod, "replay_camera_via_api", replay)

    await camera_mod.camera_move(
        client,
        {"media_id": "parent-mid", "edit_url": "edit-url", "job_level": 2},
        direction="Dolly in",
    )

    install.assert_called_once_with(client)
    get_template.assert_not_called()
    replay.assert_not_awaited()
    mocks["submit_with_confirmation"].assert_awaited_once()
    mocks["poll_status_via_api"].assert_not_awaited()


async def test_env_on_no_template_uses_ui_path_with_capture(monkeypatch):
    monkeypatch.setenv("FLOW_CAMERA_VIA_REVERSE", "1")
    client = _client()
    mocks = _patch_common(monkeypatch)
    install = MagicMock()
    get_template = MagicMock(return_value=None)
    replay = AsyncMock(return_value="api-mid")
    monkeypatch.setattr(camera_mod, "install_camera_request_capture", install)
    monkeypatch.setattr(camera_mod, "get_camera_request_template", get_template)
    monkeypatch.setattr(camera_mod, "replay_camera_via_api", replay)

    await camera_mod.camera_move(
        client,
        {"media_id": "parent-mid", "edit_url": "edit-url", "job_level": 3},
        direction="Dolly in",
    )

    install.assert_called_once_with(client)
    get_template.assert_called_once_with(client)
    replay.assert_not_awaited()
    mocks["submit_with_confirmation"].assert_awaited_once()
    mocks["poll_status_via_api"].assert_not_awaited()
    mocks["download_via_url"].assert_not_awaited()


async def test_env_on_template_replays_and_finalizes_via_status_api(monkeypatch, tmp_path):
    monkeypatch.setenv("FLOW_CAMERA_VIA_REVERSE", "1")
    client = _client()
    client.download_dir = str(tmp_path)
    saved_path = str(tmp_path / "cam_replay_api-mid.mp4")
    mocks = _patch_common(monkeypatch, tmp_path=tmp_path)
    mocks["download_via_url"].return_value = saved_path
    install = MagicMock()
    get_template = MagicMock(return_value={"template": True})
    replay = AsyncMock(return_value="api-mid")
    monkeypatch.setattr(camera_mod, "install_camera_request_capture", install)
    monkeypatch.setattr(camera_mod, "get_camera_request_template", get_template)
    monkeypatch.setattr(camera_mod, "replay_camera_via_api", replay)

    result = await camera_mod.camera_move(
        client,
        {
            "media_id": "parent-mid",
            "edit_url": "edit-url",
            "project_url": "project-url",
            "job_level": 3,
        },
        direction="Orbit left",
    )

    assert result == {
        "project_url": "project-url",
        "media_id": "api-mid",
        "edit_url": "https://labs.google/fx/en/tools/flow/project/project-id/edit/api-mid",
        "output_files": [saved_path],
        "generation_id": None,
        "profile": "profile-a",
    }
    replay.assert_awaited_once_with(
        client,
        parent_media_id="parent-mid",
        direction="Orbit left",
    )
    mocks["click_action_button"].assert_not_awaited()
    mocks["submit_with_confirmation"].assert_not_awaited()
    mocks["finalize_operation"].assert_not_awaited()
    mocks["poll_status_via_api"].assert_awaited_once()
    poll_call = mocks["poll_status_via_api"].await_args
    assert poll_call.kwargs.get("gen_ids") == ["api-mid"]
    assert poll_call.kwargs.get("project_id") == "project-id"
    mocks["download_via_url"].assert_awaited_once()
    dl_call = mocks["download_via_url"].await_args
    assert dl_call.kwargs.get("url") == "https://storage.googleapis.com/cam.mp4"
    out_path = dl_call.kwargs.get("out_path")
    assert isinstance(out_path, str) and out_path.endswith(".mp4")
    assert str(tmp_path) in out_path


async def test_env_on_replay_runtime_error_falls_back_to_ui(monkeypatch):
    monkeypatch.setenv("FLOW_CAMERA_VIA_REVERSE", "1")
    client = _client()
    mocks = _patch_common(monkeypatch)
    replay = AsyncMock(side_effect=RuntimeError("bad replay"))
    monkeypatch.setattr(camera_mod, "install_camera_request_capture", MagicMock())
    monkeypatch.setattr(
        camera_mod,
        "get_camera_request_template",
        MagicMock(return_value={"template": True}),
    )
    monkeypatch.setattr(camera_mod, "replay_camera_via_api", replay)

    result = await camera_mod.camera_move(
        client,
        {"media_id": "parent-mid", "edit_url": "edit-url", "job_level": 3},
        direction="Dolly in",
    )

    assert result == {"ok": True, "media_id": "final-mid"}
    replay.assert_awaited_once()
    mocks["submit_with_confirmation"].assert_awaited_once()
    mocks["finalize_operation"].assert_awaited_once()
    mocks["poll_status_via_api"].assert_not_awaited()
    mocks["download_via_url"].assert_not_awaited()


async def test_env_on_replay_status_failed_falls_back_to_ui(monkeypatch, tmp_path):
    monkeypatch.setenv("FLOW_CAMERA_VIA_REVERSE", "1")
    client = _client()
    client.download_dir = str(tmp_path)
    mocks = _patch_common(monkeypatch, tmp_path=tmp_path)
    mocks["poll_status_via_api"].return_value = {
        "api-mid": {"status": "failed", "media_id": "api-mid", "error": "backend_failure"}
    }
    monkeypatch.setattr(camera_mod, "install_camera_request_capture", MagicMock())
    monkeypatch.setattr(
        camera_mod,
        "get_camera_request_template",
        MagicMock(return_value={"template": True}),
    )
    monkeypatch.setattr(camera_mod, "replay_camera_via_api", AsyncMock(return_value="api-mid"))

    result = await camera_mod.camera_move(
        client,
        {"media_id": "parent-mid", "edit_url": "edit-url", "job_level": 3},
        direction="Dolly in",
    )

    assert result == {"ok": True, "media_id": "final-mid"}
    mocks["poll_status_via_api"].assert_awaited_once()
    mocks["download_via_url"].assert_not_awaited()
    mocks["submit_with_confirmation"].assert_awaited_once()
    mocks["finalize_operation"].assert_awaited_once()
