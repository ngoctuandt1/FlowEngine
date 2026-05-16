import asyncio
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
    return client


def _patch_common(monkeypatch):
    mocks = {
        "navigate_to_edit": AsyncMock(return_value=("edit-url", "project-id", "en")),
        "wait_for_video_loaded": AsyncMock(),
        "_verify_extend_panel": AsyncMock(return_value=True),
        "_type_extend_prompt": AsyncMock(),
        "select_model": AsyncMock(),
        "count_visible_cards": AsyncMock(return_value=0),
        "submit_with_confirmation": AsyncMock(return_value=True),
        "finalize_operation": AsyncMock(return_value={"ok": True, "media_id": "final-mid"}),
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


async def test_env_on_template_replays_and_skips_ui_submit(monkeypatch):
    monkeypatch.setenv("FLOW_EXTEND_VIA_REVERSE", "1")
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
    replay.assert_awaited_once_with(
        client,
        parent_media_id="parent-mid",
        prompt="next",
    )
    mocks["_verify_extend_panel"].assert_not_awaited()
    mocks["submit_with_confirmation"].assert_not_awaited()
    mocks["finalize_operation"].assert_awaited_once()


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


async def test_replay_media_id_is_deferred_for_finalize_wait(monkeypatch):
    monkeypatch.setenv("FLOW_EXTEND_VIA_REVERSE", "1")
    client = _client()
    _patch_common(monkeypatch)
    finalize = AsyncMock(return_value={"ok": True, "media_id": "api-mid"})
    monkeypatch.setattr(extend_mod, "finalize_operation", finalize)
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

    await extend_mod.extend_video(
        client,
        {"media_id": "parent-mid", "edit_url": "edit-url"},
        prompt="next",
    )
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    finalize.assert_awaited_once()
    assert client._extend_replay_media_ids == ["api-mid"]
    assert {event.get("mid") for event in client._media_id_events} == {"api-mid"}
