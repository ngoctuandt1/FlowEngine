import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from flow.operations import extend_api
from flow.operations.extend_api import (
    _anchored_parent_from_post_data,
    _extract_extend_media_names,
    _find_anchored_parent_uuid,
    _replace_uuid_in_body,
    _strip_media_prefix,
    clear_extend_capture,
    get_extend_request_template,
    install_extend_request_capture,
    replay_extend_via_api,
)


PARENT_UUID = "b9098d5f-ac4a-46e5-ae5e-725474309ec2"
NEW_PARENT_UUID = "12345678-1234-1234-1234-123456789abc"
PROJECT_ID_UUID = "deadbeef-dead-beef-dead-beefdeadbeef"


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
        # No parent UUID was present in the bodies, so the anchor is None.
        "anchored_parent": None,
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
async def test_replay_extend_via_api_default_model_none_preserves_template_model():
    body = _template_body()
    body["requests"][0]["videoModelKey"] = "veo-3.1-lite"
    client = _client_with_template()
    client._extend_request_template["post_data"] = json.dumps(body)
    client.page.context.request.post.return_value = FakeAPIResponse(
        200,
        {"operations": [{"operation": {"name": "projects/proj1/media/new-media"}}]},
    )

    await replay_extend_via_api(client, "media/new-parent", "new prompt")

    payload = json.loads(client.page.context.request.post.await_args.kwargs["data"])
    assert payload["requests"][0]["videoModelKey"] == "veo-3.1-lite"


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
async def test_replay_extend_via_api_accepts_bare_uuid_media_name_response():
    media_id = "12345678-1234-1234-1234-123456789abc"
    client = _client_with_template()
    client.page.context.request.post.return_value = FakeAPIResponse(
        200,
        {"media": [{"name": media_id}]},
    )

    assert await replay_extend_via_api(client, "media/new-parent", "new prompt") == media_id


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


def test_extract_media_names_accepts_bare_uuid_at_media_name():
    media_id = "12345678-1234-1234-1234-123456789abc"

    assert _extract_extend_media_names({"media": [{"name": media_id}]}) == [media_id]


def test_extract_media_names_accepts_bare_uuid_at_operations_operation_name():
    media_id = "12345678-1234-1234-1234-123456789abc"
    data = {"operations": [{"operation": {"name": media_id}}]}

    assert _extract_extend_media_names(data) == [media_id]


def test_extract_media_names_prefers_path_style_when_both_present():
    bare_media_id = "12345678-1234-1234-1234-123456789abc"
    path_media_id = "87654321-4321-4321-4321-cba987654321"
    data = {"media": [{"name": f"projects/proj1/media/{path_media_id}?name={bare_media_id}"}]}

    assert _extract_extend_media_names(data) == [path_media_id]


def test_extract_media_names_rejects_non_uuid_bare_name():
    assert _extract_extend_media_names({"media": [{"name": "not-a-uuid-just-text"}]}) == []


# ----------------------------------------------------------------------
# Walk-by-VALUE parent UUID rewriting — new in this PR.
# ----------------------------------------------------------------------


def _flow_extend_body_unusual_path(parent_uuid: str) -> dict:
    """A realistic-shaped extend POST body where the parent UUID lives
    under a path that the legacy ``_PARENT_FIELD_CANDIDATES`` list does NOT
    cover. Mirrors the live-verify v5 failure mode that motivated this PR.
    """
    return {
        "clientContext": {
            "projectId": PROJECT_ID_UUID,
            "tool": "PINHOLE",
        },
        "mediaGenerationContext": {"batchId": "old-batch"},
        "requests": [
            {
                # Parent media nested under a key Flow chose that the
                # candidate list does not anticipate. Stored both as bare
                # UUID and as a media/<uuid> path so a single replace pass
                # has to handle both formats.
                "videoExtendInput": {
                    "extendSource": {
                        "referenceMedia": {
                            "mediaPointer": f"media/{parent_uuid}",
                            "mediaId": parent_uuid,
                        },
                        "anchor": {
                            "sourceVideoName": (
                                f"projects/{PROJECT_ID_UUID}/media/{parent_uuid}"
                            ),
                        },
                    }
                },
                "textInput": {
                    "structuredPrompt": {"parts": [{"text": "old prompt"}]}
                },
            }
        ],
    }


