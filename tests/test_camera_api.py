import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from flow.operations import camera_api
from flow.operations.camera_api import (
    clear_camera_capture,
    get_camera_request_template,
    install_camera_request_capture,
    replay_camera_via_api,
)

PARENT_UUID = "b9098d5f-ac4a-46e5-ae5e-725474309ec2"
NEW_PARENT_UUID = "12345678-1234-1234-1234-123456789abc"
PROJECT_ID_UUID = "deadbeef-dead-beef-dead-beefdeadbeef"
CAMERA_URL = "https://aisandbox-pa.googleapis.com/v1/video:batchAsyncGenerateVideoReshootVideo"
NON_CAMERA_URL = "https://aisandbox-pa.googleapis.com/v1/video:batchAsyncGenerateVideoExtendVideo"


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


def _template_body(parent="old-parent", direction="Dolly out"):
    return {
        "clientContext": {
            "projectId": "proj1",
            "recaptchaContext": {"token": "stale-root-token"},
        },
        "mediaGenerationContext": {"batchId": "old-batch"},
        "requests": [
            {
                "videoReshootInput": {
                    "sourceMedia": {"name": parent},
                    "cameraInput": {"direction": direction},
                },
                "recaptchaContext": {"token": "stale-request-token"},
                "clientContext": {"recaptchaContext": {"token": "stale-item-token"}},
            },
            {
                "videoReshootInput": {
                    "sourceMedia": {"name": "second-parent"},
                    "cameraInput": {"direction": "Pan left"},
                }
            },
        ],
    }


