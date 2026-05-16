import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from flow.operations import extend_api
from flow.operations.extend_api import (
    _extract_extend_media_names,
    clear_extend_capture,
    get_extend_request_template,
    install_extend_request_capture,
    replay_extend_via_api,
)


EXTEND_URL = "https://aisandbox-pa.googleapis.com/v1/video:batchAsyncGenerateVideoExtendVideo"
NON_EXTEND_URL = "https://aisandbox-pa.googleapis.com/v1/projects/proj1/operations/op1"


class FakePage:
    def __init__(self):
        self.listeners = {}
        self.context = SimpleNamespace(request=SimpleNamespace(post=AsyncMock()))
        self.evaluate = AsyncMock(return_value="fresh-recaptcha-token")

    def on(self, event_name, callback):
        self.listeners.setdefault(event_name, []).append(callback)

    def remove_listener(self, event_name, callback):
        callbacks = self.listeners.get(event_name, [])
        self.listeners[event_name] = [item for item in callbacks if item is not callback]

    def fire_request(self, request):
        for callback in list(self.listeners.get("request", [])):
            callback(request)


class FakeRequest:
    url = EXTEND_URL
    method = "POST"
    headers = {
        "authorization": "Bearer tok",
        "content-type": "text/plain;charset=UTF-8",
    }
    post_data = '{"requests": []}'


class FakeAPIResponse:
    def __init__(self, status, body=None, text=""):
        self.status = status
        self.status_code = status
        self._body = body or {}
        self._text = text

    async def json(self):
        return self._body

    async def text(self):
        return self._text


def _client(page=None):
    return SimpleNamespace(page=page or FakePage(), profile_name="test-profile")


def _template_body():
    return {
        "clientContext": {
            "projectId": "proj1",
            "recaptchaContext": {"token": "stale-root-token"},
            "tool": "PINHOLE",
        },
        "mediaGenerationContext": {"batchId": "old-batch"},
        "requests": [
            {
                "videoExtendInput": {"sourceMedia": {"name": "old-parent"}},
                "textInput": {
                    "structuredPrompt": {"parts": [{"text": "old prompt"}]}
                },
                "recaptchaContext": {"token": "stale-request-token"},
                "clientContext": {"recaptchaContext": {"token": "stale-item-token"}},
            },
            {
                "videoExtendInput": {"sourceMedia": {"name": "second-parent"}},
                "textInput": {
                    "structuredPrompt": {"parts": [{"text": "second prompt"}]}
                },
            },
        ],
    }


def _client_with_template():
    client = _client()
    client._extend_request_template = {
        "url": EXTEND_URL,
        "headers": {
            "authorization": "Bearer tok",
            "content-type": "text/plain;charset=UTF-8",
            "cookie": "stale-cookie=1",
            "origin": "https://labs.google",
            "referer": "https://labs.google/fx/tools/flow",
            "host": "aisandbox-pa.googleapis.com",
            "x-goog-api-key": "api-key",
            "x-recaptcha-token": "stale-header-token",
        },
        "post_data": json.dumps(_template_body()),
    }
    return client


@pytest.fixture(autouse=True)
def _mock_recaptcha(monkeypatch):
    async def _mint(_page, *, caller):
        assert caller == "replay_extend_via_api"
        return "fresh-recaptcha-token"

    monkeypatch.setattr(extend_api, "_mint_recaptcha_token", _mint)


def test_install_extend_request_capture_records_latest_matching_post_template(caplog):
    page = FakePage()
    client = _client(page)
    install_extend_request_capture(client)

    first = SimpleNamespace(
        url=EXTEND_URL,
        method="POST",
        headers={"authorization": "Bearer first"},
        post_data='{"first": true}',
    )
    latest = SimpleNamespace(
        url=EXTEND_URL.upper(),
        method="POST",
        headers={"authorization": "Bearer latest"},
        post_data='{"latest": true}',
    )
    with caplog.at_level("INFO"):
        page.fire_request(first)
        page.fire_request(latest)

    assert client._extend_request_template == {
        "url": EXTEND_URL.upper(),
        "headers": {"authorization": "Bearer latest"},
        "post_data": '{"latest": true}',
    }
    assert "Captured Flow extend reverseAPI template" in caplog.text


def test_install_extend_request_capture_ignores_non_extend_urls():
    page = FakePage()
    client = _client(page)
    install_extend_request_capture(client)

    page.fire_request(
        SimpleNamespace(url=NON_EXTEND_URL, method="POST", headers={}, post_data="{}")
    )

    assert get_extend_request_template(client) is None


def test_install_extend_request_capture_replaces_previous_listener():
    page = FakePage()
    client = _client(page)
    install_extend_request_capture(client)
    first_listener = page.listeners["request"][0]

    install_extend_request_capture(client)


    assert len(page.listeners["request"]) == 1
    assert page.listeners["request"][0] is not first_listener


