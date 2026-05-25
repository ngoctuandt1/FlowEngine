import json
from unittest.mock import AsyncMock, MagicMock

import pytest

import flow.operations.insert as insert_mod
import flow.operations.insert_api as insert_api
from flow.operations._base import L2ReverseApiPostAcceptError

BBOX = {"x": 0.1, "y": 0.2, "w": 0.3, "h": 0.4}
INSERT_URL = "https://aisandbox-pa.googleapis.com/v1/video:batchAsyncGenerateVideoObjectInsertion"


class _FakeAPIResponse:
    def __init__(self, status, body=None, text=""):
        self.status = status
        self.status_code = status
        self._body = body or {}
        self._text = text

    async def json(self):
        return self._body

    async def text(self):
        return self._text


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
    saved_path = "ins_replay_api-mid.mp4"
    if tmp_path is not None:
        saved_path = str(tmp_path / saved_path)
    mocks = {
        "navigate_to_edit": AsyncMock(return_value=("edit-url", "project-id", "en")),
        "wait_for_video_loaded": AsyncMock(),
        "click_action_button": AsyncMock(return_value=True),
        "draw_bbox_on_video": AsyncMock(return_value=True),
        "_type_insert_prompt": AsyncMock(),
        "count_visible_cards": AsyncMock(return_value=0),
        "submit_with_confirmation": AsyncMock(return_value=True),
        "finalize_operation": AsyncMock(return_value={"ok": True, "media_id": "final-mid"}),
        "poll_status_via_api": AsyncMock(
            return_value={
                "api-mid": {
                    "status": "completed",
                    "media_id": "api-mid",
                    "media_url": "https://storage.googleapis.com/ins.mp4",
                }
            }
        ),
        "download_via_url": AsyncMock(return_value=saved_path),
    }
    for name, mock in mocks.items():
        monkeypatch.setattr(insert_mod, name, mock)
    monkeypatch.setattr(insert_mod.asyncio, "sleep", AsyncMock())
    return mocks


async def test_env_off_uses_ui_path_without_capture(monkeypatch):
    monkeypatch.setenv("FLOW_INSERT_VIA_REVERSE", "0")
    client = _client()
    mocks = _patch_common(monkeypatch)
    install = MagicMock()
    get_template = MagicMock(return_value={"headers": {"authorization": "Bearer tok"}})
    replay = AsyncMock(return_value="api-mid")
    monkeypatch.setattr(insert_mod, "install_insert_request_capture", install)
    monkeypatch.setattr(insert_mod, "get_insert_request_template", get_template)
    monkeypatch.setattr(insert_mod, "replay_insert_via_api", replay)

    result = await insert_mod.insert_object(
        client,
        {"media_id": "parent-mid", "edit_url": "edit-url", "job_level": 3},
        prompt="add kite",
        bbox=BBOX,
    )

    assert result == {"ok": True, "media_id": "final-mid"}
    install.assert_not_called()
    get_template.assert_not_called()
    replay.assert_not_awaited()
    mocks["draw_bbox_on_video"].assert_awaited_once_with(client.page, BBOX)
    mocks["submit_with_confirmation"].assert_awaited_once()
    mocks["poll_status_via_api"].assert_not_awaited()
    mocks["download_via_url"].assert_not_awaited()


async def test_env_on_l2_template_replays_with_capture(monkeypatch, tmp_path):
    monkeypatch.setenv("FLOW_INSERT_VIA_REVERSE", "1")
    client = _client()
    client.download_dir = str(tmp_path)
    saved_path = str(tmp_path / "ins_replay_api-mid.mp4")
    mocks = _patch_common(monkeypatch, tmp_path=tmp_path)
    mocks["download_via_url"].return_value = saved_path
    install = MagicMock()
    get_template = MagicMock(return_value={"headers": {"authorization": "Bearer tok"}})
    replay = AsyncMock(return_value="api-mid")
    monkeypatch.setattr(insert_mod, "install_insert_request_capture", install)
    monkeypatch.setattr(insert_mod, "get_insert_request_template", get_template)
    monkeypatch.setattr(insert_mod, "replay_insert_via_api", replay)

    result = await insert_mod.insert_object(
        client,
        {"media_id": "parent-mid", "edit_url": "edit-url", "job_level": 2},
        prompt="add kite",
        bbox=BBOX,
    )

    assert result["media_id"] == "api-mid"
    install.assert_called_once_with(client)
    get_template.assert_called_once_with(client)
    replay.assert_awaited_once()
    mocks["submit_with_confirmation"].assert_not_awaited()
    mocks["poll_status_via_api"].assert_awaited_once()