def _client_with_template(body=None):
    client = _client()
    client._camera_request_template = {
        "url": CAMERA_URL,
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
        assert caller == "replay_camera_via_api"
        return "fresh-recaptcha-token"

    monkeypatch.setattr(camera_api, "_mint_recaptcha_token", _mint)


def test_install_camera_request_capture_records_latest_matching_post_template(caplog):
    page = FakePage()
    client = _client(page)
    install_camera_request_capture(client)

    first = SimpleNamespace(
        url=CAMERA_URL,
        method="POST",
        headers={"authorization": "Bearer first"},
        post_data='{"first": true}',
    )
    latest = SimpleNamespace(
        url=CAMERA_URL.upper(),
        method="POST",
        headers={"authorization": "Bearer latest"},
        post_data='{"latest": true}',
    )
    with caplog.at_level("INFO"):
        page.fire_request(first)
        page.fire_request(latest)

    assert client._camera_request_template == {
        "url": CAMERA_URL.upper(),
        "headers": {"authorization": "Bearer latest"},
        "post_data": '{"latest": true}',
        "anchored_parent": None,
    }
    assert "Captured Flow camera reverseAPI template" in caplog.text


def test_install_camera_request_capture_ignores_non_camera_urls():
    page = FakePage()
    client = _client(page)
    install_camera_request_capture(client)

    page.fire_request(SimpleNamespace(url=NON_CAMERA_URL, method="POST", headers={}, post_data="{}"))

    assert get_camera_request_template(client) is None


def test_install_camera_request_capture_replaces_previous_listener():
    page = FakePage()
    client = _client(page)
    install_camera_request_capture(client)
    first_listener = page.listeners["request"][0]

    install_camera_request_capture(client)

    assert len(page.listeners["request"]) == 1
    assert page.listeners["request"][0] is not first_listener


def test_get_camera_request_template_returns_none_then_dict_and_clear_resets():
    client = _client()

    assert get_camera_request_template(client) is None
    client._camera_request_template = {"url": CAMERA_URL, "headers": {}, "post_data": "{}"}
    assert get_camera_request_template(client) == client._camera_request_template

    clear_camera_capture(client)
    assert get_camera_request_template(client) is None


@pytest.mark.asyncio
async def test_replay_camera_via_api_raises_when_no_template_captured():
    with pytest.raises(RuntimeError, match="no captured template"):
        await replay_camera_via_api(_client(), "media/new-parent", "Dolly in")


@pytest.mark.asyncio
async def test_replay_camera_via_api_rewrites_body_and_sets_recaptcha(caplog):
    client = _client_with_template()
    client.page.context.request.post.return_value = FakeAPIResponse(
        200,
        {"media": [{"name": "12345678-1234-1234-1234-123456789abc"}]},
    )

    with caplog.at_level("INFO"):
        media_id = await replay_camera_via_api(client, "media/new-parent", "Pan right")

    assert media_id == "12345678-1234-1234-1234-123456789abc"
    body = json.loads(client.page.context.request.post.await_args.kwargs["data"])
    request = body["requests"][0]
    assert len(body["requests"]) == 1
    assert request["videoReshootInput"]["sourceMedia"]["name"] == "media/new-parent"
    assert request["videoReshootInput"]["cameraInput"]["direction"] == "Pan right"
    assert body["mediaGenerationContext"]["batchId"] != "old-batch"
    assert body["clientContext"]["recaptchaContext"]["token"] == "fresh-recaptcha-token"
    assert request["recaptchaContext"]["token"] == "fresh-recaptcha-token"
    assert "requests[0].videoReshootInput.sourceMedia.name" in caplog.text
    assert "requests[0].videoReshootInput.cameraInput.direction" in caplog.text


@pytest.mark.asyncio
async def test_replay_camera_via_api_raises_on_4xx_with_body_snippet():
    client = _client_with_template()
    client.page.context.request.post.return_value = FakeAPIResponse(403, text="forbidden body")

    with pytest.raises(RuntimeError, match="HTTP 403: forbidden body"):
        await replay_camera_via_api(client, "media/new-parent", "Pan right")


@pytest.mark.asyncio
async def test_replay_camera_via_api_raises_on_zero_media_entries():
    client = _client_with_template()
    client.page.context.request.post.return_value = FakeAPIResponse(200, {"operations": []})

    with pytest.raises(RuntimeError, match="requested 1 media but got 0"):
        await replay_camera_via_api(client, "media/new-parent", "Pan right")


@pytest.mark.asyncio
async def test_replay_camera_via_api_headers_only_from_allowlist():
    client = _client_with_template()
    client.page.context.request.post.return_value = FakeAPIResponse(
        200,
        {"operations": [{"operation": {"name": "projects/proj1/media/new-media"}}]},
    )

    await replay_camera_via_api(client, "media/new-parent", "Pan right")

    assert client.page.context.request.post.await_args.kwargs["headers"] == {
        "authorization": "Bearer tok",
        "content-type": "text/plain;charset=UTF-8",
        "x-goog-api-key": "api-key",
        "x-recaptcha-token": "fresh-recaptcha-token",
    }


@pytest.mark.asyncio
async def test_replay_camera_via_api_recaptcha_mint_failure_uses_captured_token(monkeypatch, caplog):
    async def _mint_empty(_page, *, caller):
        return ""

    monkeypatch.setattr(camera_api, "_mint_recaptcha_token", _mint_empty)
    client = _client_with_template()
    client.page.context.request.post.return_value = FakeAPIResponse(
        200,
        {"operations": [{"operation": {"name": "projects/proj1/media/new-media"}}]},
    )

    with caplog.at_level("WARNING"):
        await replay_camera_via_api(client, "media/new-parent", "Pan right")

    body = json.loads(client.page.context.request.post.await_args.kwargs["data"])
    headers = client.page.context.request.post.await_args.kwargs["headers"]
    assert body["clientContext"]["recaptchaContext"]["token"] == "stale-root-token"
    assert body["requests"][0]["recaptchaContext"]["token"] == "stale-request-token"
    assert headers["x-recaptcha-token"] == "stale-header-token"
    assert "using captured token if present" in caplog.text


def _camera_body_unusual_path(parent_uuid: str) -> dict:
    return {
        "clientContext": {"projectId": PROJECT_ID_UUID},
        "mediaGenerationContext": {"batchId": "old-batch"},
        "requests": [
            {
                "cameraOperation": {
                    "referenceMedia": {
                        "mediaPointer": f"media/{parent_uuid}",
                        "mediaId": parent_uuid,
                    },
                    "cameraPreset": "Dolly out",
                }
            }
        ],
    }


@pytest.mark.asyncio
async def test_replay_camera_via_api_walks_by_value_when_parent_path_candidates_miss(caplog):
    client = _client_with_template(_camera_body_unusual_path(PARENT_UUID))
    client._camera_request_template["anchored_parent"] = PARENT_UUID
    client.page.context.request.post.return_value = FakeAPIResponse(
        200,
        {"operations": [{"operation": {"name": f"projects/proj1/media/{NEW_PARENT_UUID}"}}]},
    )

    with caplog.at_level("INFO"):
        media_id = await replay_camera_via_api(client, NEW_PARENT_UUID, "Dolly in")

    assert media_id == NEW_PARENT_UUID
    body = json.loads(client.page.context.request.post.await_args.kwargs["data"])
    operation = body["requests"][0]["cameraOperation"]
    assert operation["referenceMedia"]["mediaPointer"] == f"media/{NEW_PARENT_UUID}"
    assert operation["referenceMedia"]["mediaId"] == NEW_PARENT_UUID
    assert operation["cameraPreset"] == "Dolly in"
    assert body["clientContext"]["projectId"] == PROJECT_ID_UUID
    assert "walk-by-value replaced parent UUID" in caplog.text
