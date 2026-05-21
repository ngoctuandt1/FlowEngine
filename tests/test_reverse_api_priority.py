from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from flow import characters, share
from flow.operations import camera, extend, insert, remove
from flow.operations._base import run_l2_reverse_api_first
from flow.reverse_api import reverse_api_preferred


PROJECT_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
PARENT_MEDIA_ID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
BBOX = {"x": 0.2, "y": 0.3, "w": 0.4, "h": 0.5}


class _Client:
    def __init__(self) -> None:
        self.page = SimpleNamespace(url=f"https://labs.google/fx/tools/flow/project/{PROJECT_ID}")
        self.profile_name = "profile-a"
        self._gen_id = "generation-id"
        self.clear_count = 0

    def clear_captures(self) -> None:
        self.clear_count += 1


def _job() -> dict:
    return {
        "type": "extend-video",
        "project_url": f"https://labs.google/fx/tools/flow/project/{PROJECT_ID}",
        "media_id": PARENT_MEDIA_ID,
        "job_level": 2,
    }


async def _noop_async(*_args, **_kwargs):
    return None


async def _true_async(*_args, **_kwargs):
    return True


async def _zero_async(*_args, **_kwargs):
    return 0


@pytest.fixture(autouse=True)
def _clean_reverse_env(monkeypatch):
    monkeypatch.delenv("FLOW_PREFER_REVERSE_API", raising=False)
    monkeypatch.delenv("FLOW_EXTEND_VIA_REVERSE", raising=False)
    monkeypatch.delenv("FLOW_INSERT_VIA_REVERSE", raising=False)
    monkeypatch.delenv("FLOW_REMOVE_VIA_REVERSE", raising=False)
    monkeypatch.delenv("FLOW_CAMERA_VIA_REVERSE", raising=False)
    monkeypatch.delenv("FLOW_REVERSE_API_TIMEOUT_SEC", raising=False)
    monkeypatch.delenv("FLOW_REVERSE_API_TIMEOUT_MS", raising=False)


@pytest.mark.parametrize(
    "module,runner,template_attr,replay_attr,finalize_attr,runner_kwargs,expected_media_id",
    [
        (
            extend,
            extend.extend_video,
            "_current_extend_template",
            "replay_extend_via_api",
            "_finalize_replay_result",
            {"prompt": "next scene"},
            "extend-media-id",
        ),
        (
            insert,
            insert.insert_object,
            "_current_insert_template",
            "replay_insert_via_api",
            "_finalize_insert_replay_result",
            {"prompt": "red kite", "bbox": BBOX},
            "insert-media-id",
        ),
        (
            remove,
            remove.remove_object,
            "_current_remove_template",
            "replay_remove_via_api",
            "_finalize_remove_replay_result",
            {"bbox": BBOX},
            "remove-media-id",
        ),
        (
            camera,
            camera.camera_move,
            "_current_camera_template",
            "replay_camera_via_api",
            "_finalize_camera_replay_result",
            {"direction": "Dolly in"},
            "camera-media-id",
        ),
    ],
)
@pytest.mark.asyncio
async def test_l2_hot_paths_try_reverse_api_before_ui(
    monkeypatch,
    module,
    runner,
    template_attr,
    replay_attr,
    finalize_attr,
    runner_kwargs,
    expected_media_id,
):
    client = _Client()
    replay_calls: list[dict] = []

    async def navigate_to_edit(*_args, **_kwargs):
        return (
            f"https://labs.google/fx/tools/flow/project/{PROJECT_ID}/edit/{PARENT_MEDIA_ID}",
            PROJECT_ID,
            "",
        )

    async def replay_via_api(*_args, **kwargs):
        replay_calls.append(kwargs)
        return expected_media_id

    async def finalize_replay(*_args, replay_media_id: str, **_kwargs):
        return {"source": "reverse", "media_id": replay_media_id}

    async def ui_click_forbidden(*_args, **_kwargs):
        raise AssertionError("UI click path should not run after reverse success")

    monkeypatch.setattr(module, "navigate_to_edit", navigate_to_edit)
    monkeypatch.setattr(module, "wait_for_video_loaded", _noop_async)
    monkeypatch.setattr(module, template_attr, lambda _client: {"url": "https://example.test/replay"})
    monkeypatch.setattr(module, replay_attr, replay_via_api)
    monkeypatch.setattr(module, finalize_attr, finalize_replay)
    monkeypatch.setattr(module, "click_action_button", ui_click_forbidden)

    result = await runner(client, _job(), **runner_kwargs)

    assert result == {"source": "reverse", "media_id": expected_media_id}
    assert replay_calls
    assert client.clear_count == 1


