import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

import flow.operations.image as image_mod


PROJECT_ID = "12345678-1234-1234-1234-123456789012"
PROJECT_URL = f"https://labs.google/fx/tools/flow/project/{PROJECT_ID}"


class FakePage:
    def __init__(self):
        self.url = "https://labs.google/fx/tools/flow"
        self.goto = AsyncMock(side_effect=self._goto)
        self.wait_for_selector = AsyncMock(return_value=None)
        self.wait_for_url = AsyncMock(side_effect=self._wait_for_url)

    async def _goto(self, url, *_args, **_kwargs):
        self.url = url

    async def _wait_for_url(self, *_args, **_kwargs):
        self.url = PROJECT_URL


def _client():
    client = MagicMock()
    client.page = FakePage()
    client.profile_name = "profile-a"
    client._gen_id = None
    client._image_names = []
    client.clear_captures = MagicMock()
    client.download_dir = "downloads"
    return client


def _patch_ui_path(monkeypatch):
    async def click_project(page):
        page.url = PROJECT_URL

    mocks = {
        "_dismiss_overlays": AsyncMock(),
        "_click_new_project": AsyncMock(side_effect=click_project),
        "_wait_for_composer": AsyncMock(),
        "_switch_to_image_output": AsyncMock(),
        "_close_composer_menu": AsyncMock(),
        "_upload_reference_image": AsyncMock(),
        "_type_prompt": AsyncMock(),
        "_select_image_model": AsyncMock(),
        "_set_image_aspect_ratio": AsyncMock(),
        "_set_image_output_count": AsyncMock(),
        "_count_visible_cards": AsyncMock(return_value=0),
        "submit_with_confirmation": AsyncMock(return_value=True),
        "wait_for_completion": AsyncMock(
            return_value={"done": True, "media_ids": ["ui-mid"]}
        ),
        "resolve_final_media_id": AsyncMock(return_value="ui-mid"),
        "download_video": AsyncMock(return_value=["ui.png"]),
        "download_via_url": AsyncMock(return_value="api.png"),
        "_resolve_image_input_path": MagicMock(side_effect=lambda path, label: path),
    }
    monkeypatch.setattr(image_mod, "is_login_page", lambda _url: False)
    for name, mock in mocks.items():
        monkeypatch.setattr(image_mod, name, mock)
    return mocks


async def test_env_off_uses_ui_path_without_capture(monkeypatch):
    monkeypatch.setenv("FLOW_T2I_VIA_REVERSE", "0")
    client = _client()
    mocks = _patch_ui_path(monkeypatch)
    install = MagicMock()
    get_template = MagicMock(return_value={"template": True})
    replay = AsyncMock(return_value=["api-mid"])
    monkeypatch.setattr(image_mod, "install_image_request_capture", install)
    monkeypatch.setattr(image_mod, "get_image_request_template", get_template)
    monkeypatch.setattr(image_mod, "replay_image_generate", replay)

    result = await image_mod.text_to_image(client, "a cat")

    assert result["media_id"] == "ui-mid"
    assert result["output_files"] == ["ui.png"]
    install.assert_not_called()
    get_template.assert_not_called()
    replay.assert_not_awaited()
    mocks["submit_with_confirmation"].assert_awaited_once()


async def test_env_on_module_unavailable_logs_and_uses_ui(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger=image_mod.__name__)
    monkeypatch.setenv("FLOW_T2I_VIA_REVERSE", "1")
    client = _client()
    mocks = _patch_ui_path(monkeypatch)
    monkeypatch.setattr(image_mod, "install_image_request_capture", None)
    monkeypatch.setattr(image_mod, "get_image_request_template", None)
    monkeypatch.setattr(image_mod, "replay_image_generate", None)
    monkeypatch.setattr(image_mod, "_IMAGE_API_IMPORT_ERROR", RuntimeError("missing"))

    result = await image_mod.text_to_image(client, "a cat")

    assert result["media_id"] == "ui-mid"
    assert "image_api unavailable" in caplog.text
    mocks["submit_with_confirmation"].assert_awaited_once()


