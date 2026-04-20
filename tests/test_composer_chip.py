from unittest.mock import AsyncMock, MagicMock

import pytest

from flow.operations import frames_to_video, ingredients


def _make_locator(*, visible: bool = False):
    loc = MagicMock()
    loc.first = loc
    loc.is_visible = AsyncMock(return_value=visible)
    loc.click = AsyncMock()
    return loc


@pytest.mark.asyncio
async def test_open_composer_menu_skips_click_when_menu_already_open(monkeypatch):
    monkeypatch.setattr("asyncio.sleep", AsyncMock())

    open_chip = _make_locator(visible=True)
    fallback = _make_locator(visible=False)
    page = MagicMock()

    def _locator(selector):
        if selector == "button[aria-haspopup='menu'][data-state='open']":
            return open_chip
        if selector == "[role='menu'][data-state='open']":
            return fallback
        return _make_locator(visible=True)

    page.locator = MagicMock(side_effect=_locator)

    await frames_to_video._open_composer_menu(page)

    open_chip.click.assert_not_awaited()
    assert page.locator.call_count == 1


@pytest.mark.asyncio
async def test_open_composer_menu_clicks_chip_when_menu_closed(monkeypatch):
    monkeypatch.setattr("asyncio.sleep", AsyncMock())

    closed_state = _make_locator(visible=False)
    chip = _make_locator(visible=True)
    page = MagicMock()

    def _locator(selector):
        if selector in (
            "button[aria-haspopup='menu'][data-state='open']",
            "[role='menu'][data-state='open']",
        ):
            return closed_state
        if selector == frames_to_video.COMPOSER_MENU_SELECTORS[0]:
            return chip
        return _make_locator(visible=False)

    page.locator = MagicMock(side_effect=_locator)

    await frames_to_video._open_composer_menu(page)

    chip.click.assert_awaited_once_with(timeout=3000)


@pytest.mark.asyncio
async def test_open_composer_menu_logs_diagnostic_when_all_selectors_miss(monkeypatch, caplog):
    monkeypatch.setattr("asyncio.sleep", AsyncMock())

    closed_state = _make_locator(visible=False)
    missing = _make_locator(visible=False)
    page = MagicMock()
    page.evaluate = AsyncMock(return_value=["Nano Banana", "x1"])

    def _locator(selector):
        if selector in (
            "button[aria-haspopup='menu'][data-state='open']",
            "[role='menu'][data-state='open']",
        ):
            return closed_state
        return missing

    page.locator = MagicMock(side_effect=_locator)

    with caplog.at_level("WARNING"):
        with pytest.raises(RuntimeError, match="Could not open composer chip"):
            await frames_to_video._open_composer_menu(page)

    assert "no composer chip matched" in caplog.text
    assert "Nano Banana" in caplog.text


def test_ingredients_uses_shared_composer_menu_selectors():
    assert ingredients.COMPOSER_MENU_SELECTORS is frames_to_video.COMPOSER_MENU_SELECTORS