async def test_env_on_no_template_uses_ui_path_with_capture(monkeypatch):
    monkeypatch.setenv("FLOW_INSERT_VIA_REVERSE", "1")
    client = _client()
    mocks = _patch_common(monkeypatch)
    install = MagicMock()
    get_template = MagicMock(return_value=None)
    replay = AsyncMock(return_value="api-mid")
    monkeypatch.setattr(insert_mod, "install_insert_request_capture", install)
    monkeypatch.setattr(insert_mod, "get_insert_request_template", get_template)
    monkeypatch.setattr(insert_mod, "replay_insert_via_api", replay)

    await insert_mod.insert_object(
        client,
        {"media_id": "parent-mid", "edit_url": "edit-url", "job_level": 3},
        prompt="add kite",
        bbox=BBOX,
    )

    install.assert_called_once_with(client)
    get_template.assert_called_once_with(client)
    replay.assert_not_awaited()
    mocks["submit_with_confirmation"].assert_awaited_once()
    mocks["poll_status_via_api"].assert_not_awaited()
    mocks["download_via_url"].assert_not_awaited()


async def test_env_on_template_replays_job_prompt_bbox_and_status_download(monkeypatch, tmp_path):
    monkeypatch.setenv("FLOW_INSERT_VIA_REVERSE", "1")
    client = _client()
    client.download_dir = str(tmp_path)
    saved_path = str(tmp_path / "ins_replay_api-mid.mp4")
    mocks = _patch_common(monkeypatch, tmp_path=tmp_path)
    mocks["download_via_url"].return_value = saved_path
    install = MagicMock()
    get_template = MagicMock(return_value={"headers": {"authorization": "Bearer tok"}})
    replay = AsyncMock(return_value="api-mid")
    monkeypatch.setattr(insert_mod, "install_insert_request_capture", install)
    monkeypatch.setattr(insert_mod, "get_insert_request_template", get_template)
    monkeypatch.setattr(insert_mod, "replay_insert_via_api", replay)

    result = await insert_mod.insert_object(
        client,
        {
            "media_id": "parent-mid",
            "edit_url": "edit-url",
            "project_url": "project-url",
            "job_level": 3,
            "prompt": "add red kite",
            "bbox": BBOX,
        },
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
        prompt="add red kite",
        bbox=BBOX,
        model=None,
        free_mode=True,
    )
    mocks["click_action_button"].assert_not_awaited()
    mocks["draw_bbox_on_video"].assert_not_awaited()
    mocks["submit_with_confirmation"].assert_not_awaited()
    mocks["finalize_operation"].assert_not_awaited()
    mocks["poll_status_via_api"].assert_awaited_once()
    poll_call = mocks["poll_status_via_api"].await_args
    assert poll_call.kwargs.get("gen_ids") == ["api-mid"]
    assert poll_call.kwargs.get("project_id") == "project-id"
    mocks["download_via_url"].assert_awaited_once()
    dl_call = mocks["download_via_url"].await_args
    assert dl_call.kwargs.get("url") == "https://storage.googleapis.com/ins.mp4"
    out_path = dl_call.kwargs.get("out_path")
    assert isinstance(out_path, str) and out_path.endswith(".mp4")
    assert str(tmp_path) in out_path


