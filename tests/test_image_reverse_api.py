import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

import flow.operations.image as image_module
from flow.operations.image_api import (
    get_image_request_template,
    install_image_request_capture,
    replay_image_generate,
)


IMAGE_URL = "https://aisandbox-pa.googleapis.com/v1/projects/proj1/flowMedia:batchGenerateImages"
NON_IMAGE_URL = "https://aisandbox-pa.googleapis.com/v1/projects/proj1/operations/op1"


class FakePage:
    def __init__(self):
        self.listeners = {}
        self.context = SimpleNamespace(
            request=SimpleNamespace(post=AsyncMock())
        )
        self.url = "https://labs.google/fx/tools/flow/project/proj1"
        self.goto = AsyncMock(return_value=None)
        self.wait_for_selector = AsyncMock(return_value=None)
        self.wait_for_url = AsyncMock(return_value=None)
        self.evaluate = AsyncMock(return_value="fresh-recaptcha-token")

    def on(self, event_name, callback):
        self.listeners.setdefault(event_name, []).append(callback)

    async def fire_response(self, response):
        for callback in self.listeners.get("response", []):
            result = callback(response)
            if hasattr(result, "__await__"):
                await result

    def fire_request(self, request):
        for callback in self.listeners.get("request", []):
            callback(request)


class FakeAPIResponse:
    def __init__(self, status, body=None, text=""):
        self.status = status
        self.status_code = status
        self.ok = 200 <= status < 300
        self._body = body or {}
        self._text = text

    async def json(self):
        return self._body

    async def text(self):
        return self._text


class FakeRequest:
    url = IMAGE_URL
    method = "POST"
    headers = {"authorization": "Bearer tok", "content-type": "text/plain;charset=UTF-8"}
    post_data = '{"requests": []}'


class FakeResponse:
    url = IMAGE_URL
    status = 200

    async def json(self):
        return {"media": [{"name": "m1"}]}

    async def text(self):
        return ""


def _client(page=None):
    return SimpleNamespace(
        page=page or FakePage(),
        profile_name="test-profile",
        _calls=[],
        _image_names=[],
        _gen_id=None,
        clear_captures=Mock(return_value=None),
    )


def _client_with_template():
    client = _client()
    client._image_requests = [
        {
            "url": IMAGE_URL,
            "method": "POST",
            "headers": {
                "authorization": "Bearer tok",
                "content-type": "text/plain;charset=UTF-8",
                "cookie": "stale-cookie=1",
                "origin": "https://labs.google",
                "referer": "https://labs.google/fx/tools/flow",
                "x-goog-api-key": "api-key",
                "x-recaptcha-token": "stale-header-token",
            },
            "post_data": json.dumps(
                {
                    "clientContext": {
                        "projectId": "proj1",
                        "recaptchaContext": {"token": "stale-root-token"},
                        "tool": "PINHOLE",
                        "sessionId": "s1",
                    },
                    "mediaGenerationContext": {"batchId": "b1"},
                    "requests": [
                        {
                            "clientContext": {
                                "projectId": "proj1",
                                "recaptchaContext": {"token": "stale-item-token"},
                                "tool": "PINHOLE",
                                "sessionId": "r1",
                            },
                            "structuredPrompt": {"parts": [{"text": "a cat"}]},
                            "seed": 111,
                            "imageModelName": "GEM_PIX_2",
                        }
                    ],
                }
            ),
        }
    ]
    client._image_names = []
    return client


def test_capture_idempotent():
    page = FakePage()
    client = _client(page)

    install_image_request_capture(client)
    install_image_request_capture(client)

    assert len(page.listeners.get("request", [])) == 1
    assert len(page.listeners.get("response", [])) == 1


def test_capture_stores_request():
    page = FakePage()
    client = _client(page)
    install_image_request_capture(client)

    page.fire_request(FakeRequest())

    assert len(client._image_requests) == 1
    captured = client._image_requests[0]
    assert captured["url"] == IMAGE_URL
    assert captured["method"] == "POST"
    assert captured["headers"] == FakeRequest.headers
    assert captured["post_data"] == FakeRequest.post_data


