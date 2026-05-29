import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

import flow.operations.generate as generate_mod


PROJECT_ID = "12345678-1234-1234-1234-123456789012"
PROJECT_URL = f"https://labs.google/fx/tools/flow/project/{PROJECT_ID}"


class FakeLocator:
    def __init__(self, page):
        self.page = page
        self.first = self

    def filter(self, **_kwargs):
        return self

    def locator(self, *_args, **_kwargs):
        return self

    async def is_visible(self, *_args, **_kwargs):
        return True

    async def scroll_into_view_if_needed(self, *_args, **_kwargs):
        return None

    async def click(self, *_args, **_kwargs):
        self.page.url = PROJECT_URL


class FakePage:
    def __init__(self, url=PROJECT_URL):
        self.url = url
        self.mouse = MagicMock()
        self.mouse.click = AsyncMock()
        self.goto = AsyncMock(side_effect=self._goto)
        self.wait_for_selector = AsyncMock(return_value=None)
        self.wait_for_url = AsyncMock(side_effect=self._wait_for_url)

    async def _goto(self, url, *_args, **_kwargs):
        self.url = url

    async def _wait_for_url(self, *_args, **_kwargs):
        self.url = PROJECT_URL

    def get_by_role(self, *_args, **_kwargs):
        return FakeLocator(self)

    def get_by_text(self, *_args, **_kwargs):
        return FakeLocator(self)

    def locator(self, *_args, **_kwargs):
        return FakeLocator(self)


def _client(url=PROJECT_URL):
    client = MagicMock()
    client.page = FakePage(url)
    client.profile_name = "profile-a"
    client._gen_id = None
    client._media_id_events = []
    client._record_media_id = lambda media_id, source="", url="": client._media_id_events.append(
        {"mid": media_id, "source": source, "url": url}
    )
    client.clear_captures = MagicMock()
    client.download_dir = "downloads"
    return client


def _patch_ui_path(monkeypatch):
    mocks = {
        "dismiss_flow_marketing_landing": AsyncMock(),
        "recover_from_flow_canvas_page": AsyncMock(),
        "_dismiss_overlays": AsyncMock(),
        "_wait_for_composer": AsyncMock(),
        "_ensure_video_composer_mode": AsyncMock(),
        "select_model": AsyncMock(),
        "_set_aspect_ratio": AsyncMock(),
        "_set_output_count": AsyncMock(),
        "_type_prompt": AsyncMock(),
        "_guard_l1_submit": AsyncMock(),
        "_count_visible_cards": AsyncMock(return_value=0),
        # 2026-05 single agent composer path: configure Agent settings then
        # submit via submit_l1_prompt (replaces the composer-chip + the
        # submit_with_confirmation UI path).
        "ensure_agent_settings": AsyncMock(return_value=True),
        "submit_l1_prompt": AsyncMock(return_value=True),
        "submit_with_confirmation": AsyncMock(return_value=True),
        "wait_for_completion": AsyncMock(
            return_value={"done": True, "media_ids": ["ui-mid"]}
        ),
        "resolve_final_media_id": AsyncMock(return_value="ui-mid"),
        "download_video": AsyncMock(return_value=["ui.mp4"]),
        "message_with_failure_capture": AsyncMock(side_effect=lambda _c, _k, msg: msg),
        "poll_status_via_api": AsyncMock(
            return_value={
                "gen-1": {
                    "status": "completed",
                    "media_id": "api-mid",
                    "media_url": "https://storage.googleapis.com/t2v.mp4",
                }
            }
        ),
        "download_via_url": AsyncMock(return_value="api.mp4"),
    }
    monkeypatch.setattr(generate_mod, "is_login_page", lambda _url: False)
    for name, mock in mocks.items():
        monkeypatch.setattr(generate_mod, name, mock)
    return mocks


async def test_env_off_uses_ui_path_without_capture(monkeypatch):
    monkeypatch.setenv("FLOW_T2V_VIA_REVERSE", "0")
    client = _client()
    mocks = _patch_ui_path(monkeypatch)
    install = MagicMock()
    get_template = MagicMock(return_value={"template": True})
    replay = AsyncMock(return_value=["gen-1"])
    monkeypatch.setattr(generate_mod, "install_t2v_request_capture", install)
    monkeypatch.setattr(generate_mod, "get_t2v_request_template", get_template)
    monkeypatch.setattr(generate_mod, "replay_t2v_via_inflate", replay)

    result = await generate_mod.text_to_video(client, "a robot")

    assert result["media_id"] == "ui-mid"
    assert result["output_files"] == ["ui.mp4"]
    install.assert_not_called()
    get_template.assert_not_called()
    replay.assert_not_awaited()
    mocks["submit_l1_prompt"].assert_awaited_once()


