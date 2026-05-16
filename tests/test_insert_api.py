import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from flow.operations import insert_api
from flow.operations.insert_api import (
    clear_insert_capture,
    get_insert_request_template,
    install_insert_request_capture,
    replay_insert_via_api,
)

PARENT_UUID = "b9098d5f-ac4a-46e5-ae5e-725474309ec2"
NEW_PARENT_UUID = "12345678-1234-1234-1234-123456789abc"
PROJECT_ID_UUID = "deadbeef-dead-beef-dead-beefdeadbeef"
INSERT_URL = "https://aisandbox-pa.googleapis.com/v1/video:batchAsyncGenerateVideoObjectInsertion"
NON_INSERT_URL = "https://aisandbox-pa.googleapis.com/v1/video:batchAsyncGenerateVideoObjectRemoval"
BBOX = {"x": 0.1, "y": 0.2, "w": 0.3, "h": 0.4}


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


def _template_body(parent="old-parent", prompt="old prompt", bbox=None):
    bbox = bbox or {"x": 0.01, "y": 0.02, "w": 0.03, "h": 0.04}
    return {
        "clientContext": {
            "projectId": "proj1",
            "recaptchaContext": {"token": "stale-root-token"},
        },
        "mediaGenerationContext": {"batchId": "old-batch"},
        "requests": [
            {
                "videoObjectInsertionInput": {
                    "sourceMedia": {"name": parent},
                    "textInput": {
                        "structuredPrompt": {"parts": [{"text": prompt}]}
                    },
                    "bbox": bbox,
                },
                "recaptchaContext": {"token": "stale-request-token"},
                "clientContext": {"recaptchaContext": {"token": "stale-item-token"}},
            },
            {
                "videoObjectInsertionInput": {
                    "sourceMedia": {"name": "second-parent"},
                    "textInput": {"structuredPrompt": {"parts": [{"text": "second"}]}},
                    "bbox": {"x": 0.5, "y": 0.5, "w": 0.1, "h": 0.1},
                }
            },
        ],
    }


def _client_with_template(body=None):
    client = _client()
    client._insert_request_template = {
        "url": INSERT_URL,
        "headers": {
            "authorization": "Bearer tok",
            "content-type": "text/plain;charset=UTF-8",
            "cookie": "stale-cookie=1",
            "origin": "https://labs.google",
            "x-goog-api-key": "api-key",
            "x-recaptcha-token": "stale-header-token",
        },
        "post_data": json.dumps(body or _template_body()),
    }
    return client


@pytest.fixture(autouse=True)
def _mock_recaptcha(monkeypatch):
    async def _mint(_page, *, caller):
        assert caller == "replay_insert_via_api"
        return "fresh-recaptcha-token"

    monkeypatch.setattr(insert_api, "_mint_recaptcha_token", _mint)


def test_install_insert_request_capture_records_latest_matching_post_template(caplog):
    page = FakePage()
    client = _client(page)
    install_insert_request_capture(client)

    first = SimpleNamespace(
        url=INSERT_URL,
        method="POST",
        headers={"authorization": "Bearer first"},
        post_data='{"first": true}',
    )
    latest = SimpleNamespace(
        url=INSERT_URL.upper(),
        method="POST",
        headers={"authorization": "Bearer latest"},
        post_data='{"latest": true}',
    )
    with caplog.at_level("INFO"):
        page.fire_request(first)
        page.fire_request(latest)

    assert client._insert_request_template == {
        "url": INSERT_URL.upper(),
        "headers": {"authorization": "Bearer latest"},
        "post_data": '{"latest": true}',
        "anchored_parent": None,
    }
    assert "Captured Flow insert reverseAPI template" in caplog.text


def test_install_insert_request_capture_ignores_non_insert_urls():
    page = FakePage()
    client = _client(page)
    install_insert_request_capture(client)

    page.fire_request(SimpleNamespace(url=NON_INSERT_URL, method="POST", headers={}, post_data="{}"))

    assert get_insert_request_template(client) is None


def test_install_insert_request_capture_replaces_previous_listener():
    page = FakePage()
    client = _client(page)
    install_insert_request_capture(client)
    first_listener = page.listeners["request"][0]

    install_insert_request_capture(client)

    assert len(page.listeners["request"]) == 1
    assert page.listeners["request"][0] is not first_listener


def test_get_insert_request_template_returns_none_then_dict_and_clear_resets():
    client = _client()

    assert get_insert_request_template(client) is None
    client._insert_request_template = {"url": INSERT_URL, "headers": {}, "post_data": "{}"}
    assert get_insert_request_template(client) == client._insert_request_template

    clear_insert_capture(client)
    assert get_insert_request_template(client) is None


@pytest.mark.asyncio
async def test_replay_insert_via_api_raises_when_no_template_captured():
    with pytest.raises(RuntimeError, match="no captured template"):
        await replay_insert_via_api(_client(), "media/new-parent", "new prompt", BBOX)


