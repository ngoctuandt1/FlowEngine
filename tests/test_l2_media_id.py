from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from flow.operations import _base
from flow.wait import _collect_media_ids, _settle_after_done


OLD_SLUG = "a" * 32
NEW_SLUG = "b" * 32
ALT_SLUG = "c" * 32
PROJECT_ID = "d" * 32


class StickyURLPage:
    def __init__(self, *urls):
        self._urls = list(urls)
        self._last = self._urls[-1]

    @property
    def url(self):
        if self._urls:
            self._last = self._urls.pop(0)
        return self._last


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    sleep = AsyncMock()
    monkeypatch.setattr("flow.wait.asyncio.sleep", sleep)
    monkeypatch.setattr("flow.operations._base.asyncio.sleep", sleep)
    return sleep


def _edit(slug: str) -> str:
    return f"https://labs.google/fx/tools/flow/project/{PROJECT_ID}/edit/{slug}"


def _client(page, media_events=None):
    return SimpleNamespace(
        page=page,
        _media_id_events=list(media_events or []),
        _gen_id="gen-1",
        profile_name="profile-a",
    )


def _clock(*values):
    seq = list(values)
    last = seq[-1]

    def _next():
        nonlocal seq
        if seq:
            last_value = seq.pop(0)
            return last_value
        return last

    return _next


async def test_settle_after_done_returns_when_url_changes(monkeypatch, _no_sleep):
    page = StickyURLPage(_edit(OLD_SLUG), _edit(NEW_SLUG))
    client = _client(page)
    monkeypatch.setattr("flow.wait.time.monotonic", _clock(0.0, 0.1, 0.2))

    await _settle_after_done(page, client, _edit(OLD_SLUG), 0)

    assert _no_sleep.await_count == 1


async def test_settle_after_done_returns_when_media_event_arrives(monkeypatch, _no_sleep):
    page = StickyURLPage(_edit(OLD_SLUG), _edit(OLD_SLUG))
    client = _client(page)
    _no_sleep.side_effect = lambda *_: client._media_id_events.append({"mid": "redirect"})
    monkeypatch.setattr("flow.wait.time.monotonic", _clock(0.0, 0.1, 0.2))

    await _settle_after_done(page, client, _edit(OLD_SLUG), 0)

    assert len(client._media_id_events) == 1
    assert _no_sleep.await_count == 1


async def test_settle_after_done_stops_at_deadline(monkeypatch, _no_sleep):
    page = StickyURLPage(_edit(OLD_SLUG), _edit(OLD_SLUG))
    client = _client(page)
    monkeypatch.setattr("flow.wait.time.monotonic", _clock(0.0, 0.1, 3.1))

    await _settle_after_done(page, client, _edit(OLD_SLUG), 0)

    assert _no_sleep.await_count == 1


async def test_collect_media_ids_respects_start_index():
    client = _client(
        StickyURLPage(_edit(OLD_SLUG)),
        media_events=[
            {"mid": "old"},
            {"media_id": "skip"},
            {"mid": "new-1"},
            {"media_id": "new-2"},
        ],
    )

    assert set(_collect_media_ids(client, start_index=2)) == {"new-1", "new-2"}


async def test_extract_settled_route_media_id_returns_route_slug():
    page = StickyURLPage(_edit(NEW_SLUG))

    assert await _base._extract_settled_route_media_id(page, fallback=OLD_SLUG) == NEW_SLUG


async def test_extract_settled_route_media_id_falls_back_after_polling(_no_sleep):
    page = StickyURLPage("https://labs.google/fx/tools/flow/project/no-edit")

    assert await _base._extract_settled_route_media_id(page, fallback=OLD_SLUG) == OLD_SLUG
    assert _no_sleep.await_count == 12


async def test_finalize_operation_prefers_network_media_id_for_chain_and_download(monkeypatch):
    page = StickyURLPage(_edit(NEW_SLUG))
    client = _client(page)
    download = AsyncMock(return_value=["out.mp4"])
    monkeypatch.setattr(
        _base,
        "wait_for_completion",
        AsyncMock(return_value={"done": True, "media_ids": [NEW_SLUG]}),
    )
    monkeypatch.setattr(_base, "download_video", download)

    result = await _base.finalize_operation(client, {"media_id": OLD_SLUG}, "insert-object", PROJECT_ID, "", "insert")

    assert result["media_id"] == NEW_SLUG
    assert result["edit_url"] == _edit(NEW_SLUG)
    assert download.await_args.kwargs["media_ids"] == [NEW_SLUG]


async def test_finalize_operation_edit_url_fallback_uses_settled_current_url(monkeypatch):
    first_url = f"https://labs.google/fx/tools/flow/project/{PROJECT_ID}/preview/first"
    second_url = f"https://labs.google/fx/tools/flow/project/{PROJECT_ID}/preview/second"
    page = StickyURLPage(first_url, second_url)
    client = _client(page)
    monkeypatch.setattr(_base, "wait_for_completion", AsyncMock(return_value={"done": True, "media_ids": []}))
    monkeypatch.setattr(_base, "download_video", AsyncMock(return_value=["x.mp4"]))

    result = await _base.finalize_operation(client, {"media_id": OLD_SLUG}, "insert-object", "", "", "insert")

    assert result["media_id"] == OLD_SLUG
    assert result["edit_url"] == first_url


async def test_finalize_operation_serial_ops_keep_distinct_network_media_ids(monkeypatch):
    page = MagicMock()
    page.url = _edit(NEW_SLUG)
    client = _client(page)
    monkeypatch.setattr(
        _base,
        "wait_for_completion",
        AsyncMock(
            side_effect=[
                {"done": True, "media_ids": [NEW_SLUG]},
                {"done": True, "media_ids": [ALT_SLUG]},
            ]
        ),
    )
    monkeypatch.setattr(_base, "download_video", AsyncMock(side_effect=[["insert.mp4"], ["remove.mp4"]]))

    insert = await _base.finalize_operation(client, {"media_id": OLD_SLUG}, "insert-object", PROJECT_ID, "", "insert")
    page.url = _edit(ALT_SLUG)
    remove = await _base.finalize_operation(client, {"media_id": OLD_SLUG}, "remove-object", PROJECT_ID, "", "remove")

    assert insert["media_id"] == NEW_SLUG
    assert remove["media_id"] == ALT_SLUG
    assert insert["media_id"] != remove["media_id"]


async def test_finalize_operation_uses_latest_tile_when_route_missing(monkeypatch):
    page = StickyURLPage(f"https://labs.google/fx/tools/flow/project/{PROJECT_ID}/preview")
    client = _client(page)
    monkeypatch.setattr(_base, "wait_for_completion", AsyncMock(return_value={"done": True, "media_ids": []}))
    monkeypatch.setattr(_base, "download_video", AsyncMock(return_value=["x.mp4"]))
    monkeypatch.setattr(_base, "find_latest_tile_slug", AsyncMock(return_value=NEW_SLUG))

    result = await _base.finalize_operation(client, {"media_id": OLD_SLUG}, "insert-object", PROJECT_ID, "", "insert")

    assert result["media_id"] == NEW_SLUG
    assert result["edit_url"] == _edit(NEW_SLUG)
