from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import flow.operations.generate_api as t2v_api


T2V_URL = "https://aisandbox-pa.googleapis.com/v1/video:batchAsyncGenerateVideoText"
EXTEND_URL = "https://aisandbox-pa.googleapis.com/v1/video:batchAsyncGenerateVideoExtendVideo"


class FakePage:
    def __init__(self):
        self.listeners: dict[str, list] = {}

    def on(self, event_name, callback):
        self.listeners.setdefault(event_name, []).append(callback)

    def remove_listener(self, event_name, callback):
        callbacks = self.listeners.get(event_name, [])
        self.listeners[event_name] = [item for item in callbacks if item is not callback]

    def fire_request(self, request):
        for callback in list(self.listeners.get("request", [])):
            callback(request)


class FakePageWithOff(FakePage):
    def __init__(self):
        super().__init__()
        self.removed: list[tuple[str, object]] = []

    def remove_listener(self, event_name, callback):
        raise RuntimeError("remove_listener unavailable")

    def off(self, event_name, callback):
        self.removed.append((event_name, callback))
        callbacks = self.listeners.get(event_name, [])
        self.listeners[event_name] = [item for item in callbacks if item is not callback]


class FakeRequest:
    def __init__(self, *, url=T2V_URL, method="POST", headers=None, post_data=None):
        self.url = url
        self.method = method
        self.headers = headers or {}
        self.post_data = post_data


def _client(page=None):
    return SimpleNamespace(page=page or FakePage())


def test_install_t2v_request_capture_records_latest_matching_post_template(caplog):
    page = FakePage()
    client = _client(page)
    t2v_api.install_t2v_request_capture(client)

    body1 = {"requests": [{"textInput": {"text": "old"}}]}
    body2 = {"requests": [{"textInput": {"text": "new"}}]}
    page.fire_request(FakeRequest(post_data=json.dumps(body1)))

    with caplog.at_level("INFO"):
        page.fire_request(
            FakeRequest(
                headers={"authorization": "Bearer tok", "content-type": "application/json"},
                post_data=json.dumps(body2),
            )
        )

    assert client._t2v_request_template == {
        "url": T2V_URL,
        "headers": {"authorization": "Bearer tok", "content-type": "application/json"},
        "post_data": json.dumps(body2),
        "anchored_parent": None,
    }
    assert "Captured Flow t2v reverseAPI template" in caplog.text
    assert "anchored_parent=<none>" in caplog.text


def test_install_t2v_request_capture_ignores_non_matching_requests():
    page = FakePage()
    client = _client(page)
    t2v_api.install_t2v_request_capture(client)

    page.fire_request(FakeRequest(method="GET", post_data="{}"))
    page.fire_request(FakeRequest(url=EXTEND_URL, post_data="{}"))
    page.fire_request(FakeRequest(url="https://example.test/other", post_data="{}"))

    assert getattr(client, "_t2v_request_template", None) is None


def test_install_t2v_request_capture_replaces_previous_listener():
    page = FakePage()
    client = _client(page)

    t2v_api.install_t2v_request_capture(client)
    first = client._t2v_request_capture_listener
    t2v_api.install_t2v_request_capture(client)
    second = client._t2v_request_capture_listener

    assert first is not second
    assert page.listeners["request"] == [second]


def test_install_t2v_request_capture_falls_back_to_off_for_idempotency():
    page = FakePageWithOff()
    client = _client(page)

    t2v_api.install_t2v_request_capture(client)
    first = client._t2v_request_capture_listener
    t2v_api.install_t2v_request_capture(client)

    assert page.removed == [("request", first)]
    assert page.listeners["request"] == [client._t2v_request_capture_listener]


def test_install_t2v_request_capture_without_page_clears_template():
    client = SimpleNamespace(page=None, _t2v_request_template={"stale": True})

    t2v_api.install_t2v_request_capture(client)

    assert client._t2v_request_template is None



def test_get_t2v_request_template_returns_none_then_dict_and_clear_resets():
    client = _client()

    assert t2v_api.get_t2v_request_template(client) is None
    client._t2v_request_template = {"url": T2V_URL}
    assert t2v_api.get_t2v_request_template(client) == {"url": T2V_URL}
    t2v_api.clear_t2v_capture(client)
    assert t2v_api.get_t2v_request_template(client) is None


def test_t2v_url_detector_is_case_insensitive_and_specific():
    assert t2v_api._is_t2v_generate_url(
        "https://aisandbox-pa.googleapis.com/v1/video:BATCHASYNCGENERATEVIDEOTEXT"
    )
    assert not t2v_api._is_t2v_generate_url(EXTEND_URL)
    assert not t2v_api._is_t2v_generate_url("https://example.test/v1/video:other")


