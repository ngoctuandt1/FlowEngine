import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from flow.operations import frames_api
from flow.operations import _video_l1_api_common as common
from flow.operations.frames_api import (
    clear_f2v_capture,
    get_f2v_request_template,
    install_f2v_request_capture,
    replay_f2v_via_inflate,
)


F2V_URL = "https://aisandbox-pa.googleapis.com/v1/video:batchAsyncGenerateVideoFromFrames"
GENERIC_URL = "https://aisandbox-pa.googleapis.com/v1/video:batchAsyncGenerate"
NON_F2V_URL = "https://aisandbox-pa.googleapis.com/v1/projects/proj1/operations/op1"


class FakePage:
    def __init__(self):
        self.listeners = {}
        self.url = "https://labs.google/fx/tools/flow/project/project-1"
        self.evaluate = AsyncMock(return_value="fresh-token")
        self.context = SimpleNamespace(request=SimpleNamespace(post=AsyncMock()))

    def on(self, event_name, callback):
        self.listeners.setdefault(event_name, []).append(callback)

    def remove_listener(self, event_name, callback):
        callbacks = self.listeners.get(event_name, [])
        self.listeners[event_name] = [item for item in callbacks if item is not callback]

    def fire_request(self, request):
        for callback in list(self.listeners.get("request", [])):
            callback(request)


class FakeRoutePage(FakePage):
    def __init__(self):
        super().__init__()
        self.routes = {}
        self.continue_kwargs = None

    async def route(self, pattern, callback):
        self.routes[pattern] = callback

    async def unroute(self, pattern, callback):
        if self.routes.get(pattern) is callback:
            del self.routes[pattern]


class FakeAPIResponse:
    def __init__(self, status, payload=None, text=""):
        self.status = status
        self._payload = payload if payload is not None else {}
        self._text = text

    async def json(self):
        return self._payload

    async def text(self):
        return self._text


def _client(page=None):
    return SimpleNamespace(page=page if page is not None else FakePage(), profile_name="p1")


def _body(start="media/start-old", end="media/end-old"):
    return {
        "clientContext": {
            "projectId": "project-1",
            "recaptchaContext": {"token": "old-token"},
        },
        "mediaGenerationContext": {"batchId": "old-batch"},
        "requests": [
            {
                "textInput": {"structuredPrompt": {"parts": [{"text": "old prompt"}]}},
                "videoFramesInput": {
                    "startFrame": {"media": {"name": start}},
                    "endFrame": {"media": {"name": end}},
                },
                "recaptchaContext": {"token": "old-request-token"},
                "seed": 123,
                "videoModelKey": "veo_3_1_t2v_lite_low_priority",
            }
        ],
    }


def _request(url=F2V_URL, method="POST", body=None, headers=None):
    return SimpleNamespace(
        url=url,
        method=method,
        headers=headers or {
            "authorization": "Bearer tok",
            "content-type": "text/plain;charset=UTF-8",
            "x-goog-api-key": "api-key",
            "cookie": "drop-me",
        },
        post_data=json.dumps(body if body is not None else _body()),
    )


def _client_with_template(body=None):
    client = _client()
    body = body if body is not None else _body()
    client._f2v_request_template = {
        "url": F2V_URL,
        "headers": _request().headers,
        "post_data": json.dumps(body),
        "anchors": frames_api.extract_frame_anchors(body),
    }
    return client


def _image_file(tmp_path, name="frame.png"):
    path = tmp_path / name
    path.write_bytes(b"fake image bytes")
    return str(path)


def _upload_response(name):
    return FakeAPIResponse(200, {"media": {"name": name}})


def test_install_f2v_request_capture_records_matching_post_template(caplog):
    client = _client()
    install_f2v_request_capture(client)

    with caplog.at_level("INFO"):
        client.page.fire_request(_request())

    template = client._f2v_request_template
    assert template["url"] == F2V_URL
    assert template["headers"]["authorization"] == "Bearer tok"
    assert template["anchors"]["start"] == "media/start-old"
    assert template["anchors"]["end"] == "media/end-old"
    assert "Captured Flow f2v reverseAPI template" in caplog.text


