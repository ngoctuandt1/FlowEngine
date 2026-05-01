"""Unit tests for LP -> Lite fallback in the free model selector path."""

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from flow import model_selector as model_selector_mod
from flow.model_selector import select_model


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """Stub selector sleeps so retry logic stays fast in unit tests."""
    monkeypatch.setattr("asyncio.sleep", AsyncMock())


def _make_filtered_locator(texts: list[str], clicked_texts: list[str]):
    locator = MagicMock()
    locator.count = AsyncMock(return_value=len(texts))

    first = MagicMock()
    first.is_visible = AsyncMock(return_value=bool(texts))
    if texts:
        first.inner_text = AsyncMock(return_value=texts[0])
        first.click = AsyncMock(
            side_effect=lambda *args, _text=texts[0], **kwargs: clicked_texts.append(_text)
        )
    else:
        first.inner_text = AsyncMock(return_value="")
        first.click = AsyncMock(side_effect=AssertionError("No dropdown item should be clicked"))
    locator.first = first

    def _nth(index: int):
        item = MagicMock()
        text = texts[index]
        item.inner_text = AsyncMock(return_value=text)
        item.click = AsyncMock(
            side_effect=lambda *args, _text=text, **kwargs: clicked_texts.append(_text)
        )
        item.is_visible = AsyncMock(return_value=True)
        return item

    locator.nth = MagicMock(side_effect=_nth)
    return locator


def _make_select_model_page(option_texts: list[str]):
    page = MagicMock()
    clicked_texts: list[str] = []

    chip = MagicMock()
    chip.first = chip
    chip.is_visible = AsyncMock(return_value=True)
    chip.click = AsyncMock(return_value=None)
    chip.inner_text = AsyncMock(return_value="Veo 3.1 - Fast x1")

    model_items_loc = MagicMock()
    model_items_loc.count = AsyncMock(return_value=len(option_texts))

    def _filter(*, has_text):
        matches = [text for text in option_texts if has_text.search(text)]
        return _make_filtered_locator(matches, clicked_texts)

    model_items_loc.filter = MagicMock(side_effect=_filter)

    def _locator(selector: str):
        if "menuitem" in selector and "role='menuitem'" in selector:
            return model_items_loc
        return chip

    page.locator = MagicMock(side_effect=_locator)
    page.evaluate = AsyncMock(return_value=False)
    page.keyboard = MagicMock()
    page.keyboard.press = AsyncMock(return_value=None)
    page._clicked_texts = clicked_texts  # type: ignore[attr-defined]
    return page


@pytest.fixture
def _selector_stubs(monkeypatch):
    open_dropdown = AsyncMock(return_value=True)
    verify_credits = AsyncMock(return_value=True)
    close_panel = AsyncMock(return_value=None)

    monkeypatch.setattr(model_selector_mod, "_ensure_video_mode", AsyncMock(return_value=None))
    monkeypatch.setattr(model_selector_mod, "_switch_to_video_tab", AsyncMock(return_value=True))
    monkeypatch.setattr(model_selector_mod, "_open_model_dropdown", open_dropdown)
    monkeypatch.setattr(model_selector_mod, "_verify_credits", verify_credits)
    monkeypatch.setattr(model_selector_mod, "_close_model_panel", close_panel)
    monkeypatch.setattr(model_selector_mod, "_debug_model_options", AsyncMock(return_value=None))

    return {
        "open_dropdown": open_dropdown,
        "verify_credits": verify_credits,
        "close_panel": close_panel,
    }


async def test_select_model_prefers_lower_priority_when_only_lp_exists(
    _selector_stubs, caplog
):
    page = _make_select_model_page(["Veo 3.1 - Fast [Lower Priority]"])

    with caplog.at_level(logging.WARNING, logger="flow.model_selector"):
        result = await select_model(page, model="veo-3.1-fast-lp")

    assert result is True
    assert page._clicked_texts == ["Veo 3.1 - Fast [Lower Priority]"]
    assert "falling back to Lite" not in caplog.text
    _selector_stubs["open_dropdown"].assert_not_called()
    _selector_stubs["verify_credits"].assert_awaited_once_with(page, expected=0)


async def test_select_model_falls_back_to_lite_when_lp_is_missing(
    _selector_stubs, caplog
):
    page = _make_select_model_page(["Veo 3.1 - Lite"])

    with caplog.at_level(logging.WARNING, logger="flow.model_selector"):
        result = await select_model(page, model="veo-3.1-fast-lp")

    assert result is True
    assert page._clicked_texts == ["Veo 3.1 - Lite"]
    assert "LP option not found, falling back to Lite" in caplog.text
    _selector_stubs["open_dropdown"].assert_awaited_once()
    _selector_stubs["verify_credits"].assert_awaited_once_with(page, expected=0)


async def test_select_model_prefers_lp_when_lp_and_lite_both_exist(
    _selector_stubs, caplog
):
    page = _make_select_model_page(
        ["Veo 3.1 - Fast [Lower Priority]", "Veo 3.1 - Lite"]
    )

    with caplog.at_level(logging.WARNING, logger="flow.model_selector"):
        result = await select_model(page, model="veo-3.1-fast-lp")

    assert result is True
    assert page._clicked_texts == ["Veo 3.1 - Fast [Lower Priority]"]
    assert "falling back to Lite" not in caplog.text
    _selector_stubs["open_dropdown"].assert_not_called()
    _selector_stubs["verify_credits"].assert_awaited_once_with(page, expected=0)


async def test_select_model_raises_when_neither_lp_nor_lite_exists(
    _selector_stubs,
):
    page = _make_select_model_page(["Veo Quality", "Veo Imagen"])

    with pytest.raises(
        RuntimeError,
        match="Neither Lower Priority nor Lite model found",
    ):
        await select_model(page, model="veo-3.1-fast-lp")

    assert page._clicked_texts == []
    _selector_stubs["open_dropdown"].assert_awaited_once()
    _selector_stubs["verify_credits"].assert_not_called()