async def test_replay_t2v_via_inflate_returns_gen_ids_in_order(monkeypatch):
    inflate = AsyncMock(
        return_value=[
            {"prompt": "p0", "gen_id": "gen-0"},
            {"prompt": "p1", "gen_id": "gen-1"},
        ]
    )
    monkeypatch.setattr(t2v_api, "submit_l1_batch_via_inflate", inflate)
    client = _client()
    prompts = ["p0", "p1"]

    gen_ids = await t2v_api.replay_t2v_via_inflate(client, prompts)

    assert gen_ids == ["gen-0", "gen-1"]
    inflate.assert_awaited_once_with(client, prompts=prompts)


async def test_replay_t2v_via_inflate_empty_prompts_skips_inflate(monkeypatch):
    inflate = AsyncMock()
    monkeypatch.setattr(t2v_api, "submit_l1_batch_via_inflate", inflate)

    assert await t2v_api.replay_t2v_via_inflate(_client(), []) == []
    inflate.assert_not_awaited()


@pytest.mark.parametrize("bad_prompts", ["one prompt", ("p0",), None])
async def test_replay_t2v_via_inflate_requires_prompt_list(bad_prompts):
    with pytest.raises(TypeError, match="prompts must be a list"):
        await t2v_api.replay_t2v_via_inflate(_client(), bad_prompts)


async def test_replay_t2v_via_inflate_requires_all_prompts_to_be_strings():
    with pytest.raises(TypeError, match=r"prompts\[1\] must be str"):
        await t2v_api.replay_t2v_via_inflate(_client(), ["p0", 123])


async def test_replay_t2v_via_inflate_raises_on_non_list_inflate_result(monkeypatch):
    monkeypatch.setattr(
        t2v_api,
        "submit_l1_batch_via_inflate",
        AsyncMock(return_value={"gen_id": "gen-0"}),
    )

    with pytest.raises(RuntimeError, match="returned non-list"):
        await t2v_api.replay_t2v_via_inflate(_client(), ["p0"])


async def test_replay_t2v_via_inflate_raises_on_partial_submission_count(monkeypatch):
    monkeypatch.setattr(
        t2v_api,
        "submit_l1_batch_via_inflate",
        AsyncMock(return_value=[{"gen_id": "gen-0"}]),
    )

    with pytest.raises(RuntimeError, match="requested 2 prompts but got 1 submissions"):
        await t2v_api.replay_t2v_via_inflate(_client(), ["p0", "p1"])


async def test_replay_t2v_via_inflate_raises_on_non_dict_submission(monkeypatch):
    monkeypatch.setattr(
        t2v_api,
        "submit_l1_batch_via_inflate",
        AsyncMock(return_value=["gen-0"]),
    )

    with pytest.raises(RuntimeError, match="submission 0 was not a dict"):
        await t2v_api.replay_t2v_via_inflate(_client(), ["p0"])


async def test_replay_t2v_via_inflate_raises_on_missing_gen_id(monkeypatch):
    monkeypatch.setattr(
        t2v_api,
        "submit_l1_batch_via_inflate",
        AsyncMock(return_value=[{"prompt": "p0", "gen_id": ""}]),
    )

    with pytest.raises(RuntimeError, match="submission 0 missing gen_id"):
        await t2v_api.replay_t2v_via_inflate(_client(), ["p0"])


async def test_poll_t2v_status_via_api_delegates_to_l1_status_poll(monkeypatch):
    poll = AsyncMock(return_value={"gen-0": {"status": "completed"}})
    monkeypatch.setattr(t2v_api, "poll_status_via_api", poll)
    client = _client()

    result = await t2v_api.poll_t2v_status_via_api(
        client,
        gen_ids=["gen-0"],
        project_id="project-1",
        poll_interval_sec=0.1,
        hard_timeout_sec=1.0,
    )

    assert result == {"gen-0": {"status": "completed"}}
    poll.assert_awaited_once_with(
        client,
        gen_ids=["gen-0"],
        project_id="project-1",
        poll_interval_sec=0.1,
        hard_timeout_sec=1.0,
    )


async def test_poll_t2v_status_via_api_empty_gen_ids_skips_poll(monkeypatch):
    poll = AsyncMock()
    monkeypatch.setattr(t2v_api, "poll_status_via_api", poll)

    assert await t2v_api.poll_t2v_status_via_api(_client(), gen_ids=[]) == {}
    poll.assert_not_awaited()


def test_download_via_url_is_reexported_for_status_poll_finalize_path():
    assert hasattr(t2v_api, "download_via_url")
    assert callable(t2v_api.download_via_url)