def test_install_f2v_request_capture_ignores_non_f2v_urls():
    client = _client()
    install_f2v_request_capture(client)

    client.page.fire_request(_request(url=NON_F2V_URL))
    client.page.fire_request(_request(url=F2V_URL, method="GET"))

    assert getattr(client, "_f2v_request_template", None) is None


def test_install_f2v_request_capture_replaces_previous_listener():
    page = FakePage()
    client = _client(page)
    install_f2v_request_capture(client)
    first = client._f2v_request_capture_listener
    install_f2v_request_capture(client)

    assert first not in page.listeners["request"]
    assert client._f2v_request_capture_listener in page.listeners["request"]
    assert len(page.listeners["request"]) == 1


def test_get_f2v_request_template_returns_none_then_dict_and_clear_resets():
    client = _client()
    assert get_f2v_request_template(client) is None
    client._f2v_request_template = {"url": F2V_URL}
    assert get_f2v_request_template(client) == {"url": F2V_URL}
    clear_f2v_capture(client)
    assert get_f2v_request_template(client) is None


def test_install_f2v_request_capture_falls_back_to_body_hints(caplog):
    client = _client()
    install_f2v_request_capture(client)

    with caplog.at_level("WARNING"):
        client.page.fire_request(_request(url=GENERIC_URL))

    assert client._f2v_request_template["url"] == GENERIC_URL
    assert "generic batchAsyncGenerate URL via frame body hints" in caplog.text


@pytest.mark.asyncio
async def test_replay_f2v_via_inflate_raises_when_no_template_captured(tmp_path):
    client = _client()
    start = _image_file(tmp_path, "start.png")
    end = _image_file(tmp_path, "end.png")

    with pytest.raises(RuntimeError, match="no captured template"):
        await replay_f2v_via_inflate(client, "new prompt", start, end)


@pytest.mark.asyncio
async def test_replay_f2v_via_inflate_uploads_frames_rewrites_body_and_recaptcha(tmp_path):
    client = _client_with_template()
    client.page.context.request.post.side_effect = [
        _upload_response("media/start-new"),
        _upload_response("media/end-new"),
        FakeAPIResponse(200, {"media": [{"name": "projects/project-1/media/media-new"}]}),
    ]
    start = _image_file(tmp_path, "start.png")
    end = _image_file(tmp_path, "end.png")

    media_id = await replay_f2v_via_inflate(client, "new prompt", start, end)

    assert media_id == "media-new"
    calls = client.page.context.request.post.await_args_list
    assert len(calls) == 3
    assert calls[0].args[0].endswith("/v1/flow/uploadImage")
    assert json.loads(calls[0].kwargs["data"])["clientContext"]["projectId"] == "project-1"
    assert calls[2].args[0] == F2V_URL
    body = json.loads(calls[2].kwargs["data"])
    request = body["requests"][0]
    assert request["textInput"]["structuredPrompt"]["parts"][0]["text"] == "new prompt"
    assert request["videoFramesInput"]["startFrame"]["media"]["name"] == "media/start-new"
    assert request["videoFramesInput"]["endFrame"]["media"]["name"] == "media/end-new"
    assert body["mediaGenerationContext"]["batchId"] != "old-batch"
    assert body["clientContext"]["recaptchaContext"]["token"] == "fresh-token"
    assert request["recaptchaContext"]["token"] == "fresh-token"
    assert "cookie" not in calls[2].kwargs["headers"]
    assert calls[2].kwargs["headers"]["x-recaptcha-token"] == "fresh-token"


