"""Unit tests for ``click_submit`` in ``flow/submit.py``.

History:
  * B16 (commit ``004d8fb``) — KEEP-7 iterate all matching buttons + skip
    disabled, don't stop at ``.first``.
  * B26 (2026-04-19) — collapse ``SUBMIT_SELECTORS`` to the single
    exact-text selector ``button:has(i:text-is('arrow_forward'))`` and
    drop ``_SKIP_PATTERN`` (the new selector can only match submit
    buttons — no noise to filter out). Add ``scope`` parameter so
    callers on /edit/ can restrict the search to the composer panel.

The B16 iterate/skip/debug-log contracts remain intact; B26 tightens the
selector itself so the filter step is redundant.

Tests mock ``page.locator`` with ``MagicMock`` that supports ``.count()``
+ ``.nth(i)``. No Playwright runtime. The ``submit_with_confirmation``
wrapper is out of scope.
"""

import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

import flow.submit as submit_mod
from flow.submit import SUBMIT_SELECTORS, click_submit


# ---------------------------------------------------------------------------
# Helpers — build mock page.locator() returning indexable button mocks
# ---------------------------------------------------------------------------


def _make_button(*, visible=True, enabled=True, text=""):
    """Build a mock Playwright Locator for a single button.

    The returned mock supports the three async methods ``click_submit``
    calls: ``is_visible``, ``is_enabled``, ``inner_text``, plus ``click``.
    """
    btn = MagicMock()
    btn.is_visible = AsyncMock(return_value=visible)
    btn.is_enabled = AsyncMock(return_value=enabled)
    btn.inner_text = AsyncMock(return_value=text)
    btn.click = AsyncMock()
    return btn


def _locator_with_buttons(buttons):
    """Build a mock Playwright Locator matching ``buttons`` — supports
    ``.count()`` + ``.nth(i)`` indexing."""
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
    """Mock ``page`` whose ``locator(sel)`` dispatches to a per-selector
    button list. Any unmapped selector returns a zero-count locator."""
    page = MagicMock()
    page.keyboard = MagicMock()
    page.keyboard.press = AsyncMock()

    def _locator(sel):
        buttons = selector_to_buttons.get(sel, [])
        return _locator_with_buttons(buttons)

    page.locator = MagicMock(side_effect=_locator)
    return page


# ---------------------------------------------------------------------------
# B16 KEEP-7 behaviors (still required under B26 single-selector design)
# ---------------------------------------------------------------------------


async def test_click_submit_iterates_all_buttons(caplog):
    """B16 KEEP-7: selector matches 3 buttons (2 disabled + 1 enabled) →
    code iterates, skips disabled, clicks the enabled one. Master would
    have stopped at ``.first`` (disabled) or clicked it via force=True."""
    btn_disabled = _make_button(visible=True, enabled=False, text="Tạo")
    btn_not_visible = _make_button(visible=False, enabled=True, text="Tạo")
    btn_ok = _make_button(visible=True, enabled=True, text="Tạo")

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
    """B16 KEEP-7 core contract: if ``.nth(0)`` is disabled, iteration
    MUST continue to ``.nth(1)`` rather than fall through to keyboard.
    Master's ``.first`` behavior would miss the enabled sibling entirely.

    This is the exact shape of the /edit/ DOM: two buttons with
    ``<i>arrow_forward</i>`` — the disabled decorative one and the real
    submit. B16 iteration is what picks the right one under B26's single
    ``:text-is('arrow_forward')`` selector."""
    btn_disabled = _make_button(visible=True, enabled=False, text="Tạo")
    btn_enabled = _make_button(visible=True, enabled=True, text="Tạo")

    page = _make_page({SUBMIT_SELECTORS[0]: [btn_disabled, btn_enabled]})

    with caplog.at_level(logging.INFO, logger="flow.submit"):
        result = await click_submit(page)

    assert result is True
    btn_disabled.click.assert_not_awaited()
    btn_enabled.click.assert_awaited_once()
    # Only ONE selector was tried.
    tried = [call.args[0] for call in page.locator.call_args_list]
    assert tried == [SUBMIT_SELECTORS[0]], (
        f"Expected ONLY the first selector to be probed, got: {tried}"
    )


async def test_click_submit_debug_log_per_button(caplog):
    """B16 KEEP-7: per-button DEBUG log records index + state for
    post-mortem. Guards against silent drift to a single aggregate log."""
    btn_1 = _make_button(visible=True, enabled=False, text="Tạo")
    btn_2 = _make_button(visible=True, enabled=True, text="Tạo")

    page = _make_page({SUBMIT_SELECTORS[0]: [btn_1, btn_2]})

    with caplog.at_level(logging.DEBUG, logger="flow.submit"):
        await click_submit(page)

    debug_msgs = [r.getMessage() for r in caplog.records if r.levelname == "DEBUG"]
    assert any("btn[0]" in m for m in debug_msgs), (
        f"Expected DEBUG mentioning btn[0], got: {debug_msgs}"
    )
    assert any("btn[1]" in m for m in debug_msgs), (
        f"Expected DEBUG mentioning btn[1], got: {debug_msgs}"
    )
    assert any(
        "count=" in m and SUBMIT_SELECTORS[0] in m for m in debug_msgs
    ), f"Expected DEBUG with selector count, got: {debug_msgs}"


