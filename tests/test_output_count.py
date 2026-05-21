"""B35 — unit tests for `_set_output_count` in `flow/operations/generate.py`.

Flow's composer has a Quantity tablist (x1/x2/x3/x4) inside the model chip
panel (see `docs/FLOW_UI_REFERENCE.md` §Model Chip Panel row 4). Per-account
default is NOT always x1 — on `ngoctuandt20` it's x2, which doubles LP
credit cost AND produces ambiguous `media_id` extraction when 2 clips land
per submit. Engine must always pin x1.

Selector pattern mirrors `_set_aspect_ratio` (B1a Radix tab research):
trigger IDs end with `-trigger-{N}`. Tests use the same MagicMock /
AsyncMock shape as `test_aspect_ratio.py` — no Playwright runtime.

Source trip-wire also included: `text_to_video` must call
`_set_output_count` — prevents silent regression where someone removes the
Step 4.5 call and the engine reverts to account-default x2.
"""

import inspect
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from flow.operations.generate import _set_output_count, text_to_video


def _make_locator(**async_results):
    loc = MagicMock()
    loc.first = loc
    loc.filter = MagicMock(return_value=loc)
    loc.nth = MagicMock(return_value=loc)
    loc.click = AsyncMock(return_value=async_results.get("click"))
    loc.wait_for = AsyncMock(return_value=async_results.get("wait_for"))
    loc.inner_text = AsyncMock(return_value=async_results.get("inner_text", ""))
    loc.get_attribute = AsyncMock(return_value=async_results.get("get_attribute"))
    loc.is_visible = AsyncMock(return_value=async_results.get("is_visible", True))
    return loc


def _make_page(locator_router):
    page = MagicMock()
    page.locator = MagicMock(side_effect=locator_router)
    page.evaluate = AsyncMock(return_value=[{
        "index": 0,
        "text": "Video crop_9_16 x1",
        "iconText": ["crop_9_16"],
        "visible": True,
        "dataState": "",
        "ariaExpanded": "",
        "rect": {"top": 0, "left": 0, "width": 100, "height": 32},
    }])
    page.wait_for_function = AsyncMock()
    page.mouse = MagicMock()
    page.mouse.click = AsyncMock()
    return page


async def test_set_output_count_x1_happy_path(caplog):
    """B35: count=1 → open panel, click `-trigger-1`, close outside, verify chip.
    Chip innerText includes "x1" post-close → no WARNING."""
    chip = _make_locator(inner_text="Video crop_9_16 x1")
    menu = _make_locator()
    trigger = _make_locator()
    fallback = _make_locator()

    def route(selector, **_kwargs):
        if "aria-haspopup" in selector:
            return chip
        if '[role="menu"]' in selector:
            return menu
        if "trigger-1" in selector:
            return trigger
        return fallback

    page = _make_page(route)

    with caplog.at_level(logging.INFO, logger="flow.operations.generate"):
        await _set_output_count(page, 1)

    chip.click.assert_called()                    # opened panel
    trigger.click.assert_called()                 # clicked x1 trigger
    page.mouse.click.assert_called_with(10, 10)   # click-outside
    chip.inner_text.assert_called()               # verified

    warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert not any("verify failed" in m.lower() for m in warnings), (
        f"Unexpected chip verify warning: {warnings}"
    )
    infos = [r.getMessage() for r in caplog.records if r.levelname == "INFO"]
    assert any("x1" in m and "verified" in m for m in infos), (
        f"Expected INFO log mentioning x1 + verified, got: {infos}"
    )


async def test_set_output_count_skips_open_when_already_open():
    """Radix `data-state='open'` pre-state → skip open-click (mirror B19
    aspect-chip guard; avoids toggle-close when a prior interaction left
    the panel open)."""
    chip = _make_locator(inner_text="Video crop_9_16 x1", get_attribute="open")
    menu = _make_locator()
    trigger = _make_locator()
    fallback = _make_locator()

    def route(selector, **_kwargs):
        if "aria-haspopup" in selector:
            return chip
        if '[role="menu"]' in selector:
            return menu
        if "trigger-1" in selector:
            return trigger
        return fallback

    page = _make_page(route)
    page.evaluate = AsyncMock(return_value=[{
        "index": 0,
        "text": "Video crop_9_16 x1",
        "iconText": ["crop_9_16"],
        "visible": True,
        "dataState": "open",
        "ariaExpanded": "true",
        "rect": {"top": 0, "left": 0, "width": 100, "height": 32},
    }])
    await _set_output_count(page, 1)

    # Only the trigger.click should fire — not the chip.click (panel was open)
    chip.click.assert_not_called()
    trigger.click.assert_called()


async def test_set_output_count_chip_verify_failure_warns(caplog):
    """If chip innerText post-close does NOT contain 'x{count}' → WARNING,
    no raise. Prevents silent regression where the click appeared to succeed
    but Flow ignored it."""
    chip = _make_locator(inner_text="Video crop_9_16 x2")  # verify fails
    menu = _make_locator()
    trigger = _make_locator()
    fallback = _make_locator()

    def route(selector, **_kwargs):
        if "aria-haspopup" in selector:
            return chip
        if '[role="menu"]' in selector:
            return menu
        if "trigger-1" in selector:
            return trigger
        return fallback

    page = _make_page(route)

    with caplog.at_level(logging.WARNING, logger="flow.operations.generate"):
        await _set_output_count(page, 1)

    warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert any(
        "verify failed" in m.lower() and "x1" in m for m in warnings
    ), f"Expected verify-failure WARNING, got: {warnings}"


def test_set_output_count_rejects_out_of_range():
    """B35: count must be 1..4 — Flow only exposes these. 0 or 5 → ValueError
    fail-fast, no DOM touch."""
    import asyncio
    page = MagicMock()

    with pytest.raises(ValueError, match="1..4"):
        asyncio.run(_set_output_count(page, 0))
    with pytest.raises(ValueError, match="1..4"):
        asyncio.run(_set_output_count(page, 5))


def test_text_to_video_calls_set_output_count():
    """B35 source trip-wire: `text_to_video` MUST call `_set_output_count`
    in its body. Prevents silent regression where someone removes the
    Step 4.5 call and engine reverts to account-default (often x2 =
    credit leak).

    Evidence for the trip-wire: Run 10 + Run 12 + earlier Tier 2 runs
    all submitted x2 (per user screenshot 2026-04-19) because the
    pre-B35 pipeline relied on the Flow account default — burned
    ~2× LP across every run without being caught."""
    src = inspect.getsource(text_to_video)
    assert "_set_output_count(" in src, (
        "B35: text_to_video must call _set_output_count before submit. "
        "Removing this call re-opens the x2 credit leak."
    )