def test_capture_ignores_non_image_urls():
    page = FakePage()
    client = _client(page)
    install_image_request_capture(client)

    page.fire_request(SimpleNamespace(
        url=NON_IMAGE_URL,
        method="POST",
        headers={},
        post_data="{}",
    ))

    assert client._image_requests == []


@pytest.mark.asyncio
async def test_capture_stores_response():
    page = FakePage()
    client = _client(page)
    install_image_request_capture(client)

    await page.fire_response(FakeResponse())

    assert len(client._image_responses) == 1
    assert client._image_responses[0]["url"] == IMAGE_URL
    assert client._image_responses[0]["status"] == 200
    assert client._image_responses[0]["body"] == {"media": [{"name": "m1"}]}


def test_get_image_request_template_returns_latest():
    client = _client_with_template()
    client._image_requests.append({"url": IMAGE_URL, "method": "POST", "headers": {}, "post_data": "{}"})

    assert get_image_request_template(client) == client._image_requests[-1]


@pytest.mark.asyncio
async def test_replay_returns_media_names():
    client = _client_with_template()
    client.page.context.request.post.return_value = FakeAPIResponse(
        200,
        {"media": [{"name": "projects/proj1/media/abc"}, {"name": "projects/proj1/media/def"}]},
    )

    result = await replay_image_generate(client, "a dog", count=2)

    assert result == ["projects/proj1/media/abc", "projects/proj1/media/def"]


@pytest.mark.asyncio
async def test_replay_updates_client_image_names():
    client = _client_with_template()
    client.page.context.request.post.return_value = FakeAPIResponse(
        200,
        {"media": [{"name": "projects/proj1/media/abc"}, {"name": "projects/proj1/media/def"}]},
    )

    await replay_image_generate(client, "a dog", count=2)

    assert client._image_names == ["projects/proj1/media/abc", "projects/proj1/media/def"]


