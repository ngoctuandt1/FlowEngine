"""B3 — unit tests for `_click_preset` + `_verify_preset_selected` in
`flow/operations/camera.py`.

The helper does 3 ordered strategies on a Playwright `page`:

1. `page.locator("[aria-label='<dir>']").first` — exact attribute match.
2. `page.locator("[role='button']").filter(has_text=re.compile("^<dir>$")).first`
   — role-based + anchored regex (no partial match).
3. `page.get_by_text(direction, exact=True).first` — Playwright exact-text.

After each click, `_verify_preset_selected` checks the preset has an active-
state signal (aria-pressed / aria-selected / class keyword). `page.evaluate`
is called with the direction as the only argument.

Tests mock `page` with `AsyncMock` / `MagicMock` — no Playwright runtime.
Manual E2E (POST camera job with preset, verify output motion) is a
supervisor-side task after merge (WORKPLAN §5.2 Test 4).
"""

import logging
import re
from unittest.mock import AsyncMock, MagicMock

from flow.operations.camera import _click_preset, _verify_preset_selected


def _make_locator(visible=True, click_raises=False):
    """Build a MagicMock locator chain. `.first` self-references so `.first.first`
    still works; `.filter(...)` returns a fresh-configurable inner locator by
    default (overridden per-test when we need to capture filter args)."""
    loc = MagicMock()
    loc.first = loc
    loc.is_visible = AsyncMock(return_value=visible)
    if click_raises:
        loc.click = AsyncMock(side_effect=Exception("click failed"))
    else:
        loc.click = AsyncMock()
    loc.filter = MagicMock(return_value=loc)
    return loc


async def test_click_preset_aria_label_wins(caplog):
    """B3: Strategy 1 (aria-label exact) succeeds + verify True → return True."""
    aria_loc = _make_locator(visible=True)
    fallback = _make_locator(visible=False)

    def route(selector):
        if "aria-label" in selector:
            return aria_loc
        return fallback

    page = MagicMock()
    page.locator = MagicMock(side_effect=route)
    page.get_by_text = MagicMock(return_value=_make_locator(visible=False))
    page.evaluate = AsyncMock(return_value=True)  # verify → active

    with caplog.at_level(logging.INFO, logger="flow.operations.camera"):
        result = await _click_preset(page, "Dolly in")

    assert result is True
    aria_loc.click.assert_awaited_once()
    # Verify happened → evaluate called exactly once
    assert page.evaluate.await_count == 1

    infos = [r.getMessage() for r in caplog.records if r.levelname == "INFO"]
    assert any(
        "aria-label" in m and "Dolly in" in m for m in infos
    ), f"Expected INFO mentioning aria-label strategy, got: {infos}"
    assert any(
        "verified selected" in m.lower() and "Dolly in" in m for m in infos
    ), f"Expected INFO confirming verification, got: {infos}"


async def test_click_preset_exact_text_not_partial(caplog):
    """B3: direction='Low' MUST NOT partial-match 'Lower' in Strategy 2.

    The implementation passes an anchored regex to `filter(has_text=...)`.
    This test captures that regex and asserts: fullmatch('Low') succeeds,
    fullmatch('Lower') fails. If either behavior flips, the fix has regressed.
    """
    aria_loc = _make_locator(visible=False)  # Strategy 1 fails (no aria-label)
    role_button_loc = MagicMock()
    role_button_loc.first = role_button_loc

    captured_patterns = []

    def filter_fn(has_text=None, **_kwargs):
        captured_patterns.append(has_text)
        # Simulate: filter returns a locator that is NOT visible for this test
        # (we just want to capture the pattern, not actually click).
        inner = _make_locator(visible=False)
        return inner

    role_button_loc.filter = MagicMock(side_effect=filter_fn)

    def route(selector):
        if "aria-label" in selector:
            return aria_loc
        if "role='button'" in selector or 'role="button"' in selector:
            return role_button_loc
        return _make_locator(visible=False)

    page = MagicMock()
    page.locator = MagicMock(side_effect=route)
    page.get_by_text = MagicMock(return_value=_make_locator(visible=False))
    page.evaluate = AsyncMock(return_value=False)

    with caplog.at_level(logging.ERROR, logger="flow.operations.camera"):
        result = await _click_preset(page, "Low")

    assert result is False, "All strategies miss → return False"

    # The captured pattern from Strategy 2 must be an anchored regex
    assert len(captured_patterns) >= 1, "Strategy 2 must call .filter(has_text=...)"
    pattern = captured_patterns[0]
    assert isinstance(pattern, re.Pattern), (
        f"Strategy 2 must pass a compiled regex (anchored), got {type(pattern).__name__}"
    )
    assert pattern.fullmatch("Low") is not None, (
        "Anchored regex must match exactly 'Low'"
    )
    assert pattern.fullmatch("Lower") is None, (
        "Anchored regex MUST NOT match 'Lower' — this was the B3 bug"
    )
    assert pattern.fullmatch("low") is None, (
        "Regex must be case-sensitive (default) — preset labels are Title Case"
    )