async def test_env_on_no_template_uses_ui_after_capture(monkeypatch):
    monkeypatch.setenv("FLOW_T2I_VIA_REVERSE", "1")
    client = _client()
    mocks = _patch_ui_path(monkeypatch)
    install = MagicMock()
    get_template = MagicMock(return_value=None)
    replay = AsyncMock(return_value=["api-mid"])
    monkeypatch.setattr(image_mod, "install_image_request_capture", install)
    monkeypatch.setattr(image_mod, "get_image_request_template", get_template)
    monkeypatch.setattr(image_mod, "replay_image_generate", replay)

    result = await image_mod.text_to_image(client, "a cat")

    assert result["media_id"] == "ui-mid"
    install.assert_called_once_with(client)
    get_template.assert_called_once_with(client)
    replay.assert_not_awaited()
    mocks["submit_with_confirmation"].assert_awaited_once()


async def test_env_on_template_replay_success_skips_ui_submit(monkeypatch):
    monkeypatch.setenv("FLOW_T2I_VIA_REVERSE", "1")
    client = _client()
    mocks = _patch_ui_path(monkeypatch)
    replay = AsyncMock(return_value=["api-mid"])
    monkeypatch.setattr(image_mod, "install_image_request_capture", MagicMock())
    monkeypatch.setattr(
        image_mod,
        "get_image_request_template",
        MagicMock(return_value={"url": "captured"}),
    )
    monkeypatch.setattr(image_mod, "replay_image_generate", replay)

    result = await image_mod.text_to_image(client, "a cat")

    assert result == {
        "project_url": PROJECT_URL,
        "media_id": "api-mid",
        "edit_url": f"{PROJECT_URL}/edit/api-mid",
        "output_files": ["api.png"],
        "generation_id": None,
        "profile": "profile-a",
    }
    replay.assert_awaited_once_with(client, "a cat", count=1)
    mocks["submit_with_confirmation"].assert_not_awaited()
    mocks["wait_for_completion"].assert_not_awaited()
    mocks["download_video"].assert_not_awaited()
    mocks["download_via_url"].assert_awaited_once()


async def test_env_on_replay_failure_falls_back_to_ui(monkeypatch):
    monkeypatch.setenv("FLOW_T2I_VIA_REVERSE", "1")
    client = _client()
    mocks = _patch_ui_path(monkeypatch)
    replay = AsyncMock(side_effect=RuntimeError("bad replay"))
    monkeypatch.setattr(image_mod, "install_image_request_capture", MagicMock())
    monkeypatch.setattr(
        image_mod,
        "get_image_request_template",
        MagicMock(return_value={"url": "captured"}),
    )
    monkeypatch.setattr(image_mod, "replay_image_generate", replay)

    result = await image_mod.text_to_image(client, "a cat")

    assert result["media_id"] == "ui-mid"
    replay.assert_awaited_once_with(client, "a cat", count=1)
    mocks["submit_with_confirmation"].assert_awaited_once()
    mocks["download_via_url"].assert_not_awaited()


async def test_i2i_uploads_reference_before_replay(monkeypatch):
    monkeypatch.setenv("FLOW_T2I_VIA_REVERSE", "1")
    client = _client()
    mocks = _patch_ui_path(monkeypatch)
    replay = AsyncMock(return_value=["api-mid"])
    monkeypatch.setattr(image_mod, "install_image_request_capture", MagicMock())
    monkeypatch.setattr(
        image_mod,
        "get_image_request_template",
        MagicMock(return_value={"url": "captured"}),
    )
    monkeypatch.setattr(image_mod, "replay_image_generate", replay)

    result = await image_mod.text_to_image(client, "a cat", ref_image_path="ref.png")

    assert result["media_id"] == "api-mid"
    mocks["_resolve_image_input_path"].assert_called_once_with("ref.png", label="Reference")
    mocks["_upload_reference_image"].assert_awaited_once_with(client.page, "ref.png")
    replay.assert_awaited_once_with(client, "a cat", count=1)
    mocks["submit_with_confirmation"].assert_not_awaited()
