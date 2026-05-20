from unittest.mock import AsyncMock, MagicMock

import pytest

import flow.operations.frames_to_video as f2v_mod


PROJECT_ID = "11111111-1111-4111-8111-111111111111"
FINAL_MEDIA_ID = "22222222-2222-4222-8222-222222222222"
API_MEDIA_ID = "33333333-3333-4333-8333-333333333333"


def _image(tmp_path, name):
    path = tmp_path / name
    path.write_bytes(b"fake image bytes")
    return str(path)


def _client(tmp_path):
    page = MagicMock()
    page.url = f"https://labs.google/fx/tools/flow/project/{PROJECT_ID}"
    page.goto = AsyncMock()
    page.wait_for_selector = AsyncMock()
    page.wait_for_url = AsyncMock()

    client = MagicMock()
    client.page = page
    client.profile_name = "profile-a"
    client._gen_id = None
    client._media_id_events = []
    client._record_media_id = lambda media_id, source="", url="": client._media_id_events.append(
        {"mid": media_id, "source": source, "url": url}
    )
    client.clear_captures = MagicMock()
    client.download_dir = str(tmp_path)
    return client


def _expected_ui_result():
    return {
        "project_url": f"https://labs.google/fx/tools/flow/project/{PROJECT_ID}",
        "media_id": FINAL_MEDIA_ID,
        "edit_url": f"https://labs.google/fx/tools/flow/project/{PROJECT_ID}/edit/{FINAL_MEDIA_ID}",
        "output_files": ["ui-f2v.mp4"],
        "generation_id": None,
        "profile": "profile-a",
    }


def _patch_common(monkeypatch, tmp_path):
    saved_path = str(tmp_path / "f2v_replay.mp4")
    mocks = {
        "_dismiss_overlays": AsyncMock(),
        "_click_new_project": AsyncMock(),
        "_wait_for_composer": AsyncMock(),
        "_ensure_video_composer_mode": AsyncMock(),
        "_ensure_frames_mode": AsyncMock(),
        "_close_composer_menu": AsyncMock(),
        "_verify_frames_upload_affordances": AsyncMock(),
        "_upload_frame": AsyncMock(),
        "_type_prompt": AsyncMock(),
        "select_model": AsyncMock(),
        "_set_aspect_ratio": AsyncMock(),
        "_set_output_count": AsyncMock(),
        "_guard_l1_submit": AsyncMock(),
        "_count_visible_cards": AsyncMock(return_value=0),
        "submit_with_confirmation": AsyncMock(return_value=True),
        "wait_for_completion": AsyncMock(return_value={"done": True, "media_ids": [FINAL_MEDIA_ID]}),
        "resolve_final_media_id": AsyncMock(return_value=FINAL_MEDIA_ID),
        "download_video": AsyncMock(return_value=["ui-f2v.mp4"]),
        "poll_status_via_api": AsyncMock(
            return_value={
                API_MEDIA_ID: {
                    "status": "completed",
                    "media_id": API_MEDIA_ID,
                    "media_url": "https://storage.googleapis.com/f2v.mp4",
                }
            }
        ),
        "download_via_url": AsyncMock(return_value=saved_path),
        "message_with_failure_capture": AsyncMock(side_effect=lambda _client, _kind, message, **_kwargs: message),
    }
    for name, mock in mocks.items():
        monkeypatch.setattr(f2v_mod, name, mock)
    return mocks


async def test_default_env_uses_ui_path_without_capture(monkeypatch, tmp_path):
    monkeypatch.delenv("FLOW_F2V_VIA_REVERSE", raising=False)
    client = _client(tmp_path)
    start = _image(tmp_path, "start.png")
    end = _image(tmp_path, "end.png")
    mocks = _patch_common(monkeypatch, tmp_path)
    install = MagicMock()
    get_template = MagicMock(return_value={"template": True})
    replay = AsyncMock(return_value=API_MEDIA_ID)
    monkeypatch.setattr(f2v_mod, "install_f2v_request_capture", install)
    monkeypatch.setattr(f2v_mod, "get_f2v_request_template", get_template)
    monkeypatch.setattr(f2v_mod, "replay_f2v_via_inflate", replay)

    result = await f2v_mod.frames_to_video(client, "prompt", start, end)

    assert result == _expected_ui_result()
    install.assert_not_called()
    get_template.assert_not_called()
    replay.assert_not_awaited()
    mocks["submit_with_confirmation"].assert_awaited_once()
    mocks["wait_for_completion"].assert_awaited_once()
    mocks["download_video"].assert_awaited_once()
    mocks["poll_status_via_api"].assert_not_awaited()
    mocks["download_via_url"].assert_not_awaited()


async def test_env_on_no_template_uses_ui_path_with_capture(monkeypatch, tmp_path):
    monkeypatch.setenv("FLOW_F2V_VIA_REVERSE", "1")
    client = _client(tmp_path)
    start = _image(tmp_path, "start.png")
    end = _image(tmp_path, "end.png")
    mocks = _patch_common(monkeypatch, tmp_path)
    install = MagicMock()
    get_template = MagicMock(return_value=None)
    replay = AsyncMock(return_value=API_MEDIA_ID)
    monkeypatch.setattr(f2v_mod, "install_f2v_request_capture", install)
    monkeypatch.setattr(f2v_mod, "get_f2v_request_template", get_template)
    monkeypatch.setattr(f2v_mod, "replay_f2v_via_inflate", replay)

    result = await f2v_mod.frames_to_video(client, "prompt", start, end)

    assert result == _expected_ui_result()
    install.assert_called_once_with(client)
    get_template.assert_called_once_with(client)
    replay.assert_not_awaited()
    mocks["submit_with_confirmation"].assert_awaited_once()
    mocks["poll_status_via_api"].assert_not_awaited()


