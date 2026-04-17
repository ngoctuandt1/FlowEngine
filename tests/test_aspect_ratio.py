"""B1b — unit tests for `_set_aspect_ratio` in `flow/operations/generate.py`.

Flow Video exposes only 9:16 / 16:9 via a Radix chip panel (see
`docs/FLOW_UI_REFERENCE.md` §Aspect Ratio UI, B1a research report). 16:9 is
default, so the helper short-circuits for 16:9 and any unsupported value
(e.g. "1:1" — image-only). 9:16 triggers the real open-panel → click-tab →
close-outside → verify-chip sequence.

Tests mock `page` with `AsyncMock`/`MagicMock` — no Playwright runtime here.
The manual E2E (POST job with aspect_ratio="9:16", verify output video is
portrait) is a supervisor-side check after merge.
"""

import logging
from unittest.mock import AsyncMock, MagicMock

from flow.operations.generate import _set_aspect_ratio


def _make_locator(**async_results):
    """Build a MagicMock shaped like a Playwright Locator.

    `.first` and `.filter(...)` chain back to self so nested access works.
    Async methods (`click`, `wait_for`, `inner_text`, `get_attribute`,
    `is_visible`) are `AsyncMock`s whose return values come from kwargs.
    """
    loc = MagicMock()
    loc.first = loc
    loc.filter = MagicMock(return_value=loc)
    loc.click = AsyncMock(return_value=async_results.get("click"))
    loc.wait_for = AsyncMock(return_value=async_results.get("wait_for"))
    loc.inner_text = AsyncMock(return_value=async_results.get("inner_text", ""))
    loc.get_attribute = AsyncMock(return_value=async_results.get("get_attribute"))
    loc.is_visible = AsyncMock(return_value=async_results.get("is_visible", True))
    return loc


def _make_page(locator_router):
    page = MagicMock()
    page.locator = MagicMock(side_effect=locator_router)
    page.wait_for_function = AsyncMock()
    page.mouse = MagicMock()
    page.mouse.click = AsyncMock()
    return page


async def test_default_ratio_no_interaction(caplog):
    """ratio='16:9' → early return, no DOM calls, INFO log mentions default."""
    page = MagicMock()
    page.locator = MagicMock()
    page.mouse = MagicMock()
    page.mouse.click = AsyncMock()
    page.wait_for_function = AsyncMock()

    with caplog.at_level(logging.INFO, logger="flow.operations.generate"):
        await _set_aspect_ratio(page, "16:9")

    page.locator.assert_not_called()
    page.mouse.click.assert_not_called()
    page.wait_for_function.assert_not_called()

    info_messages = [r.getMessage() for r in caplog.records if r.levelname == "INFO"]
    assert any(
        "default" in m.lower() and "16:9" in m for m in info_messages
    ), f"Expected INFO log mentioning default 16:9, got: {info_messages}"


async def test_unsupported_ratio_logs_warning(caplog):
    """ratio='1:1' (image-only) → WARNING, no DOM touch, fall back to default."""
    page = MagicMock()
    page.locator = MagicMock()
    page.mouse = MagicMock()
    page.mouse.click = AsyncMock()
    page.wait_for_function = AsyncMock()

    with caplog.at_level(logging.WARNING, logger="flow.operations.generate"):
        await _set_aspect_ratio(page, "1:1")

    page.locator.assert_not_called()
    page.mouse.click.assert_not_called()
    page.wait_for_function.assert_not_called()

    warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert any(
        "unsupported" in m.lower() and "1:1" in m for m in warnings
    ), f"Expected WARNING about '1:1' unsupported, got: {warnings}"


async def test_portrait_opens_panel_and_clicks_trigger(caplog):
    """ratio='9:16' → open chip, click PORTRAIT, close outside, verify chip."""
    chip = _make_locator(inner_text="Video crop_9_16 x1")
    menu = _make_locator()
    video_tab = _make_locator(get_attribute="active")
    portrait_trigger = _make_locator()
    fallback = _make_locator()

    def route(selector, **_kwargs):
        if "aria-haspopup" in selector:
            return chip
        if '[role="menu"]' in selector:
            return menu
        if "trigger-VIDEO" in selector:
            return video_tab
        if "trigger-PORTRAIT" in selector:
            return portrait_trigger
        return fallback

    page = _make_page(route)

    with caplog.at_level(logging.INFO, logger="flow.operations.generate"):
        await _set_aspect_ratio(page, "9:16")

    chip.click.assert_called()
    portrait_trigger.click.assert_called()
    page.mouse.click.assert_called_with(10, 10)
    chip.inner_text.assert_called()

    warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert not any(
        "verify failed" in m.lower() for m in warnings
    ), f"Unexpected chip verify warning: {warnings}"

    info_messages = [r.getMessage() for r in caplog.records if r.levelname == "INFO"]
    assert any(
        "9:16" in m and "crop_9_16" in m for m in info_messages
    ), f"Expected success INFO log mentioning '9:16' and 'crop_9_16', got: {info_messages}"
