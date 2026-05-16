import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from flow.operations import ingredients_api
from flow.operations.ingredients_api import (
    clear_i2v_capture,
    get_i2v_request_template,
    install_i2v_request_capture,
    replay_i2v_via_inflate,
)


I2V_URL = "https://aisandbox-pa.googleapis.com/v1/video:batchAsyncGenerateVideoFromIngredients"
GENERIC_URL = "https://aisandbox-pa.googleapis.com/v1/video:batchAsyncGenerate"
NON_I2V_URL = "https://aisandbox-pa.googleapis.com/v1/projects/proj1/operations/op1"


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


def _body(refs=None):
    refs = refs or ["media/ingredient-old-1", "media/ingredient-old-2"]
    return {
        "clientContext": {
            "projectId": "project-1",
            "recaptchaContext": {"token": "old-token"},
        },
        "mediaGenerationContext": {"batchId": "old-batch"},
        "requests": [
            {
                "textInput": {"structuredPrompt": {"parts": [{"text": "old prompt"}]}},
                "videoIngredientsInput": {
                    "ingredients": [
                        {"imageResource": {"name": ref}, "weight": 1}
                        for ref in refs
                    ]
                },
                "recaptchaContext": {"token": "old-request-token"},
                "seed": 123,
                "videoModelKey": "veo_3_1_t2v_lite_low_priority",
            }
        ],
    }


def _request(url=I2V_URL, method="POST", body=None, headers=None):
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
    client._i2v_request_template = {
        "url": I2V_URL,
        "headers": _request().headers,
        "post_data": json.dumps(body),
        "anchors": ingredients_api.extract_ingredient_anchors(body),
    }
    return client


def _image_file(tmp_path, name="ingredient.png"):
    path = tmp_path / name
    path.write_bytes(b"fake image bytes")
    return str(path)


def _upload_response(name):
    return FakeAPIResponse(200, {"media": {"name": name}})


def test_install_i2v_request_capture_records_matching_post_template(caplog):
    client = _client()
    install_i2v_request_capture(client)

    with caplog.at_level("INFO"):
        client.page.fire_request(_request())

    template = client._i2v_request_template
    assert template["url"] == I2V_URL
    assert template["headers"]["authorization"] == "Bearer tok"
    assert template["anchors"]["ingredients"] == [
        "media/ingredient-old-1",
        "media/ingredient-old-2",
    ]
    assert "Captured Flow i2v reverseAPI template" in caplog.text


def test_install_i2v_request_capture_ignores_non_i2v_urls():
    client = _client()
    install_i2v_request_capture(client)

    client.page.fire_request(_request(url=NON_I2V_URL))
    client.page.fire_request(_request(url=I2V_URL, method="GET"))

    assert getattr(client, "_i2v_request_template", None) is None


def test_install_i2v_request_capture_replaces_previous_listener():
    page = FakePage()
    client = _client(page)
    install_i2v_request_capture(client)
    first = client._i2v_request_capture_listener
    install_i2v_request_capture(client)

    assert first not in page.listeners["request"]
    assert client._i2v_request_capture_listener in page.listeners["request"]
    assert len(page.listeners["request"]) == 1


def test_get_i2v_request_template_returns_none_then_dict_and_clear_resets():
    client = _client()
    assert get_i2v_request_template(client) is None
    client._i2v_request_template = {"url": I2V_URL}
    assert get_i2v_request_template(client) == {"url": I2V_URL}
    clear_i2v_capture(client)
    assert get_i2v_request_template(client) is None


def test_install_i2v_request_capture_falls_back_to_body_hints(caplog):
    client = _client()
    install_i2v_request_capture(client)

    with caplog.at_level("WARNING"):
        client.page.fire_request(_request(url=GENERIC_URL))

    assert client._i2v_request_template["url"] == GENERIC_URL
    assert "generic batchAsyncGenerate URL via ingredient body hints" in caplog.text


@pytest.mark.asyncio
async def test_replay_i2v_via_inflate_raises_when_no_template_captured(tmp_path):
    client = _client()
    with pytest.raises(RuntimeError, match="no captured template"):
        await replay_i2v_via_inflate(client, "new prompt", [_image_file(tmp_path)])


@pytest.mark.asyncio
async def test_replay_i2v_via_inflate_raises_when_no_ingredients():
    client = _client_with_template()
    with pytest.raises(RuntimeError, match="at least one ingredient"):
        await replay_i2v_via_inflate(client, "new prompt", [])


