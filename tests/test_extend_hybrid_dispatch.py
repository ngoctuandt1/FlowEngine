from unittest.mock import AsyncMock, MagicMock

import pytest

import flow.operations.extend as extend_mod


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
    # _replay_download_dir consults client.download_dir; MagicMock returns
    # another MagicMock by default which fails the isinstance check, so
    # provide a concrete string to keep tests deterministic across CI.
    client.download_dir = "downloads"
    return client


def _patch_common(monkeypatch, *, tmp_path=None):
    """Patch the side-effect entry points used by extend_video.

    ``finalize_operation`` is mocked so we can prove which branch the
    extend pipeline took without exercising any browser code. The
    reverse-API replay branch uses ``poll_status_via_api`` +
    ``download_via_url`` instead of ``download_video`` (the legacy UI
    path) so we mock both new entry points here too.
    """
    saved_path = "ext_replay_api-mid.mp4"
    if tmp_path is not None:
        saved_path = str(tmp_path / saved_path)
    mocks = {
        "navigate_to_edit": AsyncMock(return_value=("edit-url", "project-id", "en")),
        "wait_for_video_loaded": AsyncMock(),
        "_verify_extend_panel": AsyncMock(return_value=True),
        "_type_extend_prompt": AsyncMock(),
        "select_model": AsyncMock(),
        "count_visible_cards": AsyncMock(return_value=0),
        "submit_with_confirmation": AsyncMock(return_value=True),
        "finalize_operation": AsyncMock(return_value={"ok": True, "media_id": "final-mid"}),
        "poll_status_via_api": AsyncMock(
            return_value={
                "api-mid": {
                    "status": "completed",
                    "media_id": "api-mid",
                    "media_url": "https://storage.googleapis.com/x.mp4",
                }
            }
        ),
        "download_via_url": AsyncMock(return_value=saved_path),
    }
    for name, mock in mocks.items():
        monkeypatch.setattr(extend_mod, name, mock)
    return mocks


async def test_env_off_uses_ui_path_without_capture(monkeypatch):
    monkeypatch.setenv("FLOW_EXTEND_VIA_REVERSE", "0")
    client = _client()
    mocks = _patch_common(monkeypatch)
    install = MagicMock()
    get_template = MagicMock(return_value={"template": True})
    replay = AsyncMock(return_value={"media_id": "api-mid"})
    monkeypatch.setattr(extend_mod, "install_extend_request_capture", install)
    monkeypatch.setattr(extend_mod, "get_extend_request_template", get_template)
    monkeypatch.setattr(extend_mod, "replay_extend_via_api", replay)

    result = await extend_mod.extend_video(
        client,
        {"media_id": "parent-mid", "edit_url": "edit-url"},
        prompt="next",
    )

    assert result == {"ok": True, "media_id": "final-mid"}
    install.assert_not_called()
    get_template.assert_not_called()
    replay.assert_not_awaited()
    mocks["submit_with_confirmation"].assert_awaited_once()
    mocks["poll_status_via_api"].assert_not_awaited()
    mocks["download_via_url"].assert_not_awaited()


async def test_env_on_no_template_uses_ui_path_with_capture(monkeypatch):
    monkeypatch.setenv("FLOW_EXTEND_VIA_REVERSE", "1")
    client = _client()
    mocks = _patch_common(monkeypatch)
    install = MagicMock()
    get_template = MagicMock(return_value=None)
    replay = AsyncMock(return_value={"media_id": "api-mid"})
    monkeypatch.setattr(extend_mod, "install_extend_request_capture", install)
    monkeypatch.setattr(extend_mod, "get_extend_request_template", get_template)
    monkeypatch.setattr(extend_mod, "replay_extend_via_api", replay)

    await extend_mod.extend_video(
        client,
        {"media_id": "parent-mid", "edit_url": "edit-url"},
        prompt="next",
    )

    install.assert_called_once_with(client)
    get_template.assert_called_once_with(client)
    replay.assert_not_awaited()
    mocks["submit_with_confirmation"].assert_awaited_once()
    mocks["poll_status_via_api"].assert_not_awaited()
    mocks["download_via_url"].assert_not_awaited()