@pytest.mark.asyncio
async def test_recoverable_reverse_error_falls_back_to_ui_and_redacts_logs(
    monkeypatch,
    caplog,
):
    client = _Client()

    async def navigate_to_edit(*_args, **_kwargs):
        return (
            f"https://labs.google/fx/tools/flow/project/{PROJECT_ID}/edit/{PARENT_MEDIA_ID}",
            PROJECT_ID,
            "",
        )

    async def replay_failure(*_args, **_kwargs):
        raise RuntimeError("HTTP 503 Authorization=Bearer raw-token token=secret-cookie")

    async def finalize_ui(*_args, **_kwargs):
        return {"source": "ui", "media_id": "ui-media-id"}

    async def no_sleep(*_args, **_kwargs):
        return None

    monkeypatch.setattr(extend.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(extend, "navigate_to_edit", navigate_to_edit)
    monkeypatch.setattr(extend, "wait_for_video_loaded", _noop_async)
    monkeypatch.setattr(extend, "_current_extend_template", lambda _client: {"url": "https://example.test/replay"})
    monkeypatch.setattr(extend, "replay_extend_via_api", replay_failure)
    monkeypatch.setattr(extend, "_verify_extend_panel", _true_async)
    monkeypatch.setattr(extend, "select_model", _noop_async)
    monkeypatch.setattr(extend, "count_visible_cards", _zero_async)
    monkeypatch.setattr(extend, "submit_with_confirmation", _true_async)
    monkeypatch.setattr(extend, "finalize_operation", finalize_ui)

    caplog.set_level(logging.INFO)
    result = await extend.extend_video(client, _job(), prompt="next scene")

    assert result == {"source": "ui", "media_id": "ui-media-id"}
    assert "falling back to UI path" in caplog.text
    assert "raw-token" not in caplog.text
    assert "secret-cookie" not in caplog.text
    assert "Authorization=<redacted>" in caplog.text


@pytest.mark.asyncio
async def test_env_disabled_skips_reverse_attempt_and_uses_ui(monkeypatch):
    monkeypatch.setenv("FLOW_PREFER_REVERSE_API", "0")
    assert not reverse_api_preferred()
    client = _Client()
    installed = False

    async def navigate_to_edit(*_args, **_kwargs):
        return (
            f"https://labs.google/fx/tools/flow/project/{PROJECT_ID}/edit/{PARENT_MEDIA_ID}",
            PROJECT_ID,
            "",
        )

    async def replay_forbidden(*_args, **_kwargs):
        raise AssertionError("reverse API should not run when env disabled")

    async def finalize_ui(*_args, **_kwargs):
        return {"source": "ui", "media_id": "ui-media-id"}

    async def no_sleep(*_args, **_kwargs):
        return None

    def install_capture(_client):
        nonlocal installed
        installed = True
        return True

    monkeypatch.setattr(extend.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(extend, "_install_extend_capture_if_enabled", install_capture)
    monkeypatch.setattr(extend, "navigate_to_edit", navigate_to_edit)
    monkeypatch.setattr(extend, "wait_for_video_loaded", _noop_async)
    def current_template_forbidden(_client):
        raise AssertionError("template lookup should not run when env disabled")

    monkeypatch.setattr(extend, "_current_extend_template", current_template_forbidden)
    monkeypatch.setattr(extend, "replay_extend_via_api", replay_forbidden)
    monkeypatch.setattr(extend, "_verify_extend_panel", _true_async)
    monkeypatch.setattr(extend, "select_model", _noop_async)
    monkeypatch.setattr(extend, "count_visible_cards", _zero_async)
    monkeypatch.setattr(extend, "submit_with_confirmation", _true_async)
    monkeypatch.setattr(extend, "finalize_operation", finalize_ui)

    result = await extend.extend_video(client, _job(), prompt="next scene")

    assert result == {"source": "ui", "media_id": "ui-media-id"}
    assert installed is False


@pytest.mark.asyncio
async def test_fatal_validation_error_is_not_downgraded_to_recoverable():
    async def fatal_reverse_call():
        raise ValueError("validation failed: invalid argument")

    with pytest.raises(ValueError, match="validation failed"):
        await run_l2_reverse_api_first(
            operation="extend-video",
            call=fatal_reverse_call,
            available=True,
        )


@pytest.mark.asyncio
async def test_share_mint_skips_uncaptured_reverse_body_and_uses_ui(
    monkeypatch,
    caplog,
):
    async def click_first_visible(*_args, **_kwargs):
        return "selector"

    async def clipboard_text(_page):
        return ""

    class Modal:
        async def inner_text(self, *, timeout: int):
            return "https://labs.google/fx/tools/flow/project/proj/share/share-token-secret"

    monkeypatch.setattr(share, "_click_first_visible", click_first_visible)
    async def wait_for_share_modal(*_args, **_kwargs):
        return Modal()

    monkeypatch.setattr(share, "_wait_for_share_modal", wait_for_share_modal)
    monkeypatch.setattr(share, "_clipboard_text", clipboard_text)
    caplog.set_level(logging.INFO, logger="flow.share")

    result = await share.copy_flow_share_link(object())

    assert result.token == "share-token-secret"
    assert "reverse_api_unavailable" in caplog.text
    assert "share-token-secret" not in caplog.text


@pytest.mark.asyncio
async def test_character_create_skips_uncaptured_reverse_body_and_uses_ui(
    monkeypatch,
    caplog,
):
    client = SimpleNamespace(page=object())

    async def open_character_creator(*_args, **_kwargs):
        return "character-url"

    async def submit_character_create(*_args, **_kwargs):
        return "entity-id"

    monkeypatch.setattr(characters, "open_character_creator", open_character_creator)
    monkeypatch.setattr(characters, "fill_character_prompt", _noop_async)
    monkeypatch.setattr(characters, "select_character_model", _noop_async)
    monkeypatch.setattr(characters, "submit_character_create", submit_character_create)
    caplog.set_level(logging.INFO, logger="flow.characters")

    result = await characters.create_character_via_ui(
        client,
        project_id=PROJECT_ID,
        prompt="new hero",
    )

    assert result["entity_id"] == "entity-id"
    assert "reverse_api_unavailable" in caplog.text
    assert "flow/entities" in caplog.text