async def test_click_preset_all_strategies_fail(caplog):
    """B3: no strategy finds visible preset → return False + log ERROR."""
    nothing = _make_locator(visible=False)

    page = MagicMock()
    page.locator = MagicMock(return_value=nothing)
    page.get_by_text = MagicMock(return_value=nothing)
    page.evaluate = AsyncMock(return_value=False)

    with caplog.at_level(logging.ERROR, logger="flow.operations.camera"):
        result = await _click_preset(page, "Nonexistent Preset")

    assert result is False

    errors = [r.getMessage() for r in caplog.records if r.levelname == "ERROR"]
    assert any(
        "could not click+verify" in m.lower() and "Nonexistent Preset" in m
        for m in errors
    ), f"Expected ERROR log about click+verify failure, got: {errors}"

    # No click happened (nothing was visible)
    nothing.click.assert_not_awaited()


async def test_verify_returns_false_on_no_active_state(caplog):
    """B3: click succeeded but no aria-pressed/active-class → verify=False."""
    page = MagicMock()
    # evaluate returns False: direction found but no active-state signal
    page.evaluate = AsyncMock(return_value=False)

    with caplog.at_level(logging.WARNING, logger="flow.operations.camera"):
        result = await _verify_preset_selected(page, "Center")

    assert result is False
    page.evaluate.assert_awaited_once()

    # The evaluate call must have passed direction as the arg
    call_args = page.evaluate.await_args
    assert "Center" in call_args.args, (
        f"evaluate must be called with direction 'Center', got args={call_args.args}"
    )

    warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert any(
        "not verified active" in m.lower() and "Center" in m for m in warnings
    ), f"Expected WARNING about unverified preset, got: {warnings}"


async def test_click_preset_strategy_2_role_button(caplog):
    """B3: Strategy 1 fails (no aria-label), Strategy 2 (role=button exact) wins."""
    aria_loc = _make_locator(visible=False)  # Strategy 1: nothing

    # Strategy 2: filter returns a visible locator that clicks successfully
    strategy_2_inner = _make_locator(visible=True)
    role_button_loc = MagicMock()
    role_button_loc.first = role_button_loc
    role_button_loc.filter = MagicMock(return_value=strategy_2_inner)

    def route(selector):
        if "aria-label" in selector:
            return aria_loc
        if "role='button'" in selector or 'role="button"' in selector:
            return role_button_loc
        return _make_locator(visible=False)

    page = MagicMock()
    page.locator = MagicMock(side_effect=route)
    page.get_by_text = MagicMock(return_value=_make_locator(visible=False))
    page.evaluate = AsyncMock(return_value=True)  # verify: active

    with caplog.at_level(logging.INFO, logger="flow.operations.camera"):
        result = await _click_preset(page, "Orbit left")

    assert result is True
    strategy_2_inner.click.assert_awaited_once()
    # aria-label strategy checked visibility first and saw False → no click
    aria_loc.click.assert_not_awaited()
    # get_by_text (Strategy 3) never consulted
    page.get_by_text.assert_not_called()

    infos = [r.getMessage() for r in caplog.records if r.levelname == "INFO"]
    assert any(
        "role=button exact" in m and "Orbit left" in m for m in infos
    ), f"Expected INFO mentioning role=button exact, got: {infos}"
