from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from flow.operations import _base


PARENT_SLUG = "a" * 32
NEW_SLUG = "b" * 32
FALLBACK_SLUG = "c" * 32
PROJECT_ID = "d" * 32


def _edit(slug: str) -> str:
    return f"https://labs.google/fx/tools/flow/project/{PROJECT_ID}/edit/{slug}"


@pytest.fixture
def page():
    return SimpleNamespace(url=_edit(PARENT_SLUG))


@pytest.fixture
def capture_failure(monkeypatch):
    capture = AsyncMock(return_value=None)
    monkeypatch.setattr("flow.diagnostics.capture_failure", capture)
    return capture


async def test_strict_parent_only_download_id_raises_before_tile_lookup(
    monkeypatch, page, capture_failure
):
    find_latest_tile_slug = AsyncMock(return_value=NEW_SLUG)
    monkeypatch.setattr(_base, "find_latest_tile_slug", find_latest_tile_slug)
    monkeypatch.setattr(_base, "extract_media_id", MagicMock(return_value=PARENT_SLUG))

    with pytest.raises(RuntimeError, match="chain_child_no_new_media") as exc_info:
        await _base.resolve_final_media_id(
            page,
            parent_media_id=PARENT_SLUG,
            download_media_ids=[PARENT_SLUG],
            strict=True,
        )

    assert "parent=" + PARENT_SLUG[:20] in str(exc_info.value)
    assert "tile_observed=None" in str(exc_info.value)
    find_latest_tile_slug.assert_not_awaited()
    capture_failure.assert_awaited_once_with(
        page,
        kind="chain_child_no_new_media",
        logger=_base.logger,
    )


async def test_strict_network_new_media_wins_without_capture_or_tile(
    monkeypatch, page, capture_failure
):
    find_latest_tile_slug = AsyncMock(return_value=NEW_SLUG)
    monkeypatch.setattr(_base, "find_latest_tile_slug", find_latest_tile_slug)
    monkeypatch.setattr(_base, "extract_media_id", MagicMock(return_value=PARENT_SLUG))

    result = await _base.resolve_final_media_id(
        page,
        parent_media_id=PARENT_SLUG,
        download_media_ids=[PARENT_SLUG, NEW_SLUG],
        strict=True,
    )

    assert result == NEW_SLUG
    find_latest_tile_slug.assert_not_awaited()
    capture_failure.assert_not_awaited()


async def test_strict_empty_download_ids_raises_and_captures(
    monkeypatch, page, capture_failure
):
    find_latest_tile_slug = AsyncMock(return_value=NEW_SLUG)
    monkeypatch.setattr(_base, "find_latest_tile_slug", find_latest_tile_slug)
    monkeypatch.setattr(_base, "extract_media_id", MagicMock(return_value=PARENT_SLUG))

    with pytest.raises(RuntimeError, match="chain_child_no_new_media"):
        await _base.resolve_final_media_id(
            page,
            parent_media_id=PARENT_SLUG,
            download_media_ids=[],
            strict=True,
        )

    find_latest_tile_slug.assert_not_awaited()
    capture_failure.assert_awaited_once_with(
        page,
        kind="chain_child_no_new_media",
        logger=_base.logger,
    )


async def test_non_strict_parent_keeps_legacy_tile_fallback(monkeypatch, page, capture_failure):
    find_latest_tile_slug = AsyncMock(return_value=NEW_SLUG)
    extract_media_id = MagicMock(return_value=PARENT_SLUG)
    monkeypatch.setattr(_base, "find_latest_tile_slug", find_latest_tile_slug)
    monkeypatch.setattr(_base, "extract_media_id", extract_media_id)

    result = await _base.resolve_final_media_id(
        page,
        parent_media_id=PARENT_SLUG,
        download_media_ids=[],
        strict=False,
    )

    assert result == NEW_SLUG
    find_latest_tile_slug.assert_awaited_once_with(page)
    extract_media_id.assert_called_once_with(page.url)
    capture_failure.assert_not_awaited()


async def test_non_strict_without_parent_keeps_legacy_route_fallback(
    monkeypatch, page, capture_failure
):
    find_latest_tile_slug = AsyncMock(return_value=None)
    extract_route = AsyncMock(return_value=FALLBACK_SLUG)
    monkeypatch.setattr(_base, "find_latest_tile_slug", find_latest_tile_slug)
    monkeypatch.setattr(_base, "extract_media_id", MagicMock(return_value=None))
    monkeypatch.setattr(_base, "_extract_settled_route_media_id", extract_route)

    result = await _base.resolve_final_media_id(
        page,
        fallback=FALLBACK_SLUG,
        parent_media_id=None,
        download_media_ids=[],
        strict=False,
    )

    assert result == FALLBACK_SLUG
    find_latest_tile_slug.assert_awaited_once_with(page)
    extract_route.assert_awaited_once_with(page, fallback=FALLBACK_SLUG)
    capture_failure.assert_not_awaited()


async def test_strict_without_parent_does_not_raise(monkeypatch, page, capture_failure):
    find_latest_tile_slug = AsyncMock(return_value=None)
    extract_route = AsyncMock(return_value=FALLBACK_SLUG)
    monkeypatch.setattr(_base, "find_latest_tile_slug", find_latest_tile_slug)
    monkeypatch.setattr(_base, "extract_media_id", MagicMock(return_value=None))
    monkeypatch.setattr(_base, "_extract_settled_route_media_id", extract_route)

    result = await _base.resolve_final_media_id(
        page,
        fallback=FALLBACK_SLUG,
        parent_media_id=None,
        download_media_ids=[],
        strict=True,
    )

    assert result == FALLBACK_SLUG
    find_latest_tile_slug.assert_awaited_once_with(page)
    extract_route.assert_awaited_once_with(page, fallback=FALLBACK_SLUG)
    capture_failure.assert_not_awaited()