async def test_env_on_template_replays_and_finalizes_via_status_api(monkeypatch, tmp_path):
    monkeypatch.setenv("FLOW_F2V_VIA_REVERSE", "1")
    client = _client(tmp_path)
    start = _image(tmp_path, "start.png")
    end = _image(tmp_path, "end.png")
    saved_path = str(tmp_path / "f2v_replay.mp4")
    mocks = _patch_common(monkeypatch, tmp_path)
    install = MagicMock()
    get_template = MagicMock(return_value={"post_data": {"clientContext": {"projectId": PROJECT_ID}}})
    replay = AsyncMock(return_value=API_MEDIA_ID)
    monkeypatch.setattr(f2v_mod, "install_f2v_request_capture", install)
    monkeypatch.setattr(f2v_mod, "get_f2v_request_template", get_template)
    monkeypatch.setattr(f2v_mod, "replay_f2v_via_inflate", replay)

    result = await f2v_mod.frames_to_video(client, "prompt", start, end)

    assert result == {
        "project_url": f"https://labs.google/fx/tools/flow/project/{PROJECT_ID}",
        "media_id": API_MEDIA_ID,
        "edit_url": f"https://labs.google/fx/tools/flow/project/{PROJECT_ID}/edit/{API_MEDIA_ID}",
        "output_files": [saved_path],
        "generation_id": None,
        "profile": "profile-a",
    }
    replay.assert_awaited_once_with(client, "prompt", start, end)
    mocks["submit_with_confirmation"].assert_not_awaited()
    mocks["wait_for_completion"].assert_not_awaited()
    mocks["download_video"].assert_not_awaited()
    mocks["poll_status_via_api"].assert_awaited_once()
    assert mocks["poll_status_via_api"].await_args.kwargs["gen_ids"] == [API_MEDIA_ID]
    mocks["download_via_url"].assert_awaited_once()
    assert mocks["download_via_url"].await_args.kwargs["url"] == "https://storage.googleapis.com/f2v.mp4"
    assert {event["source"] for event in client._media_id_events} == {"f2v_replay"}


async def test_env_on_replay_error_falls_back_to_ui(monkeypatch, tmp_path):
    monkeypatch.setenv("FLOW_F2V_VIA_REVERSE", "1")
    client = _client(tmp_path)
    start = _image(tmp_path, "start.png")
    end = _image(tmp_path, "end.png")
    mocks = _patch_common(monkeypatch, tmp_path)
    monkeypatch.setattr(f2v_mod, "install_f2v_request_capture", MagicMock())
    monkeypatch.setattr(f2v_mod, "get_f2v_request_template", MagicMock(return_value={"template": True}))
    replay = AsyncMock(side_effect=RuntimeError("bad replay"))
    monkeypatch.setattr(f2v_mod, "replay_f2v_via_inflate", replay)

    result = await f2v_mod.frames_to_video(client, "prompt", start, end)

    assert result == _expected_ui_result()
    replay.assert_awaited_once()
    mocks["submit_with_confirmation"].assert_awaited_once()
    mocks["wait_for_completion"].assert_awaited_once()
    mocks["poll_status_via_api"].assert_not_awaited()
    mocks["download_via_url"].assert_not_awaited()


async def test_env_on_replay_status_failed_falls_back_to_ui(monkeypatch, tmp_path):
    monkeypatch.setenv("FLOW_F2V_VIA_REVERSE", "1")
    client = _client(tmp_path)
    start = _image(tmp_path, "start.png")
    end = _image(tmp_path, "end.png")
    mocks = _patch_common(monkeypatch, tmp_path)
    mocks["poll_status_via_api"].return_value = {
        API_MEDIA_ID: {
            "status": "failed",
            "media_id": API_MEDIA_ID,
            "media_url": None,
            "error": "backend_failure",
        }
    }
    monkeypatch.setattr(f2v_mod, "install_f2v_request_capture", MagicMock())
    monkeypatch.setattr(f2v_mod, "get_f2v_request_template", MagicMock(return_value={"template": True}))
    monkeypatch.setattr(f2v_mod, "replay_f2v_via_inflate", AsyncMock(return_value=API_MEDIA_ID))

    result = await f2v_mod.frames_to_video(client, "prompt", start, end)

    assert result == _expected_ui_result()
    mocks["poll_status_via_api"].assert_awaited_once()
    mocks["download_via_url"].assert_not_awaited()
    mocks["submit_with_confirmation"].assert_awaited_once()
    mocks["download_video"].assert_awaited_once()


async def test_env_on_missing_end_frame_skips_replay_and_uses_ui(monkeypatch, tmp_path):
    monkeypatch.setenv("FLOW_F2V_VIA_REVERSE", "1")
    client = _client(tmp_path)
    start = _image(tmp_path, "start.png")
    mocks = _patch_common(monkeypatch, tmp_path)
    install = MagicMock()
    get_template = MagicMock(return_value={"template": True})
    replay = AsyncMock(return_value=API_MEDIA_ID)
    monkeypatch.setattr(f2v_mod, "install_f2v_request_capture", install)
    monkeypatch.setattr(f2v_mod, "get_f2v_request_template", get_template)
    monkeypatch.setattr(f2v_mod, "replay_f2v_via_inflate", replay)

    result = await f2v_mod.frames_to_video(client, "prompt", start)

    assert result == _expected_ui_result()
    install.assert_called_once_with(client)
    get_template.assert_called_once_with(client)
    replay.assert_not_awaited()
    mocks["submit_with_confirmation"].assert_awaited_once()
    mocks["poll_status_via_api"].assert_not_awaited()