def test_find_anchored_parent_uuid_picks_uuid_under_parent_ish_key():
    body = _flow_extend_body_unusual_path(PARENT_UUID)
    assert _find_anchored_parent_uuid(body) == PARENT_UUID


def test_find_anchored_parent_uuid_skips_uuid_under_neutral_key():
    # ``projectId`` is UUID-shaped but does not match parent|source|media
    # |name; the walker must NOT return it just because it scans first.
    body = {
        "clientContext": {"projectId": PROJECT_ID_UUID},
        "requests": [],
    }
    assert _find_anchored_parent_uuid(body) is None


def test_find_anchored_parent_uuid_returns_none_for_empty_body():
    assert _find_anchored_parent_uuid({}) is None
    assert _find_anchored_parent_uuid([]) is None


def test_strip_media_prefix_handles_path_forms():
    assert _strip_media_prefix(PARENT_UUID) == PARENT_UUID
    assert _strip_media_prefix(f"media/{PARENT_UUID}") == PARENT_UUID
    assert (
        _strip_media_prefix(f"projects/proj1/media/{PARENT_UUID}") == PARENT_UUID
    )
    assert _strip_media_prefix("not-a-uuid") == "not-a-uuid"
    assert _strip_media_prefix("") == ""


def test_replace_uuid_in_body_rewrites_every_occurrence_in_place():
    body = _flow_extend_body_unusual_path(PARENT_UUID)
    paths = _replace_uuid_in_body(body, PARENT_UUID, NEW_PARENT_UUID)

    # Three occurrences across the unusual-path body: mediaPointer,
    # mediaId, sourceVideoName.
    assert len(paths) == 3
    extend = body["requests"][0]["videoExtendInput"]["extendSource"]
    assert extend["referenceMedia"]["mediaPointer"] == f"media/{NEW_PARENT_UUID}"
    assert extend["referenceMedia"]["mediaId"] == NEW_PARENT_UUID
    assert (
        extend["anchor"]["sourceVideoName"]
        == f"projects/{PROJECT_ID_UUID}/media/{NEW_PARENT_UUID}"
    )
    # Surrounding context (projectId) is untouched.
    assert body["clientContext"]["projectId"] == PROJECT_ID_UUID


def test_replace_uuid_in_body_returns_empty_when_uuid_absent():
    body = {"requests": [{"videoExtendInput": {"sourceMedia": {"name": "old"}}}]}
    assert _replace_uuid_in_body(body, PARENT_UUID, NEW_PARENT_UUID) == []


def test_replace_uuid_in_body_no_op_for_empty_old_uuid():
    body = {"x": PARENT_UUID}
    assert _replace_uuid_in_body(body, "", NEW_PARENT_UUID) == []
    assert body["x"] == PARENT_UUID


def test_anchored_parent_from_post_data_parses_json_string():
    body = _flow_extend_body_unusual_path(PARENT_UUID)
    assert _anchored_parent_from_post_data(json.dumps(body)) == PARENT_UUID


def test_anchored_parent_from_post_data_returns_none_for_bad_json():
    assert _anchored_parent_from_post_data("not json") is None
    assert _anchored_parent_from_post_data("") is None
    assert _anchored_parent_from_post_data(None) is None
    assert _anchored_parent_from_post_data(12345) is None


def test_anchored_parent_from_post_data_accepts_bytes():
    body = _flow_extend_body_unusual_path(PARENT_UUID)
    assert (
        _anchored_parent_from_post_data(json.dumps(body).encode("utf-8"))
        == PARENT_UUID
    )


def test_install_extend_request_capture_records_anchored_parent_uuid():
    page = FakePage()
    client = _client(page)
    install_extend_request_capture(client)

    body = _flow_extend_body_unusual_path(PARENT_UUID)
    page.fire_request(
        SimpleNamespace(
            url=EXTEND_URL,
            method="POST",
            headers={"authorization": "Bearer tok"},
            post_data=json.dumps(body),
        )
    )

    template = client._extend_request_template
    assert template["anchored_parent"] == PARENT_UUID


def _client_with_unusual_template():
    """Build a client whose captured template has the parent UUID at a
    Flow-shape that the legacy path candidates do NOT cover."""
    client = _client()
    body = _flow_extend_body_unusual_path(PARENT_UUID)
    client._extend_request_template = {
        "url": EXTEND_URL,
        "headers": {
            "authorization": "Bearer tok",
            "content-type": "text/plain;charset=UTF-8",
            "x-goog-api-key": "api-key",
            "x-recaptcha-token": "stale-header-token",
        },
        "post_data": json.dumps(body),
        "anchored_parent": PARENT_UUID,
    }
    return client