async def test_env_on_template_replays_and_finalizes_via_status_api(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("FLOW_EXTEND_VIA_REVERSE", "1")
    client = _client()
    client.download_dir = str(tmp_path)
    saved_path = str(tmp_path / "ext_replay_api-mid.mp4")
    mocks = _patch_common(monkeypatch, tmp_path=tmp_path)
    mocks["download_via_url"].return_value = saved_path

    install = MagicMock()
    get_template = MagicMock(return_value={"template": True})
    replay = AsyncMock(return_value={"media_id": "api-mid"})
    create_task = MagicMock()
    monkeypatch.setattr(extend_mod, "install_extend_request_capture", install)
    monkeypatch.setattr(extend_mod, "get_extend_request_template", get_template)
    monkeypatch.setattr(extend_mod, "replay_extend_via_api", replay)
    monkeypatch.setattr(extend_mod.asyncio, "create_task", create_task, raising=False)

    result = await extend_mod.extend_video(
        client,
        {
            "media_id": "parent-mid",
            "edit_url": "edit-url",
            "project_url": "project-url",
        },
        prompt="next",
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
        prompt="next",
    )
    mocks["_verify_extend_panel"].assert_not_awaited()
    mocks["submit_with_confirmation"].assert_not_awaited()
    mocks["finalize_operation"].assert_not_awaited()

    # Status poll path used instead of wait_for_completion + download_video.
    mocks["poll_status_via_api"].assert_awaited_once()
    poll_call = mocks["poll_status_via_api"].await_args
    assert poll_call.kwargs.get("gen_ids") == ["api-mid"]
    assert poll_call.kwargs.get("project_id") == "project-id"
    assert poll_call.args[0] is client

    # Direct-URL download path used instead of UI tile click.
    mocks["download_via_url"].assert_awaited_once()
    dl_call = mocks["download_via_url"].await_args
    assert dl_call.args[0] is client
    assert dl_call.kwargs.get("url") == "https://storage.googleapis.com/x.mp4"
    out_path = dl_call.kwargs.get("out_path")
    assert isinstance(out_path, str) and out_path.endswith(".mp4")
    assert str(tmp_path) in out_path

    create_task.assert_not_called()


async def test_env_on_replay_runtime_error_falls_back_to_ui(monkeypatch):
    monkeypatch.setenv("FLOW_EXTEND_VIA_REVERSE", "1")
    client = _client()
    mocks = _patch_common(monkeypatch)
    monkeypatch.setattr(extend_mod, "install_extend_request_capture", MagicMock())
    monkeypatch.setattr(
        extend_mod,
        "get_extend_request_template",
        MagicMock(return_value={"template": True}),
    )
    replay = AsyncMock(side_effect=RuntimeError("bad replay"))
    monkeypatch.setattr(extend_mod, "replay_extend_via_api", replay)

    result = await extend_mod.extend_video(
        client,
        {"media_id": "parent-mid", "edit_url": "edit-url"},
        prompt="next",
    )

    assert result == {"ok": True, "media_id": "final-mid"}
    replay.assert_awaited_once()
    mocks["submit_with_confirmation"].assert_awaited_once()
    mocks["finalize_operation"].assert_awaited_once()
    # No status-API call once replay fails before yielding a media id.
    mocks["poll_status_via_api"].assert_not_awaited()
    mocks["download_via_url"].assert_not_awaited()


async def test_env_on_replay_status_failed_falls_back_to_ui(monkeypatch, tmp_path):
    """A replay that submits OK but whose status API reports failure must
    fall through to the UI path, not raise upward."""
    monkeypatch.setenv("FLOW_EXTEND_VIA_REVERSE", "1")
    client = _client()
    client.download_dir = str(tmp_path)
    mocks = _patch_common(monkeypatch, tmp_path=tmp_path)
    mocks["poll_status_via_api"].return_value = {
        "api-mid": {
            "status": "failed",
            "media_id": "api-mid",
            "media_url": None,
            "error": "backend_failure",
        }
    }
    monkeypatch.setattr(extend_mod, "install_extend_request_capture", MagicMock())
    monkeypatch.setattr(
        extend_mod,
        "get_extend_request_template",
        MagicMock(return_value={"template": True}),
    )
    monkeypatch.setattr(
        extend_mod,
        "replay_extend_via_api",
        AsyncMock(return_value={"media_id": "api-mid"}),
    )

    result = await extend_mod.extend_video(
        client,
        {"media_id": "parent-mid", "edit_url": "edit-url"},
        prompt="next",
    )

    # Fallback path produced the UI-finalize result.
    assert result == {"ok": True, "media_id": "final-mid"}
    mocks["poll_status_via_api"].assert_awaited_once()
    mocks["download_via_url"].assert_not_awaited()
    mocks["submit_with_confirmation"].assert_awaited_once()
    mocks["finalize_operation"].assert_awaited_once()


async def test_replay_media_id_is_recorded_synchronously_without_wait(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("FLOW_EXTEND_VIA_REVERSE", "1")
    client = _client()
    client.download_dir = str(tmp_path)
    saved_path = str(tmp_path / "ext_replay_api-mid.mp4")
    mocks = _patch_common(monkeypatch, tmp_path=tmp_path)
    mocks["download_via_url"].return_value = saved_path
    create_task = MagicMock()
    monkeypatch.setattr(extend_mod, "install_extend_request_capture", MagicMock())
    monkeypatch.setattr(
        extend_mod,
        "get_extend_request_template",
        MagicMock(return_value={"template": True}),
    )
    monkeypatch.setattr(
        extend_mod,
        "replay_extend_via_api",
        AsyncMock(return_value={"media_id": "api-mid"}),
    )
    monkeypatch.setattr(extend_mod.asyncio, "create_task", create_task, raising=False)

    result = await extend_mod.extend_video(
        client,
        {"media_id": "parent-mid", "edit_url": "edit-url"},
        prompt="next",
    )

    assert result["media_id"] == "api-mid"
    assert result["output_files"] == [saved_path]
    mocks["finalize_operation"].assert_not_awaited()
    mocks["poll_status_via_api"].assert_awaited_once()
    mocks["download_via_url"].assert_awaited_once()
    create_task.assert_not_called()
    assert "_extend_replay_media_ids" not in client.__dict__
    assert {event.get("mid") for event in client._media_id_events} == {"api-mid"}
    assert {event.get("source") for event in client._media_id_events} == {"extend_replay"}