@pytest.mark.asyncio
async def test_replay_uses_new_prompt():
    client = _client_with_template()
    client.page.context.request.post.return_value = FakeAPIResponse(
        200,
        {"media": [{"name": "projects/proj1/media/abc"}, {"name": "projects/proj1/media/def"}]},
    )

    await replay_image_generate(client, "a dog", count=2)

    post_data = client.page.context.request.post.await_args.kwargs["data"]
    payload = json.loads(post_data) if isinstance(post_data, str) else post_data
    prompt_text = payload["requests"][0]["structuredPrompt"]["parts"][0]["text"]
    assert prompt_text == "a dog"
    assert payload["clientContext"]["recaptchaContext"]["token"] == "fresh-recaptcha-token"
    assert {r["clientContext"]["recaptchaContext"]["token"] for r in payload["requests"]} == {
        "fresh-recaptcha-token"
    }
    assert payload["clientContext"]["sessionId"] != "s1"
    assert all(r["clientContext"]["sessionId"] != "r1" for r in payload["requests"])
    assert payload["mediaGenerationContext"]["batchId"] != "b1"
    assert client.page.evaluate.await_count == 1

    headers = client.page.context.request.post.await_args.kwargs["headers"]
    assert headers == {
        "authorization": "Bearer tok",
        "content-type": "text/plain;charset=UTF-8",
        "x-goog-api-key": "api-key",
        "x-recaptcha-token": "fresh-recaptcha-token",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("minted_token", ["", None])
async def test_replay_raises_when_recaptcha_mint_empty(monkeypatch, caplog, minted_token):
    client = _client_with_template()
    monkeypatch.setattr(client.page, "evaluate", AsyncMock(return_value=minted_token))
    caplog.set_level("WARNING")

    with pytest.raises(RuntimeError, match="refusing to reuse single-use captured token"):
        await replay_image_generate(client, "a dog", count=2)

    assert (
        "reCAPTCHA mint returned empty — cannot perform reverse-API replay safely; falling back to UI path required"
        in caplog.messages
    )
    client.page.context.request.post.assert_not_awaited()


@pytest.mark.asyncio
async def test_replay_raises_when_no_template():
    client = _client()
    client._image_requests = []

    with pytest.raises(RuntimeError):
        await replay_image_generate(client, "a dog", count=2)


@pytest.mark.asyncio
async def test_replay_raises_on_api_failure_or_partial_media():
    client = _client_with_template()
    client.page.context.request.post.return_value = FakeAPIResponse(500, {"error": "boom"}, "boom")

    with pytest.raises(RuntimeError):
        await replay_image_generate(client, "a dog", count=2)

    client = _client_with_template()
    client.page.context.request.post.return_value = FakeAPIResponse(
        200,
        {"media": [{"name": "projects/proj1/media/abc"}]},
    )

    with pytest.raises(RuntimeError, match="requested 2 images but got 1"):
        await replay_image_generate(client, "a dog", count=2)


@pytest.mark.asyncio
async def test_reverse_env_template_replay_skips_ui_submit(monkeypatch):
    client = _client(FakePage())
    replay = AsyncMock(return_value=["media/replay-1"])
    install_capture = Mock(return_value=None)
    get_template = Mock(return_value={"url": "x", "headers": {}, "post_data": "{}"})
    monkeypatch.setenv("FLOW_T2I_VIA_REVERSE", "1")
    monkeypatch.setattr(image_module, "install_image_request_capture", install_capture)
    monkeypatch.setattr(image_module, "replay_image_generate", replay)
    monkeypatch.setattr(image_module, "get_image_request_template", get_template)
    mocks = _patch_image_ui_path(monkeypatch)

    result = await image_module.text_to_image(client, prompt="a dog")

    assert result["media_id"] == "media/replay-1"
    assert result["output_files"] == ["api.png"]
    install_capture.assert_called_once_with(client)
    get_template.assert_called_once_with(client)
    replay.assert_awaited_once_with(client, "a dog", count=1)
    mocks["submit_with_confirmation"].assert_not_awaited()


@pytest.mark.asyncio
async def test_reverse_env_off_no_replay(monkeypatch):
    client = _client(FakePage())
    replay = AsyncMock(return_value=["media/replay-1"])
    install_capture = Mock(return_value=None)
    get_template = Mock(return_value={"url": "x", "headers": {}, "post_data": "{}"})
    monkeypatch.setenv("FLOW_T2I_VIA_REVERSE", "0")
    monkeypatch.setattr(image_module, "install_image_request_capture", install_capture)
    monkeypatch.setattr(image_module, "replay_image_generate", replay)
    monkeypatch.setattr(image_module, "get_image_request_template", get_template)
    mocks = _patch_image_ui_path(monkeypatch)

    result = await image_module.text_to_image(client, prompt="a dog")

    assert result["media_id"] == "media/ui"
    assert result["output_files"] == ["out.png"]
    install_capture.assert_not_called()
    get_template.assert_not_called()
    replay.assert_not_awaited()
    mocks["submit_with_confirmation"].assert_awaited_once()


def _patch_image_ui_path(monkeypatch):
    monkeypatch.setattr(image_module, "flow_url", lambda _locale="": "https://labs.google/fx/tools/flow")
    monkeypatch.setattr(image_module, "is_login_page", lambda _url: False)
    monkeypatch.setattr(image_module, "extract_project_id", lambda _url: "proj1")
    mocks = {
        "_dismiss_overlays": AsyncMock(return_value=None),
        "_click_new_project": AsyncMock(return_value=None),
        "_wait_for_composer": AsyncMock(return_value=None),
        "_switch_to_image_output": AsyncMock(return_value=None),
        "_close_composer_menu": AsyncMock(return_value=None),
        "_type_prompt": AsyncMock(return_value=None),
        "_select_image_model": AsyncMock(return_value=None),
        "_set_image_aspect_ratio": AsyncMock(return_value=None),
        "_set_image_output_count": AsyncMock(return_value=None),
        "_count_visible_cards": AsyncMock(return_value=0),
        "submit_with_confirmation": AsyncMock(return_value=True),
        "wait_for_completion": AsyncMock(return_value={"done": True, "media_ids": ["media/ui"]}),
        "resolve_final_media_id": AsyncMock(return_value="media/ui"),
        "download_video": AsyncMock(return_value=["out.png"]),
        "download_via_url": AsyncMock(return_value="api.png"),
    }
    for name, mock in mocks.items():
        monkeypatch.setattr(image_module, name, mock)
    return mocks
