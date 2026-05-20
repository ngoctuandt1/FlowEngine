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

import inspect
import logging
import re
from unittest.mock import AsyncMock, MagicMock

from flow.operations import generate as _generate_module
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


async def test_portrait_skips_chip_click_when_already_open():
    """B19 — when the chip's Radix `data-state` is already 'open', skip
    the open-click. A preceding interaction (model-selector dismiss,
    focus-trap reset) can leave the aspect chip trigger in the open
    state before we arrive. Clicking it again would TOGGLE the menu
    CLOSED and cause the subsequent `[role="menu"][data-state="open"]`
    wait to time out — exactly the failure symptom seen live in
    Tier 2 Runs 3-6 (docs/E2E_RESULTS_PHASE_A.md).

    Invariant: `chip.get_attribute('data-state')` == 'open' → skip
    `chip.click()`, but still run tab + trigger + outside-click +
    verify.
    """
    chip = _make_locator(inner_text="Video crop_9_16 x1", get_attribute="open")
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
    page.evaluate = AsyncMock(return_value=[{
        "index": 0,
        "text": "Video crop_9_16 x1",
        "iconText": ["crop_9_16"],
        "visible": True,
        "dataState": "open",
        "ariaExpanded": "true",
        "rect": {"top": 0, "left": 0, "width": 100, "height": 32},
    }])
    await _set_aspect_ratio(page, "9:16")

    chip.click.assert_not_called()  # the core B19 invariant
    portrait_trigger.click.assert_called()
    page.mouse.click.assert_called_with(10, 10)
    chip.inner_text.assert_called()


def test_chip_selector_uses_role_text_menu_discovery_not_model_text():
    """Aspect-ratio chip must be opened by role/text/menu discovery.

    Pre-B19 helper located the chip via ``video.*x`` text. Later fixes used
    exact crop icon ligatures. Unit B's 2026-05 contract is stricter: open
    ``button[aria-haspopup="menu"]`` candidates filtered by visible current
    mode/model/count text and require the Radix menu to open. Crop icon text
    may still appear in diagnostics and final ratio verification, but it is
    not the menu-open acceptance criterion.
    """
    # (1) Source trip-wire on the actual helper.
    src = inspect.getsource(_generate_module._set_aspect_ratio)
    assert "_open_composer_menu_by_role_text" in src, (
        "Expected `_set_aspect_ratio` to open the composer chip via "
        "role/text/menu discovery. Source:\n" + src
    )
    assert 'aria-haspopup="menu"' in src, (
        "Expected chip selector to still scope to "
        '`button[aria-haspopup="menu"]` (the Radix chip role). '
        "Source:\n" + src
    )
    assert "video.*x" not in src.lower().replace(" ", ""), (
        "Found lingering `video.*x\\d`-style text probe in "
        "`_set_aspect_ratio` — B19 removed the model-name text "
        "dependency. Source:\n" + src
    )

    # (2) Pre-B19 RED-case: old regex fails against live Run 3 chip text.
    live_chip_text_run3 = "\U0001f34c Nano Banana Pro\ncrop_9_16\nx1"
    pre_fix = re.compile(r"video.*x\d", re.IGNORECASE | re.DOTALL)
    assert pre_fix.search(live_chip_text_run3) is None, (
        "Pre-B19 regex unexpectedly matched the Tier 2 Run 3 chip "
        f"text {live_chip_text_run3!r}. If this matches, the B19 "
        "rationale (model name varies, 'video' missing) is invalid."
    )

    # (3) Ligature invariance: the icon-text regex matches every variant.
    ligature_re = re.compile(r"^crop_(9_16|16_9)$")
    for ligature in ("crop_9_16", "crop_16_9"):
        assert ligature_re.match(ligature), (
            f"Icon-text regex `^crop_(9_16|16_9)$` should match "
            f"ligature {ligature!r} (the inner text of "
            "`<i class='google-symbols'>`)."
        )

    # And it should NOT swallow unrelated Material Icon tokens
    # (e.g. `add_2`, `crop_free`) that may co-exist on the page.
    for unrelated in ("add_2", "crop_free", "crop_rotate", "video_library"):
        assert ligature_re.match(unrelated) is None, (
            f"Icon-text regex unexpectedly matched unrelated token "
            f"{unrelated!r} — the selector should be scoped to the "
            "two supported aspect-ratio ligatures only."
        )