async def test_env_on_template_replays_paid_model_override_in_payload(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("FLOW_INSERT_VIA_REVERSE", "1")
    client = _client()
    client.download_dir = str(tmp_path)
    _patch_common(monkeypatch, tmp_path=tmp_path)
    client.page.context.request.post = AsyncMock(
        return_value=_FakeAPIResponse(200, {"media": [{"name": "api-mid"}]})
    )
    client._insert_request_template = {
        "url": INSERT_URL,
        "headers": {
            "authorization": "Bearer tok",
            "content-type": "text/plain;charset=UTF-8",
        },
        "post_data": json.dumps(
            {
                "clientContext": {
                    "projectId": "project-id",
                    "recaptchaContext": {"token": "stale-token"},
                },
                "mediaGenerationContext": {"batchId": "old-batch"},
                "requests": [
                    {
                        "videoModelKey": "veo-3.1-lite",
                        "videoObjectInsertionInput": {
                            "sourceMedia": {"name": "parent-mid"},
                            "textInput": {
                                "structuredPrompt": {
                                    "parts": [{"text": "old prompt"}]
                                }
                            },
                            "bbox": {"x": 0.01, "y": 0.02, "w": 0.03, "h": 0.04},
                        },
                    }
                ],
            }
        ),
    }

    async def _mint(_page, *, caller):
        assert caller == "replay_insert_via_api"
        return "fresh-token"

    monkeypatch.setattr(insert_api, "_mint_recaptcha_token", _mint)
    monkeypatch.setattr(insert_mod, "install_insert_request_capture", MagicMock())
    monkeypatch.setattr(
        insert_mod,
        "_finalize_insert_replay_result",
        AsyncMock(return_value={"media_id": "api-mid"}),
    )

    await insert_mod.insert_object(
        client,
        {"media_id": "parent-mid", "edit_url": "edit-url", "job_level": 2},
        prompt="add kite",
        bbox=BBOX,
        model="omni-flash",
        free_mode=False,
    )

    payload = json.loads(client.page.context.request.post.await_args.kwargs["data"])
    request = payload["requests"][0]
    assert request["videoModelKey"] == "omni-flash"
    assert request["videoObjectInsertionInput"]["sourceMedia"]["name"] == "parent-mid"


async def test_env_on_replay_runtime_error_falls_back_to_ui(monkeypatch):
    monkeypatch.setenv("FLOW_INSERT_VIA_REVERSE", "1")
    client = _client()
    mocks = _patch_common(monkeypatch)
    replay = AsyncMock(side_effect=RuntimeError("bad replay"))
    monkeypatch.setattr(insert_mod, "install_insert_request_capture", MagicMock())
    monkeypatch.setattr(
        insert_mod,
        "get_insert_request_template",
        MagicMock(return_value={"headers": {"authorization": "Bearer tok"}}),
    )
    monkeypatch.setattr(insert_mod, "replay_insert_via_api", replay)

    result = await insert_mod.insert_object(
        client,
        {"media_id": "parent-mid", "edit_url": "edit-url", "job_level": 3},
        prompt="add kite",
        bbox=BBOX,
    )

    assert result == {"ok": True, "media_id": "final-mid"}
    replay.assert_awaited_once()
    mocks["submit_with_confirmation"].assert_awaited_once()
    mocks["finalize_operation"].assert_awaited_once()
    mocks["poll_status_via_api"].assert_not_awaited()
    mocks["download_via_url"].assert_not_awaited()


async def test_env_on_replay_status_failed_does_not_fall_back_to_ui(monkeypatch, tmp_path):
    monkeypatch.setenv("FLOW_INSERT_VIA_REVERSE", "1")
    client = _client()
    client.download_dir = str(tmp_path)
    mocks = _patch_common(monkeypatch, tmp_path=tmp_path)
    mocks["poll_status_via_api"].return_value = {
        "api-mid": {"status": "failed", "media_id": "api-mid", "error": "backend_failure"}
    }
    monkeypatch.setattr(insert_mod, "install_insert_request_capture", MagicMock())
    monkeypatch.setattr(
        insert_mod,
        "get_insert_request_template",
        MagicMock(return_value={"headers": {"authorization": "Bearer tok"}}),
    )
    monkeypatch.setattr(insert_mod, "replay_insert_via_api", AsyncMock(return_value="api-mid"))

    with pytest.raises(L2ReverseApiPostAcceptError) as exc_info:
        await insert_mod.insert_object(
            client,
            {"media_id": "parent-mid", "edit_url": "edit-url", "job_level": 3},
            prompt="add kite",
            bbox=BBOX,
        )

    assert exc_info.value.media_id == "api-mid"
    assert client._l2_reverse_api_inflight["api-mid"]["status"] == "post_accept_failed"
    mocks["poll_status_via_api"].assert_awaited_once()
    mocks["download_via_url"].assert_not_awaited()
    mocks["submit_with_confirmation"].assert_not_awaited()
    mocks["finalize_operation"].assert_not_awaited()
