"""B16 — unit tests for click_submit iteration + disabled-skip in `flow/submit.py`.

Cherry-picks from `stash@{0}` §7.4 KEEP-7 (see
`docs/session-reports/2026-04-17_stash-triage_flow-refinements.md`):

- **KEEP-7** — `click_submit` used to call `page.locator(selector).first` and
  accept the first *visible* match. If that first match was disabled
  (loading state, duplicate DOM node, stale shadow tree), master fell
  through to the next selector and could miss an enabled submit button
  sibling. Stash iterates ALL matches via `.nth(i)` in `range(count)`,
  checks `is_enabled` alongside `is_visible`, and clicks the first match
  that is visible + enabled + not in `_SKIP_PATTERN`. Per-button debug
  log is added for post-mortem.

Tests mock `page.locator` with `MagicMock` that supports `.count()` +
`.nth(i)`. No Playwright runtime. The `submit_with_confirmation` wrapper
is out of scope (Phase A commit `5c7d625` touched it separately; the
cherry-pick does not).
"""

import logging
import re
from unittest.mock import AsyncMock, MagicMock

import pytest

from flow.submit import SUBMIT_SELECTORS, _SKIP_PATTERN, click_submit


# ---------------------------------------------------------------------------
# Helpers — build mock page.locator() returning indexable button mocks
# ---------------------------------------------------------------------------


def _make_button(*, visible=True, enabled=True, text=""):
    """Build a mock Playwright Locator for a single button.

    The returned mock supports the three async methods `click_submit` calls:
    `is_visible`, `is_enabled`, `inner_text`, plus `click`.
    """
    btn = MagicMock()
    btn.is_visible = AsyncMock(return_value=visible)
    btn.is_enabled = AsyncMock(return_value=enabled)
    btn.inner_text = AsyncMock(return_value=text)
    btn.click = AsyncMock()
    return btn


def _locator_with_buttons(buttons):
    """Build a mock Playwright Locator matching `buttons` — supports
    `.count()` + `.nth(i)` indexing.

    `.first` aliases `buttons[0]` when present so a would-be master-era code
    path (`.first.is_visible()` → click) would see the real state of the
    first match, not a zero-count sentinel. Empty list returns a locator
    whose `.first.is_visible()` is False (no element to probe).
    """
    loc = MagicMock()
    loc.count = AsyncMock(return_value=len(buttons))
    loc.nth = MagicMock(side_effect=lambda i: buttons[i])
    if buttons:
        loc.first = buttons[0]
    else:
        loc.first = MagicMock()
        loc.first.is_visible = AsyncMock(return_value=False)
    return loc


def _make_page(selector_to_buttons):
    """Mock `page` whose `locator(sel)` dispatches to a per-selector button
    list. Any unmapped selector returns a zero-count locator (fall-through).
    """
    page = MagicMock()
    page.keyboard = MagicMock()
    page.keyboard.press = AsyncMock()

    def _locator(sel):
        buttons = selector_to_buttons.get(sel, [])
        return _locator_with_buttons(buttons)

    page.locator = MagicMock(side_effect=_locator)
    return page


# ---------------------------------------------------------------------------
# KEEP-7: iterate all matching buttons, skip disabled
# ---------------------------------------------------------------------------


async def test_click_submit_iterates_all_buttons(caplog):
    """B16 KEEP-7: selector matches 3 buttons (2 disabled + 1 enabled) →
    code iterates, skips disabled, clicks the enabled one. Master would
    have stopped at .first (disabled) or clicked it via force=True."""
    btn_disabled = _make_button(visible=True, enabled=False, text="Generate")
    btn_not_visible = _make_button(visible=False, enabled=True, text="Generate")
    btn_ok = _make_button(visible=True, enabled=True, text="Generate")

    # Use the FIRST selector in SUBMIT_SELECTORS so we hit the iteration
    # before any other selector is tried.
    page = _make_page({SUBMIT_SELECTORS[0]: [btn_disabled, btn_not_visible, btn_ok]})

    with caplog.at_level(logging.INFO, logger="flow.submit"):
        result = await click_submit(page)

    assert result is True
    btn_disabled.click.assert_not_awaited()
    btn_not_visible.click.assert_not_awaited()
    btn_ok.click.assert_awaited_once()
    infos = [r.getMessage() for r in caplog.records if r.levelname == "INFO"]
    assert any(
        "submit clicked" in m.lower() and "[2]" in m for m in infos
    ), f"Expected INFO mentioning Submit clicked with index [2], got: {infos}"


async def test_click_submit_skip_disabled_first(caplog):
    """B16 KEEP-7 core contract: if `.nth(0)` is disabled, iteration MUST
    continue to `.nth(1)` rather than fall through to the next selector.
    Master's `.first` behavior would miss the enabled sibling entirely."""
    btn_disabled = _make_button(visible=True, enabled=False, text="Generate")
    btn_enabled = _make_button(visible=True, enabled=True, text="Generate")

    # Same selector matches both — stop iteration on the enabled one.
    page = _make_page({SUBMIT_SELECTORS[0]: [btn_disabled, btn_enabled]})

    with caplog.at_level(logging.INFO, logger="flow.submit"):
        result = await click_submit(page)

    assert result is True
    btn_disabled.click.assert_not_awaited()
    btn_enabled.click.assert_awaited_once()
    # Only ONE selector was tried — fall-through did not occur.
    tried = [call.args[0] for call in page.locator.call_args_list]
    assert tried == [SUBMIT_SELECTORS[0]], (
        f"Expected ONLY the first selector to be probed (no fall-through), got: {tried}"
    )