async def test_click_submit_all_disabled_falls_back_to_keyboard(caplog):
    """B16 KEEP-7 interaction with existing Ctrl+Enter fallback: if ALL
    matches are disabled/invisible, ``click_submit`` reaches the keyboard
    fallback unchanged."""
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


async def test_click_submit_per_button_exception_does_not_abort():
    """B16 KEEP-7: if ``is_visible`` / ``is_enabled`` / ``inner_text``
    raises on ``btn[i]``, the loop moves on to ``btn[i+1]`` rather than
    aborting the whole selector."""
    btn_broken = MagicMock()
    btn_broken.is_visible = AsyncMock(side_effect=RuntimeError("detached"))
    btn_broken.is_enabled = AsyncMock(return_value=True)
    btn_broken.inner_text = AsyncMock(return_value="Tạo")
    btn_broken.click = AsyncMock()

    btn_ok = _make_button(text="Tạo")

    page = _make_page({SUBMIT_SELECTORS[0]: [btn_broken, btn_ok]})

    result = await click_submit(page)

    assert result is True
    btn_broken.click.assert_not_awaited()
    btn_ok.click.assert_awaited_once()


# ---------------------------------------------------------------------------
# B26 — scope parameter + exact-text selector contract
# ---------------------------------------------------------------------------


async def test_click_submit_scope_prepends_to_selector():
    """B26 behavioral contract: when ``scope`` is given, ``click_submit``
    queries ``"{scope} {SUBMIT_SELECTORS[0]}"`` — NOT the bare selector.
    This lets L2 callers scope the search to the composer panel (e.g.
    ``[data-scroll-state='START']``) so decorative submit buttons elsewhere
    on the page aren't candidates."""
    scope = "[data-scroll-state='START']"
    scoped_selector = f"{scope} {SUBMIT_SELECTORS[0]}"
    btn_ok = _make_button(text="Tạo")

    page = _make_page({scoped_selector: [btn_ok]})

    result = await click_submit(page, scope=scope)

    assert result is True
    btn_ok.click.assert_awaited_once()
    tried = [call.args[0] for call in page.locator.call_args_list]
    assert tried == [scoped_selector], (
        f"Expected ``page.locator`` to be called with the scoped selector only, "
        f"got: {tried}"
    )


def test_submit_selectors_use_exact_text_and_no_fuzzy_antipattern():
    """B26 source trip-wire: the submit selector list must use Playwright's
    ``:text-is('arrow_forward')`` (exact-match engine) and must NOT use
    fuzzy ``:has-text('arrow_forward')`` or text-based aria-label probes.

    Reason for both halves:
      * Exact-text is required — on /edit/ the Camera mode-switch button's
        innerText is ``"videocam\\nCamera"`` and the L1 composer has
        multiple buttons with Material Icon ligatures. Fuzzy matches leak
        across icons (``arrow_forward`` vs ``arrow_forward_ios``) and
        across icon-plus-label text.
      * ``aria-label``-based selectors (pre-B26 ``button[aria-label*='Create' i]``)
        are locale-dependent AND the live DOM has EMPTY aria-label on the
        real submit button.

    Also enforces the B26 collapse to a single canonical selector — if
    someone adds a new submit selector, they must either update this test
    with a deliberate reason (and probably extend scope handling), or
    reconsider whether the addition is locale-independent and unique.
    """
    src = Path(submit_mod.__file__).read_text(encoding="utf-8")

    assert "button:has(i:text-is('arrow_forward'))" in src, (
        "B26 canonical selector missing. Expected exact-text "
        "``button:has(i:text-is('arrow_forward'))`` in SUBMIT_SELECTORS."
    )
    # Whitelist docstring mentions of the anti-pattern (they explain why
    # it's forbidden). Only code-level use is fatal.
    forbidden_fuzzy = "button:has(i:has-text('arrow_forward'))"
    assert forbidden_fuzzy not in src, (
        f"B26 anti-pattern leaked into source: ``{forbidden_fuzzy}`` is "
        "fuzzy and matches across Material Icon variants. Use "
        "``:text-is('arrow_forward')`` instead."
    )
    for forbidden_text_aria in (
        "aria-label*='Create' i",
        "aria-label*='Generate' i",
        "aria-label*='Send' i",
    ):
        assert forbidden_text_aria not in src, (
            f"B26 anti-pattern leaked into source: text-based aria-label "
            f"probe ``{forbidden_text_aria}`` — live DOM's submit button "
            "has EMPTY aria-label, so this selector is a locale-dependent "
            "red herring."
        )

    # Exactly one canonical selector — collapse is intentional.
    assert len(SUBMIT_SELECTORS) == 1, (
        f"Expected B26 collapse to a single submit selector, got "
        f"{len(SUBMIT_SELECTORS)}: {SUBMIT_SELECTORS}. If you add a "
        "second selector, extend the scope-prepending logic + this test."
    )
