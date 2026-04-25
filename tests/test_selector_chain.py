"""Unit tests for flow.selector_chain.click_first_visible."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from flow.selector_chain import click_first_visible


def _make_page(visibility_map: dict[str, bool], click_raises: dict[str, Exception] | None = None):
    """Build a mock Page whose locator(sel).first is a MagicMock with
    is_visible / click coroutines wired from the per-selector maps.
    """
    click_raises = click_raises or {}
    locators_seen: list[str] = []

    def _locator_factory(sel: str):
        loc = MagicMock()
        first = MagicMock()
        first.is_visible = AsyncMock(return_value=visibility_map.get(sel, False))
        if sel in click_raises:
            first.click = AsyncMock(side_effect=click_raises[sel])
        else:
            first.click = AsyncMock()
        loc.first = first
        return loc

    page = MagicMock()
    page.locator = MagicMock(side_effect=lambda sel: (locators_seen.append(sel) or _locator_factory(sel)))
    page._locators_seen = locators_seen
    return page


async def test_returns_first_visible_selector_and_clicks():
    page = _make_page({"A": False, "B": True, "C": True})
    matched = await click_first_visible(page, ["A", "B", "C"])
    assert matched == "B"
    assert page._locators_seen == ["A", "B"]  # short-circuit after B


async def test_returns_none_when_nothing_visible():
    page = _make_page({"A": False, "B": False})
    matched = await click_first_visible(page, ["A", "B"])
    assert matched is None
    assert page._locators_seen == ["A", "B"]


async def test_swallows_per_selector_exception_and_continues():
    page = _make_page(
        {"A": True, "B": True},
        click_raises={"A": RuntimeError("playwright timeout")},
    )
    matched = await click_first_visible(page, ["A", "B"])
    # A is visible but click raised → fall through to B
    assert matched == "B"


async def test_passes_visibility_timeout_arg():
    captured: list[int] = []

    def _factory(sel: str):
        loc = MagicMock()
        first = MagicMock()

        async def _is_visible(timeout=None):
            captured.append(timeout)
            return True

        first.is_visible = _is_visible
        first.click = AsyncMock()
        loc.first = first
        return loc

    page = MagicMock()
    page.locator = MagicMock(side_effect=_factory)
    await click_first_visible(page, ["A"], is_visible_timeout_ms=2500, click_timeout_ms=4000)
    assert captured == [2500]


async def test_passes_click_timeout_arg():
    captured: list[int] = []

    def _factory(sel: str):
        loc = MagicMock()
        first = MagicMock()
        first.is_visible = AsyncMock(return_value=True)

        async def _click(timeout=None):
            captured.append(timeout)

        first.click = _click
        loc.first = first
        return loc

    page = MagicMock()
    page.locator = MagicMock(side_effect=_factory)
    await click_first_visible(page, ["A"], is_visible_timeout_ms=1000, click_timeout_ms=3500)
    assert captured == [3500]


async def test_invokes_on_match_with_selector():
    page = _make_page({"A": False, "B": True})
    seen: list[str] = []
    matched = await click_first_visible(page, ["A", "B"], on_match=seen.append)
    assert matched == "B"
    assert seen == ["B"]


async def test_on_match_exception_does_not_break_return():
    page = _make_page({"A": True})

    def boom(_):
        raise RuntimeError("logging blew up")

    matched = await click_first_visible(page, ["A"], on_match=boom)
    assert matched == "A"  # callback failure must not mask the click success


async def test_empty_selector_list_returns_none():
    page = _make_page({})
    matched = await click_first_visible(page, [])
    assert matched is None
    assert page._locators_seen == []