@pytest.mark.asyncio
async def test_replay_insert_via_api_rewrites_body_and_sets_recaptcha(caplog):
    client = _client_with_template()
    client.page.context.request.post.return_value = FakeAPIResponse(
        200,
        {"media": [{"name": "12345678-1234-1234-1234-123456789abc"}]},
    )

    with caplog.at_level("INFO"):
        media_id = await replay_insert_via_api(client, "media/new-parent", "add red hat", BBOX)

    assert media_id == "12345678-1234-1234-1234-123456789abc"
    body = json.loads(client.page.context.request.post.await_args.kwargs["data"])
    request = body["requests"][0]
    insert = request["videoObjectInsertionInput"]
    assert len(body["requests"]) == 1
    assert insert["sourceMedia"]["name"] == "media/new-parent"
    assert insert["textInput"]["structuredPrompt"]["parts"][0]["text"] == "add red hat"
    assert insert["bbox"] == BBOX
    assert body["mediaGenerationContext"]["batchId"] != "old-batch"
    assert body["clientContext"]["recaptchaContext"]["token"] == "fresh-recaptcha-token"
    assert request["recaptchaContext"]["token"] == "fresh-recaptcha-token"
    assert "requests[0].videoObjectInsertionInput.sourceMedia.name" in caplog.text
    assert "requests[0].videoObjectInsertionInput.textInput.structuredPrompt.parts[0].text" in caplog.text
    assert "requests[0].videoObjectInsertionInput.bbox" in caplog.text


@pytest.mark.asyncio
async def test_replay_insert_via_api_raises_on_4xx_with_body_snippet():
    client = _client_with_template()
    client.page.context.request.post.return_value = FakeAPIResponse(400, text="bad bbox")

    with pytest.raises(RuntimeError, match="HTTP 400: bad bbox"):
        await replay_insert_via_api(client, "media/new-parent", "new prompt", BBOX)


@pytest.mark.asyncio
async def test_replay_insert_via_api_raises_on_zero_media_entries():
    client = _client_with_template()
    client.page.context.request.post.return_value = FakeAPIResponse(200, {"operations": []})

    with pytest.raises(RuntimeError, match="requested 1 media but got 0"):
        await replay_insert_via_api(client, "media/new-parent", "new prompt", BBOX)


@pytest.mark.asyncio
async def test_replay_insert_via_api_headers_only_from_allowlist():
    client = _client_with_template()
    client.page.context.request.post.return_value = FakeAPIResponse(
        200,
        {"operations": [{"operation": {"name": "projects/proj1/media/new-media"}}]},
    )

    await replay_insert_via_api(client, "media/new-parent", "new prompt", BBOX)

    assert client.page.context.request.post.await_args.kwargs["headers"] == {
        "authorization": "Bearer tok",
        "content-type": "text/plain;charset=UTF-8",
        "x-goog-api-key": "api-key",
        "x-recaptcha-token": "fresh-recaptcha-token",
    }


@pytest.mark.asyncio
async def test_replay_insert_via_api_recaptcha_mint_failure_uses_captured_token(monkeypatch, caplog):
    async def _mint_empty(_page, *, caller):
        return ""

    monkeypatch.setattr(insert_api, "_mint_recaptcha_token", _mint_empty)
    client = _client_with_template()
    client.page.context.request.post.return_value = FakeAPIResponse(
        200,
        {"operations": [{"operation": {"name": "projects/proj1/media/new-media"}}]},
    )

    with caplog.at_level("WARNING"):
        await replay_insert_via_api(client, "media/new-parent", "new prompt", BBOX)

    body = json.loads(client.page.context.request.post.await_args.kwargs["data"])
    headers = client.page.context.request.post.await_args.kwargs["headers"]
    assert body["clientContext"]["recaptchaContext"]["token"] == "stale-root-token"
    assert body["requests"][0]["recaptchaContext"]["token"] == "stale-request-token"
    assert headers["x-recaptcha-token"] == "stale-header-token"
    assert "using captured token if present" in caplog.text


def _insert_body_unusual_path(parent_uuid: str) -> dict:
    return {
        "clientContext": {"projectId": PROJECT_ID_UUID},
        "mediaGenerationContext": {"batchId": "old-batch"},
        "requests": [
            {
                "insertOperation": {
                    "referenceMedia": {
                        "mediaPointer": f"media/{parent_uuid}",
                        "mediaId": parent_uuid,
                    },
                    "prompt": "old prompt",
                    "selection": {"bbox": {"x": 0.5, "y": 0.5, "w": 0.1, "h": 0.1}},
                }
            }
        ],
    }


@pytest.mark.asyncio
async def test_replay_insert_via_api_walks_by_value_when_parent_path_candidates_miss(caplog):
    client = _client_with_template(_insert_body_unusual_path(PARENT_UUID))
    client._insert_request_template["anchored_parent"] = PARENT_UUID
    client.page.context.request.post.return_value = FakeAPIResponse(
        200,
        {"operations": [{"operation": {"name": f"projects/proj1/media/{NEW_PARENT_UUID}"}}]},
    )

    with caplog.at_level("INFO"):
        media_id = await replay_insert_via_api(client, NEW_PARENT_UUID, "add bike", BBOX)

    assert media_id == NEW_PARENT_UUID
    body = json.loads(client.page.context.request.post.await_args.kwargs["data"])
    operation = body["requests"][0]["insertOperation"]
    assert operation["referenceMedia"]["mediaPointer"] == f"media/{NEW_PARENT_UUID}"
    assert operation["referenceMedia"]["mediaId"] == NEW_PARENT_UUID
    assert operation["prompt"] == "add bike"
    assert operation["selection"]["bbox"] == BBOX
    assert body["clientContext"]["projectId"] == PROJECT_ID_UUID
    assert "walk-by-value replaced parent UUID" in caplog.text