@pytest.mark.asyncio
async def test_replay_extend_via_api_walks_by_value_when_path_candidates_miss(caplog):
    """Live-verify v5 regression: the candidate path list misses Flow's
    actual parent slot. With an anchored UUID, replay must still succeed by
    walking the body and rewriting every occurrence."""
    client = _client_with_unusual_template()
    client.page.context.request.post.return_value = FakeAPIResponse(
        200,
        {"operations": [{"operation": {"name": f"projects/proj1/media/{NEW_PARENT_UUID}"}}]},
    )

    with caplog.at_level("INFO"):
        media_id = await replay_extend_via_api(client, NEW_PARENT_UUID, "new prompt")

    assert media_id == NEW_PARENT_UUID
    body = json.loads(client.page.context.request.post.await_args.kwargs["data"])
    extend = body["requests"][0]["videoExtendInput"]["extendSource"]
    assert extend["referenceMedia"]["mediaPointer"] == f"media/{NEW_PARENT_UUID}"
    assert extend["referenceMedia"]["mediaId"] == NEW_PARENT_UUID
    assert (
        extend["anchor"]["sourceVideoName"]
        == f"projects/{PROJECT_ID_UUID}/media/{NEW_PARENT_UUID}"
    )
    # The projectId UUID must NOT have been collateral-damaged.
    assert body["clientContext"]["projectId"] == PROJECT_ID_UUID
    # Walk-by-value log message surfaces every rewritten path.
    assert "walk-by-value replaced parent UUID" in caplog.text


@pytest.mark.asyncio
async def test_replay_extend_via_api_walks_by_value_accepts_path_form_new_parent():
    """Callers historically passed ``media/<uuid>``; new helper must
    strip that prefix before substring-replacing inside path strings so the
    result stays a well-formed ``media/<uuid>``."""
    client = _client_with_unusual_template()
    client.page.context.request.post.return_value = FakeAPIResponse(
        200,
        {"operations": [{"operation": {"name": f"projects/proj1/media/{NEW_PARENT_UUID}"}}]},
    )

    await replay_extend_via_api(client, f"media/{NEW_PARENT_UUID}", "new prompt")

    body = json.loads(client.page.context.request.post.await_args.kwargs["data"])
    extend = body["requests"][0]["videoExtendInput"]["extendSource"]
    assert extend["referenceMedia"]["mediaPointer"] == f"media/{NEW_PARENT_UUID}"
    # Bare-UUID slot stays bare (no duplicated ``media/`` prefix).
    assert extend["referenceMedia"]["mediaId"] == NEW_PARENT_UUID


@pytest.mark.asyncio
async def test_replay_extend_via_api_raises_when_no_anchored_parent_and_path_misses():
    """If the captured template has no anchored parent (couldn't be parsed
    at install time) AND the legacy path candidates miss, replay must
    raise an informative error rather than silently sending an unmodified
    body."""
    client = _client()
    body = _flow_extend_body_unusual_path(PARENT_UUID)
    client._extend_request_template = {
        "url": EXTEND_URL,
        "headers": {"authorization": "Bearer tok"},
        "post_data": json.dumps(body),
        "anchored_parent": None,
    }

    with pytest.raises(RuntimeError, match="no anchored parent UUID was captured"):
        await replay_extend_via_api(client, NEW_PARENT_UUID, "new prompt")


@pytest.mark.asyncio
async def test_replay_extend_via_api_raises_when_anchored_uuid_absent_from_body():
    """If the anchored UUID was captured but the (mutated) body no longer
    contains it, we must raise rather than send an unchanged body."""
    client = _client()
    body = {
        "clientContext": {"projectId": PROJECT_ID_UUID},
        "requests": [{"unrelated": {"field": "no uuid here"}}],
    }
    client._extend_request_template = {
        "url": EXTEND_URL,
        "headers": {"authorization": "Bearer tok"},
        "post_data": json.dumps(body),
        "anchored_parent": PARENT_UUID,
    }

    with pytest.raises(RuntimeError, match="not found in captured template body"):
        await replay_extend_via_api(client, NEW_PARENT_UUID, "new prompt")
