from unittest.mock import AsyncMock, MagicMock

import pytest

from flow.ai_locator import AILocatorResult
from flow.edit_menu import _EDIT_VIEW_KEBAB_AI_CACHE_KEY, open_edit_view_kebab


@pytest.mark.asyncio
async def test_edit_view_kebab_fast_path_skips_ai(monkeypatch):
    monkeypatch.setenv("FLOW_AI_LOCATOR_ENABLED", "true")
    ai_spy = AsyncMock()
    monkeypatch.setattr("flow.ai_locator.ai_locate", ai_spy)

    button = MagicMock()
    button.first = button
    button.is_visible = AsyncMock(return_value=True)
    button.click = AsyncMock()
    page = MagicMock()
    page.locator.return_value = button

    assert await open_edit_view_kebab(page) is True
    button.click.assert_awaited_once_with(timeout=3000)
    ai_spy.assert_not_awaited()


@pytest.mark.asyncio
async def test_edit_view_kebab_ai_fallback_uses_cache_key(monkeypatch):
    monkeypatch.setenv("FLOW_AI_LOCATOR_ENABLED", "true")
    ai_spy = AsyncMock(
        return_value=AILocatorResult(
            selector="#kebab",
            coordinates=None,
            method="ai",
            cost_estimate=0.0,
            debug_log=[],
        )
    )
    monkeypatch.setattr("flow.ai_locator.ai_locate", ai_spy)

    missing = MagicMock()
    missing.first = missing
    missing.is_visible = AsyncMock(return_value=False)
    kebab = MagicMock()
    kebab.first = kebab
    kebab.click = AsyncMock()

    page = MagicMock()

    def _locator(selector):
        if selector == "#kebab":
            return kebab
        return missing

    page.locator = MagicMock(side_effect=_locator)

    assert await open_edit_view_kebab(page) is True
    kebab.click.assert_awaited_once_with(timeout=3000)
    ai_spy.assert_awaited_once()
    assert ai_spy.await_args.kwargs["cache_key"] == _EDIT_VIEW_KEBAB_AI_CACHE_KEY


@pytest.mark.asyncio
async def test_edit_view_kebab_ai_disabled_and_miss_return_false(monkeypatch):
    ai_spy = AsyncMock()
    monkeypatch.setattr("flow.ai_locator.ai_locate", ai_spy)

    missing = MagicMock()
    missing.first = missing
    missing.is_visible = AsyncMock(return_value=False)
    page = MagicMock()
    page.locator.return_value = missing

    assert await open_edit_view_kebab(page) is False
    ai_spy.assert_not_awaited()

    monkeypatch.setenv("FLOW_AI_LOCATOR_ENABLED", "true")
    ai_spy.return_value = AILocatorResult(
        selector=None,
        coordinates=None,
        method="miss",
        cost_estimate=0.0,
        debug_log=["ai_not_found"],
    )

    assert await open_edit_view_kebab(page) is False
    assert ai_spy.await_count == 1