async def test_click_submit_skip_pattern_preserved():
    """B16 KEEP-7 rule: master's `_SKIP_PATTERN` noise filter MUST still
    run inside the per-button loop. Stash iterates but keeps this filter;
    a regression that removed it would click "Generate Video" / "Lower
    Priority" buttons that aren't the composer submit."""
    # "video" matches `_SKIP_PATTERN` — confirm the pattern itself first.
    assert _SKIP_PATTERN.search("Generate video")

    btn_noise = _make_button(visible=True, enabled=True, text="Generate video")
    btn_ok = _make_button(visible=True, enabled=True, text="Create")

    page = _make_page({SUBMIT_SELECTORS[0]: [btn_noise, btn_ok]})

    result = await click_submit(page)

    assert result is True
    btn_noise.click.assert_not_awaited()  # filtered by _SKIP_PATTERN
    btn_ok.click.assert_awaited_once()


async def test_click_submit_no_enabled_button():
    """B16 KEEP-7: all matches on selector A disabled → fall through to
    selector B. The selector-level fall-through (master's only strategy)
    still works on top of the per-selector iteration."""
    btn_a1 = _make_button(visible=True, enabled=False, text="Generate")
    btn_a2 = _make_button(visible=True, enabled=False, text="Generate")
    btn_b = _make_button(visible=True, enabled=True, text="Create")

    page = _make_page(
        {
            SUBMIT_SELECTORS[0]: [btn_a1, btn_a2],
            SUBMIT_SELECTORS[1]: [btn_b],
        }
    )

    result = await click_submit(page)

    assert result is True
    btn_a1.click.assert_not_awaited()
    btn_a2.click.assert_not_awaited()
    btn_b.click.assert_awaited_once()


async def test_click_submit_debug_log_per_button(caplog):
    """B16 KEEP-7: per-button DEBUG log records index + state for
    post-mortem. Guards against silent drift to a single aggregate log."""
    btn_1 = _make_button(visible=True, enabled=False, text="Generate")
    btn_2 = _make_button(visible=True, enabled=True, text="Create")

    page = _make_page({SUBMIT_SELECTORS[0]: [btn_1, btn_2]})

    with caplog.at_level(logging.DEBUG, logger="flow.submit"):
        await click_submit(page)

    debug_msgs = [r.getMessage() for r in caplog.records if r.levelname == "DEBUG"]
    # Expect a per-index log for each of the 2 buttons.
    assert any("btn[0]" in m for m in debug_msgs), (
        f"Expected DEBUG mentioning btn[0], got: {debug_msgs}"
    )
    assert any("btn[1]" in m for m in debug_msgs), (
        f"Expected DEBUG mentioning btn[1], got: {debug_msgs}"
    )
    # And a selector-level count log.
    assert any(
        "count=" in m and SUBMIT_SELECTORS[0] in m for m in debug_msgs
    ), f"Expected DEBUG with selector count, got: {debug_msgs}"


async def test_click_submit_all_disabled_falls_back_to_keyboard(caplog):
    """B16 KEEP-7 interaction with existing Ctrl+Enter fallback: if ALL
    selectors match disabled/invisible buttons only, click_submit reaches
    the keyboard fallback unchanged. Verifies the cherry-pick didn't
    accidentally short-circuit the fallback branch."""
    # Every selector returns 1 disabled button — none clickable.
    selector_map = {sel: [_make_button(enabled=False)] for sel in SUBMIT_SELECTORS}
    page = _make_page(selector_map)

    with caplog.at_level(logging.INFO, logger="flow.submit"):
        result = await click_submit(page)

    assert result is True
    page.keyboard.press.assert_awaited_once_with("Control+Enter")
    infos = [r.getMessage() for r in caplog.records if r.levelname == "INFO"]
    assert any(
        "ctrl+enter" in m.lower() for m in infos
    ), f"Expected Ctrl+Enter fallback INFO log, got: {infos}"


async def test_click_submit_zero_count_falls_through():
    """B16 KEEP-7: if `locator(selector).count()` returns 0, skip the
    iteration entirely and move to the next selector. No phantom click."""
    page = _make_page(
        {
            # First selector: 0 matches.
            SUBMIT_SELECTORS[0]: [],
            # Second selector: 1 enabled match — should win.
            SUBMIT_SELECTORS[1]: [_make_button(text="Create")],
        }
    )

    result = await click_submit(page)
    assert result is True
    # Confirm both selectors were probed (first returned 0 → move on).
    tried = [call.args[0] for call in page.locator.call_args_list]
    assert SUBMIT_SELECTORS[0] in tried
    assert SUBMIT_SELECTORS[1] in tried


async def test_click_submit_per_button_exception_does_not_abort():
    """B16 KEEP-7: if `is_visible` / `is_enabled` / `inner_text` raises on
    `btn[i]`, the loop moves on to `btn[i+1]` rather than aborting the
    whole selector. The per-button try/except around the inner probes is
    the mechanism; this test is the trip-wire against a future refactor
    that flattens the exception handling."""
    btn_broken = MagicMock()
    btn_broken.is_visible = AsyncMock(side_effect=RuntimeError("detached"))
    btn_broken.is_enabled = AsyncMock(return_value=True)
    btn_broken.inner_text = AsyncMock(return_value="Generate")
    btn_broken.click = AsyncMock()

    btn_ok = _make_button(text="Create")

    page = _make_page({SUBMIT_SELECTORS[0]: [btn_broken, btn_ok]})

    result = await click_submit(page)

    assert result is True
    btn_broken.click.assert_not_awaited()
    btn_ok.click.assert_awaited_once()