@pytest.mark.asyncio
async def test_replay_f2v_via_inflate_piggybacks_ui_submit_when_route_available(
    tmp_path, monkeypatch
):
    page = FakeRoutePage()
    client = _client_with_template()
    client.page = page
    page.context.request.post.side_effect = [
        _upload_response("media/start-new"),
        _upload_response("media/end-new"),
    ]

    async def _fake_submit(client_arg, *, prompts, aspect_ratio="16:9", **kwargs):
        assert client_arg is client
        assert prompts == ["new prompt"]
        route_handler = page.routes["**/v1/video:batchAsyncGenerate**"]

        class Route:
            async def continue_(self, **route_kwargs):
                page.continue_kwargs = route_kwargs

        request = SimpleNamespace(
            url="https://aisandbox-pa.googleapis.com/v1/video:batchAsyncGenerateVideoText",
            method="POST",
            headers={"authorization": "Bearer fresh-ui"},
            post_data=json.dumps(
                {
                    "clientContext": {
                        "projectId": "throwaway-project",
                        "sessionId": "fresh-session",
                        "recaptchaContext": {"token": "fresh-ui-token"},
                    },
                    "requests": [{"recaptchaContext": {"token": "fresh-request-token"}}],
                }
            ),
        )
        await route_handler(Route(), request)
        return [{"gen_id": "gen-piggyback"}]

    monkeypatch.setattr(common, "submit_l1_batch_via_inflate", _fake_submit)

    media_id = await replay_f2v_via_inflate(
        client,
        "new prompt",
        _image_file(tmp_path, "start.png"),
        _image_file(tmp_path, "end.png"),
    )

    assert media_id == "gen-piggyback"
    assert page.continue_kwargs["url"] == F2V_URL
    body = json.loads(page.continue_kwargs["post_data"])
    assert body["clientContext"]["projectId"] == "project-1"
    assert body["clientContext"]["sessionId"] == "fresh-session"
    assert body["clientContext"]["recaptchaContext"]["token"] == "fresh-ui-token"
    assert body["requests"][0]["videoFramesInput"]["startFrame"]["media"]["name"] == "media/start-new"
    assert body["requests"][0]["videoFramesInput"]["endFrame"]["media"]["name"] == "media/end-new"
    assert page.routes == {}
    assert page.context.request.post.await_count == 2


@pytest.mark.asyncio
async def test_replay_f2v_via_inflate_raises_on_upload_4xx(tmp_path):
    client = _client_with_template()
    client.page.context.request.post.return_value = FakeAPIResponse(403, text="blocked")

    with pytest.raises(RuntimeError, match="uploadImage failed with HTTP 403: blocked"):
        await replay_f2v_via_inflate(
            client,
            "prompt",
            _image_file(tmp_path, "start.png"),
            _image_file(tmp_path, "end.png"),
        )


@pytest.mark.asyncio
async def test_replay_f2v_via_inflate_raises_on_generate_4xx(tmp_path):
    client = _client_with_template()
    client.page.context.request.post.side_effect = [
        _upload_response("media/start-new"),
        _upload_response("media/end-new"),
        FakeAPIResponse(403, text="recaptcha failed"),
    ]

    with pytest.raises(RuntimeError, match="HTTP 403: recaptcha failed"):
        await replay_f2v_via_inflate(
            client,
            "prompt",
            _image_file(tmp_path, "start.png"),
            _image_file(tmp_path, "end.png"),
        )


@pytest.mark.asyncio
async def test_replay_f2v_via_inflate_raises_on_zero_media_entries(tmp_path):
    client = _client_with_template()
    client.page.context.request.post.side_effect = [
        _upload_response("media/start-new"),
        _upload_response("media/end-new"),
        FakeAPIResponse(200, {"media": []}),
    ]

    with pytest.raises(RuntimeError, match="requested 1 media but got 0"):
        await replay_f2v_via_inflate(
            client,
            "prompt",
            _image_file(tmp_path, "start.png"),
            _image_file(tmp_path, "end.png"),
        )


@pytest.mark.asyncio
async def test_replay_f2v_via_inflate_empty_recaptcha_uses_captured_token(tmp_path, monkeypatch, caplog):
    async def _empty_token(page, *, caller):
        return ""

    monkeypatch.setattr(frames_api, "mint_recaptcha_token", _empty_token)
    client = _client_with_template()
    client.page.context.request.post.side_effect = [
        _upload_response("media/start-new"),
        _upload_response("media/end-new"),
        FakeAPIResponse(200, {"media": [{"name": "projects/project-1/media/media-new"}]}),
    ]

    with caplog.at_level("WARNING"):
        await replay_f2v_via_inflate(
            client,
            "prompt",
            _image_file(tmp_path, "start.png"),
            _image_file(tmp_path, "end.png"),
        )

    body = json.loads(client.page.context.request.post.await_args_list[2].kwargs["data"])
    headers = client.page.context.request.post.await_args_list[2].kwargs["headers"]
    assert body["clientContext"]["recaptchaContext"]["token"] == "old-token"
    assert headers.get("x-recaptcha-token") is None
    assert "reCAPTCHA mint returned empty token" in caplog.text