async def test_env_on_module_unavailable_logs_and_uses_ui(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger=generate_mod.__name__)
    monkeypatch.setenv("FLOW_T2V_VIA_REVERSE", "1")
    client = _client()
    mocks = _patch_ui_path(monkeypatch)
    monkeypatch.setattr(generate_mod, "install_t2v_request_capture", None)
    monkeypatch.setattr(generate_mod, "get_t2v_request_template", None)
    monkeypatch.setattr(generate_mod, "replay_t2v_via_inflate", None)
    monkeypatch.setattr(generate_mod, "_T2V_API_IMPORT_ERROR", RuntimeError("missing"))

    result = await generate_mod.text_to_video(client, "a robot")

    assert result["media_id"] == "ui-mid"
    assert "generate_api unavailable" in caplog.text
    mocks["submit_l1_prompt"].assert_awaited_once()


async def test_env_on_capture_install_failure_uses_ui(monkeypatch):
    monkeypatch.setenv("FLOW_T2V_VIA_REVERSE", "1")
    client = _client()
    mocks = _patch_ui_path(monkeypatch)
    install = MagicMock(side_effect=RuntimeError("route failed"))
    replay = AsyncMock(return_value=["gen-1"])
    monkeypatch.setattr(generate_mod, "install_t2v_request_capture", install)
    monkeypatch.setattr(generate_mod, "get_t2v_request_template", MagicMock(return_value={"template": True}))
    monkeypatch.setattr(generate_mod, "replay_t2v_via_inflate", replay)

    result = await generate_mod.text_to_video(client, "a robot")

    assert result["media_id"] == "ui-mid"
    install.assert_called_once_with(client)
    replay.assert_not_awaited()
    mocks["submit_l1_prompt"].assert_awaited_once()


async def test_env_on_no_template_uses_ui_after_capture(monkeypatch):
    monkeypatch.setenv("FLOW_T2V_VIA_REVERSE", "1")
    client = _client()
    mocks = _patch_ui_path(monkeypatch)
    install = MagicMock()
    get_template = MagicMock(return_value=None)
    replay = AsyncMock(return_value=["gen-1"])
    monkeypatch.setattr(generate_mod, "install_t2v_request_capture", install)
    monkeypatch.setattr(generate_mod, "get_t2v_request_template", get_template)
    monkeypatch.setattr(generate_mod, "replay_t2v_via_inflate", replay)

    result = await generate_mod.text_to_video(client, "a robot")

    assert result["media_id"] == "ui-mid"
    install.assert_called_once_with(client)
    get_template.assert_called_once_with(client)
    replay.assert_not_awaited()
    mocks["submit_l1_prompt"].assert_awaited_once()


async def test_env_on_template_replay_success_skips_ui_submit(monkeypatch):
    monkeypatch.setenv("FLOW_T2V_VIA_REVERSE", "1")
    client = _client()
    mocks = _patch_ui_path(monkeypatch)
    replay = AsyncMock(return_value=["gen-1"])
    monkeypatch.setattr(generate_mod, "install_t2v_request_capture", MagicMock())
    monkeypatch.setattr(
        generate_mod,
        "get_t2v_request_template",
        MagicMock(return_value={"url": "captured"}),
    )
    monkeypatch.setattr(generate_mod, "replay_t2v_via_inflate", replay)

    result = await generate_mod.text_to_video(client, "a robot")

    assert result == {
        "project_url": PROJECT_URL,
        "media_id": "api-mid",
        "edit_url": f"{PROJECT_URL}/edit/api-mid",
        "output_files": ["api.mp4"],
        "generation_id": None,
        "profile": "profile-a",
    }
    replay.assert_awaited_once_with(client, ["a robot"])
    mocks["submit_l1_prompt"].assert_not_awaited()
    mocks["wait_for_completion"].assert_not_awaited()
    mocks["download_video"].assert_not_awaited()
    mocks["poll_status_via_api"].assert_awaited_once()
    mocks["download_via_url"].assert_awaited_once()


async def test_env_on_replay_failure_falls_back_to_ui(monkeypatch):
    monkeypatch.setenv("FLOW_T2V_VIA_REVERSE", "1")
    client = _client()
    mocks = _patch_ui_path(monkeypatch)
    replay = AsyncMock(side_effect=RuntimeError("recaptcha wall"))
    monkeypatch.setattr(generate_mod, "install_t2v_request_capture", MagicMock())
    monkeypatch.setattr(
        generate_mod,
        "get_t2v_request_template",
        MagicMock(return_value={"url": "captured"}),
    )
    monkeypatch.setattr(generate_mod, "replay_t2v_via_inflate", replay)

    result = await generate_mod.text_to_video(client, "a robot")

    assert result["media_id"] == "ui-mid"
    replay.assert_awaited_once_with(client, ["a robot"])
    mocks["submit_l1_prompt"].assert_awaited_once()
    mocks["poll_status_via_api"].assert_not_awaited()
    mocks["download_via_url"].assert_not_awaited()
