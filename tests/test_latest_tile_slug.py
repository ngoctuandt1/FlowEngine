from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from flow.navigation import find_latest_tile_slug
from flow.operations import _base


PARENT_SLUG = "a" * 32
NEW_SLUG = "b" * 32
OTHER_SLUG = "c" * 32
PROJECT_ID = "d" * 32


def _edit(slug: str) -> str:
    return f"https://labs.google/fx/tools/flow/project/{PROJECT_ID}/edit/{slug}"


def _client(page):
    return SimpleNamespace(
        page=page,
        _gen_id="gen-1",
        profile_name="profile-a",
    )


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    sleep = AsyncMock()
    monkeypatch.setattr("flow.operations._base.asyncio.sleep", sleep)
    return sleep


async def test_find_latest_tile_slug_reads_last_tile_data_attr():
    page = SimpleNamespace(
        wait_for_selector=AsyncMock(),
        evaluate=AsyncMock(return_value={"slug": NEW_SLUG, "ambiguous": False}),
    )

    assert await find_latest_tile_slug(page) == NEW_SLUG
    page.wait_for_selector.assert_awaited_once_with(
        "[data-tile-id]", state="attached", timeout=3000
    )


async def test_find_latest_tile_slug_returns_none_on_ambiguity():
    page = SimpleNamespace(
        wait_for_selector=AsyncMock(),
        evaluate=AsyncMock(return_value={"slug": None, "ambiguous": True}),
    )

    assert await find_latest_tile_slug(page) is None


async def test_finalize_operation_prefers_latest_tile_when_url_stays_on_parent(monkeypatch, caplog):
    page = SimpleNamespace(url=_edit(PARENT_SLUG))
    client = _client(page)
    monkeypatch.setattr(
        _base,
        "wait_for_completion",
        AsyncMock(return_value={"done": True, "media_ids": [PARENT_SLUG]}),
    )
    monkeypatch.setattr(
        _base, "_extract_settled_route_media_id", AsyncMock(return_value=PARENT_SLUG)
    )
    monkeypatch.setattr(_base, "find_latest_tile_slug", AsyncMock(return_value=NEW_SLUG))
    monkeypatch.setattr(_base, "download_video", AsyncMock(return_value=["out.mp4"]))

    with caplog.at_level("WARNING", logger="flow.operations._base"):
        result = await _base.finalize_operation(
            client,
            {"media_id": PARENT_SLUG},
            "remove-object",
            PROJECT_ID,
            "",
            "remove",
        )

    assert result["media_id"] == NEW_SLUG
    assert result["edit_url"] == _edit(NEW_SLUG)
    warnings = [record.getMessage().lower() for record in caplog.records]
    assert any("no new network mid; using latest tile slug" in msg for msg in warnings)


async def test_finalize_operation_overrides_when_url_is_clip_route_slug(monkeypatch):
    """Flow's post-op URL often carries a clip-route slug that is neither the
    parent nor the new generation id. The latest tile in DOM is the only
    authoritative source. Live-verified on ngoctuandt20 L2 remove (2026-04-23).
    """
    page = SimpleNamespace(url=_edit(OTHER_SLUG))
    client = _client(page)
    monkeypatch.setattr(
        _base,
        "wait_for_completion",
        AsyncMock(return_value={"done": True, "media_ids": []}),
    )
    monkeypatch.setattr(
        _base, "_extract_settled_route_media_id", AsyncMock(return_value=OTHER_SLUG)
    )
    monkeypatch.setattr(_base, "find_latest_tile_slug", AsyncMock(return_value=NEW_SLUG))
    monkeypatch.setattr(_base, "download_video", AsyncMock(return_value=["out.mp4"]))

    result = await _base.finalize_operation(
        client,
        {"media_id": PARENT_SLUG},
        "remove-object",
        PROJECT_ID,
        "",
        "remove",
    )

    assert result["media_id"] == NEW_SLUG


async def test_finalize_operation_does_not_override_when_tile_slug_missing(monkeypatch):
    page = SimpleNamespace(url=_edit(PARENT_SLUG))
    client = _client(page)
    monkeypatch.setattr(
        _base,
        "wait_for_completion",
        AsyncMock(return_value={"done": True, "media_ids": [PARENT_SLUG]}),
    )
    monkeypatch.setattr(
        _base, "_extract_settled_route_media_id", AsyncMock(return_value=PARENT_SLUG)
    )
    monkeypatch.setattr(_base, "find_latest_tile_slug", AsyncMock(return_value=None))
    monkeypatch.setattr(_base, "download_video", AsyncMock(return_value=["out.mp4"]))

    result = await _base.finalize_operation(
        client,
        {"media_id": PARENT_SLUG},
        "remove-object",
        PROJECT_ID,
        "",
        "remove",
    )

    assert result["media_id"] == PARENT_SLUG
