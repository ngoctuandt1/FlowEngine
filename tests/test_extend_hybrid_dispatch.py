import json
from unittest.mock import AsyncMock, MagicMock

import pytest

import flow.operations.extend as extend_mod
import flow.operations.extend_api as extend_api
from flow.operations._base import L2ReverseApiPostAcceptError


EXTEND_URL = "https://aisandbox-pa.googleapis.com/v1/video:batchAsyncGenerateVideoExtendVideo"


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
    get_template = MagicMock(return_value={"headers": {"authorization": "Bearer tok"}})
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
    get_template = MagicMock(return_value={"headers": {"authorization": "Bearer tok"}})
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
        model="veo-3.1-lite",
        free_mode=True,
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


async def test_env_on_template_replays_paid_model_override_in_payload(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("FLOW_EXTEND_VIA_REVERSE", "1")
    client = _client()
    client.download_dir = str(tmp_path)
    _patch_common(monkeypatch, tmp_path=tmp_path)
    client.page.context.request.post = AsyncMock(
        return_value=_FakeAPIResponse(200, {"media": [{"name": "api-mid"}]})
    )
    client._extend_request_template = {
        "url": EXTEND_URL,
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
                        "videoExtendInput": {"sourceMedia": {"name": "parent-mid"}},
                        "textInput": {
                            "structuredPrompt": {
                                "parts": [{"text": "old prompt"}]
                            }
                        },
                    }
                ],
            }
        ),
    }

    async def _mint(_page, *, caller):
        assert caller == "replay_extend_via_api"
        return "fresh-token"

    monkeypatch.setattr(extend_api, "_mint_recaptcha_token", _mint)
    monkeypatch.setattr(extend_mod, "install_extend_request_capture", MagicMock())
    monkeypatch.setattr(
        extend_mod,
        "_finalize_replay_result",
        AsyncMock(return_value={"media_id": "api-mid"}),
    )

    await extend_mod.extend_video(
        client,
        {"media_id": "parent-mid", "edit_url": "edit-url", "job_level": 2},
        prompt="next",
        model="omni-flash",
        free_mode=False,
    )

    payload = json.loads(client.page.context.request.post.await_args.kwargs["data"])
    request = payload["requests"][0]
    assert request["videoModelKey"] == "omni-flash"
    assert request["videoExtendInput"]["sourceMedia"]["name"] == "parent-mid"


async def test_env_on_replay_runtime_error_falls_back_to_ui(monkeypatch):
    monkeypatch.setenv("FLOW_EXTEND_VIA_REVERSE", "1")
    client = _client()
    mocks = _patch_common(monkeypatch)
    monkeypatch.setattr(extend_mod, "install_extend_request_capture", MagicMock())
    monkeypatch.setattr(
        extend_mod,
        "get_extend_request_template",
        MagicMock(return_value={"headers": {"authorization": "Bearer tok"}}),
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


async def test_env_on_replay_status_failed_does_not_fall_back_to_ui(monkeypatch, tmp_path):
    """After reverse submit returns a media id, UI fallback would duplicate work."""
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
        MagicMock(return_value={"headers": {"authorization": "Bearer tok"}}),
    )
    monkeypatch.setattr(
        extend_mod,
        "replay_extend_via_api",
        AsyncMock(return_value={"media_id": "api-mid"}),
    )

    with pytest.raises(L2ReverseApiPostAcceptError) as exc_info:
        await extend_mod.extend_video(
            client,
            {"media_id": "parent-mid", "edit_url": "edit-url"},
            prompt="next",
        )

    assert exc_info.value.media_id == "api-mid"
    assert client._l2_reverse_api_inflight["api-mid"]["status"] == "post_accept_failed"
    mocks["poll_status_via_api"].assert_awaited_once()
    mocks["download_via_url"].assert_not_awaited()
    mocks["submit_with_confirmation"].assert_not_awaited()
    mocks["finalize_operation"].assert_not_awaited()


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
        MagicMock(return_value={"headers": {"authorization": "Bearer tok"}}),
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