@pytest.mark.asyncio
async def test_replay_i2v_via_inflate_uploads_ingredients_rewrites_list_and_recaptcha(tmp_path):
    client = _client_with_template()
    client.page.context.request.post.side_effect = [
        _upload_response("media/ingredient-new-1"),
        _upload_response("media/ingredient-new-2"),
        _upload_response("media/ingredient-new-3"),
        FakeAPIResponse(200, {"media": [{"name": "projects/project-1/media/media-new"}]}),
    ]
    ingredients = [
        _image_file(tmp_path, "a.png"),
        _image_file(tmp_path, "b.png"),
        _image_file(tmp_path, "c.png"),
    ]

    media_id = await replay_i2v_via_inflate(client, "new prompt", ingredients)

    assert media_id == "media-new"
    calls = client.page.context.request.post.await_args_list
    assert len(calls) == 4
    assert all(call.args[0].endswith("/v1/flow/uploadImage") for call in calls[:3])
    assert calls[3].args[0] == I2V_URL
    body = json.loads(calls[3].kwargs["data"])
    request = body["requests"][0]
    rewritten = request["videoIngredientsInput"]["ingredients"]
    assert [item["imageResource"]["name"] for item in rewritten] == [
        "media/ingredient-new-1",
        "media/ingredient-new-2",
        "media/ingredient-new-3",
    ]
    assert request["textInput"]["structuredPrompt"]["parts"][0]["text"] == "new prompt"
    assert body["mediaGenerationContext"]["batchId"] != "old-batch"
    assert body["clientContext"]["recaptchaContext"]["token"] == "fresh-token"
    assert request["recaptchaContext"]["token"] == "fresh-token"
    assert "cookie" not in calls[3].kwargs["headers"]
    assert calls[3].kwargs["headers"]["x-recaptcha-token"] == "fresh-token"


@pytest.mark.asyncio
async def test_replay_i2v_via_inflate_raises_on_upload_4xx(tmp_path):
    client = _client_with_template()
    client.page.context.request.post.return_value = FakeAPIResponse(403, text="blocked")

    with pytest.raises(RuntimeError, match="uploadImage failed with HTTP 403: blocked"):
        await replay_i2v_via_inflate(client, "prompt", [_image_file(tmp_path)])


@pytest.mark.asyncio
async def test_replay_i2v_via_inflate_raises_on_generate_4xx(tmp_path):
    client = _client_with_template(_body(["media/ingredient-old-1"]))
    client.page.context.request.post.side_effect = [
        _upload_response("media/ingredient-new-1"),
        FakeAPIResponse(403, text="recaptcha failed"),
    ]

    with pytest.raises(RuntimeError, match="HTTP 403: recaptcha failed"):
        await replay_i2v_via_inflate(client, "prompt", [_image_file(tmp_path)])


@pytest.mark.asyncio
async def test_replay_i2v_via_inflate_raises_on_zero_media_entries(tmp_path):
    client = _client_with_template(_body(["media/ingredient-old-1"]))
    client.page.context.request.post.side_effect = [
        _upload_response("media/ingredient-new-1"),
        FakeAPIResponse(200, {"media": []}),
    ]

    with pytest.raises(RuntimeError, match="requested 1 media but got 0"):
        await replay_i2v_via_inflate(client, "prompt", [_image_file(tmp_path)])


@pytest.mark.asyncio
async def test_replay_i2v_via_inflate_empty_recaptcha_uses_captured_token(tmp_path, monkeypatch, caplog):
    async def _empty_token(page, *, caller):
        return ""

    monkeypatch.setattr(ingredients_api, "mint_recaptcha_token", _empty_token)
    client = _client_with_template(_body(["media/ingredient-old-1"]))
    client.page.context.request.post.side_effect = [
        _upload_response("media/ingredient-new-1"),
        FakeAPIResponse(200, {"media": [{"name": "projects/project-1/media/media-new"}]}),
    ]

    with caplog.at_level("WARNING"):
        await replay_i2v_via_inflate(client, "prompt", [_image_file(tmp_path)])

    body = json.loads(client.page.context.request.post.await_args_list[1].kwargs["data"])
    headers = client.page.context.request.post.await_args_list[1].kwargs["headers"]
    assert body["clientContext"]["recaptchaContext"]["token"] == "old-token"
    assert headers.get("x-recaptcha-token") is None
    assert "reCAPTCHA mint returned empty token" in caplog.text