def test_get_extend_request_template_returns_none_then_dict_and_clear_resets():
    client = _client()

    assert get_extend_request_template(client) is None

    client._extend_request_template = {"url": EXTEND_URL, "headers": {}, "post_data": "{}"}
    assert get_extend_request_template(client) == client._extend_request_template

    clear_extend_capture(client)
    assert get_extend_request_template(client) is None


@pytest.mark.asyncio
async def test_replay_extend_via_api_raises_when_no_template_captured():
    client = _client()

    with pytest.raises(RuntimeError, match="no captured template"):
        await replay_extend_via_api(client, "media/new-parent", "new prompt")


@pytest.mark.asyncio
async def test_replay_extend_via_api_rewrites_body_and_sets_recaptcha(caplog):
    client = _client_with_template()
    client.page.context.request.post.return_value = FakeAPIResponse(
        200,
        {
            "operations": [
                {"operation": {"name": "projects/proj1/operations/op1/media/new-media"}}
            ]
        },
    )

    with caplog.at_level("INFO"):
        media_id = await replay_extend_via_api(client, "media/new-parent", "new prompt")

    assert media_id == "new-media"
    body = json.loads(client.page.context.request.post.await_args.kwargs["data"])
    assert len(body["requests"]) == 1
    assert body["requests"][0]["videoExtendInput"]["sourceMedia"]["name"] == "media/new-parent"
    prompt_text = body["requests"][0]["textInput"]["structuredPrompt"]["parts"][0]["text"]
    assert prompt_text == "new prompt"
    assert body["mediaGenerationContext"]["batchId"] != "old-batch"
    assert body["clientContext"]["recaptchaContext"]["token"] == "fresh-recaptcha-token"
    assert body["requests"][0]["recaptchaContext"]["token"] == "fresh-recaptcha-token"
    assert body["requests"][0]["clientContext"]["recaptchaContext"]["token"] == "fresh-recaptcha-token"
    assert "requests[0].videoExtendInput.sourceMedia.name" in caplog.text
    assert "requests[0].textInput.structuredPrompt.parts[0].text" in caplog.text


@pytest.mark.asyncio
async def test_replay_extend_via_api_raises_on_4xx_with_body_snippet():
    client = _client_with_template()
    client.page.context.request.post.return_value = FakeAPIResponse(403, text="forbidden body")

    with pytest.raises(RuntimeError, match="HTTP 403: forbidden body"):
        await replay_extend_via_api(client, "media/new-parent", "new prompt")


@pytest.mark.asyncio
async def test_replay_extend_via_api_raises_on_zero_media_entries():
    client = _client_with_template()
    client.page.context.request.post.return_value = FakeAPIResponse(200, {"operations": []})

    with pytest.raises(RuntimeError, match="requested 1 media but got 0"):
        await replay_extend_via_api(client, "media/new-parent", "new prompt")


@pytest.mark.asyncio
async def test_replay_extend_via_api_headers_only_from_allowlist():
    client = _client_with_template()
    client.page.context.request.post.return_value = FakeAPIResponse(
        200,
        {"operations": [{"operation": {"name": "projects/proj1/media/new-media"}}]},
    )

    await replay_extend_via_api(client, "media/new-parent", "new prompt")

    headers = client.page.context.request.post.await_args.kwargs["headers"]
    assert headers == {
        "authorization": "Bearer tok",
        "content-type": "text/plain;charset=UTF-8",
        "x-goog-api-key": "api-key",
        "x-recaptcha-token": "fresh-recaptcha-token",
    }


@pytest.mark.asyncio
async def test_replay_extend_via_api_recaptcha_mint_failure_uses_captured_token(monkeypatch, caplog):
    async def _mint_empty(_page, *, caller):
        return ""

    monkeypatch.setattr(extend_api, "_mint_recaptcha_token", _mint_empty)
    client = _client_with_template()
    client.page.context.request.post.return_value = FakeAPIResponse(
        200,
        {"operations": [{"operation": {"name": "projects/proj1/media/new-media"}}]},
    )

    with caplog.at_level("WARNING"):
        await replay_extend_via_api(client, "media/new-parent", "new prompt")

    body = json.loads(client.page.context.request.post.await_args.kwargs["data"])
    headers = client.page.context.request.post.await_args.kwargs["headers"]
    assert body["clientContext"]["recaptchaContext"]["token"] == "stale-root-token"
    assert body["requests"][0]["recaptchaContext"]["token"] == "stale-request-token"
    assert headers["x-recaptcha-token"] == "stale-header-token"
    assert "using captured token if present" in caplog.text


def test_extract_extend_media_names_walks_nested_operation_names():
    data = {
        "operations": [
            {"operation": {"name": "projects/proj1/operations/op1/media/first"}},
            {"operation": {"name": "projects/proj1/media/second"}},
        ]
    }

    assert _extract_extend_media_names(data) == ["first", "second"]
