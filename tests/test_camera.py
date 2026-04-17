"""B12 — unit tests for `_click_preset` + `_verify_preset_selected` in
`flow/operations/camera.py`.

Post-B12, `_click_preset` uses exactly one click strategy:
  `page.get_by_text(direction, exact=True).first` — Playwright exact-text.

The pre-B12 strategies `[aria-label='<direction>']` and
`page.locator("[role='button']").filter(has_text=...)` were pruned as dead:
Tier1 live-DOM probing (2026-04-17) confirmed they return 0 elements on
production Flow — presets lack `aria-label` and no element on the page has
an explicit `role="button"` attribute (Flow uses `<button>` tags, which
Playwright's CSS `[role='button']` does NOT match as it is strict-attribute).
Evidence: `docs/session-reports/2026-04-17_Tier1_dom-validation.md` §7 B3.

After click, `_verify_preset_selected` reads `getComputedStyle(labelDiv).color`
on the inner label DIV inside the preset BUTTON via a single `page.evaluate`.
The JS returns:
  - True  when R+G+B sum < 400 (selected ≈ rgb(48, 48, 48), sum 144)
  - False otherwise (unselected ≈ rgb(255, 255, 255), sum 765, OR label
    DIV not found, OR color fails to parse as rgb(...))

Tests mock `page` with `AsyncMock` / `MagicMock` — no Playwright runtime.
Manual E2E (POST camera job, verify output motion) is supervisor-side
after merge (WORKPLAN §5.2 Test 4).
"""

import logging
from unittest.mock import AsyncMock, MagicMock

from flow.operations.camera import _click_preset, _verify_preset_selected


def _make_locator(visible=True, click_raises=False):
    """Build a MagicMock locator chain. `.first` self-references so `.first.first`
    still works."""
    loc = MagicMock()
    loc.first = loc
    loc.is_visible = AsyncMock(return_value=visible)
    if click_raises:
        loc.click = AsyncMock(side_effect=Exception("click failed"))
    else:
        loc.click = AsyncMock()
    return loc


# ---------------------------------------------------------------------------
# _verify_preset_selected — color-based signal
# ---------------------------------------------------------------------------


async def test_verify_returns_true_on_dim_color(caplog):
    """B12: selected preset → JS returns True (label color dim) → verify True + INFO."""
    page = MagicMock()
    # Simulate JS-side: found label DIV with computed color rgb(48,48,48)
    # (sum 144 < 400 threshold) → evaluate resolves to True.
    page.evaluate = AsyncMock(return_value=True)

    with caplog.at_level(logging.INFO, logger="flow.operations.camera"):
        result = await _verify_preset_selected(page, "Low")

    assert result is True
    page.evaluate.assert_awaited_once()

    # direction must be passed to evaluate so JS side can match
    call_args = page.evaluate.await_args
    assert "Low" in call_args.args, (
        f"evaluate must be called with direction 'Low', got args={call_args.args}"
    )

    infos = [r.getMessage() for r in caplog.records if r.levelname == "INFO"]
    assert any(
        "verified selected" in m.lower() and "Low" in m for m in infos
    ), f"Expected INFO confirming verification, got: {infos}"


async def test_verify_returns_false_on_bright_color(caplog):
    """B12: unselected preset OR label missing → JS returns False → verify False + WARNING."""
    page = MagicMock()
    # Simulate JS-side: label DIV has color rgb(255,255,255) (sum 765 >= 400)
    # OR no matching label DIV at all — both paths yield False.
    page.evaluate = AsyncMock(return_value=False)

    with caplog.at_level(logging.WARNING, logger="flow.operations.camera"):
        result = await _verify_preset_selected(page, "Center")

    assert result is False
    page.evaluate.assert_awaited_once()

    warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert any(
        "not verified active" in m.lower() and "Center" in m for m in warnings
    ), f"Expected WARNING about unverified preset, got: {warnings}"


async def test_verify_returns_false_on_evaluate_exception(caplog):
    """B12: page.evaluate raising → verify returns False (swallowed) + WARNING."""
    page = MagicMock()
    page.evaluate = AsyncMock(side_effect=Exception("evaluate blew up"))

    with caplog.at_level(logging.WARNING, logger="flow.operations.camera"):
        result = await _verify_preset_selected(page, "Dolly in")

    assert result is False

    warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert any(
        "verify failed" in m.lower() and "Dolly in" in m for m in warnings
    ), f"Expected WARNING about verify failure, got: {warnings}"


async def test_verify_script_uses_computed_color_signal():
    """B12: the JS sent to page.evaluate must read getComputedStyle.color.

    This is a contract test — the semantic signal per Tier1 §B3 is the
    computed `color` on the inner label DIV. Any future refactor that
    drops this signal would reintroduce the B3 regression.
    """
    page = MagicMock()
    page.evaluate = AsyncMock(return_value=True)

    await _verify_preset_selected(page, "Low")

    call_args = page.evaluate.await_args
    script = call_args.args[0]
    assert "getComputedStyle" in script, (
        "Verify JS must call getComputedStyle (computed-style read)"
    )
    assert "color" in script, "Verify JS must read the 'color' property"
    # Ground-truth colors from Tier1: selected rgb(48,48,48), unselected rgb(255,255,255).
    # A threshold on the R+G+B sum is the natural discriminator — the sentinel
    # `rgb` token proves the script is parsing color values.
    assert "rgb" in script.lower(), "Verify JS must parse rgb(...) color values"


# ---------------------------------------------------------------------------
# _click_preset — single strategy (get_by_text exact=True)
# ---------------------------------------------------------------------------


async def test_click_preset_get_by_text_succeeds(caplog):
    """B12: get_by_text(exact=True) finds preset + verify True → return True + INFO."""
    preset_loc = _make_locator(visible=True)

    page = MagicMock()
    # Explicit mock so we can assert it is NEVER called (pruning contract below).
    page.locator = MagicMock()
    page.get_by_text = MagicMock(return_value=preset_loc)
    page.evaluate = AsyncMock(return_value=True)  # verify: selected

    with caplog.at_level(logging.INFO, logger="flow.operations.camera"):
        result = await _click_preset(page, "Dolly in")

    assert result is True
    preset_loc.click.assert_awaited_once()
    # Exactly one verify call — only one strategy remains
    assert page.evaluate.await_count == 1

    # Pruning contract: strategies 1 (`[aria-label=...]`) and 2 (`[role='button']`)
    # were removed after Tier1 confirmed they find 0 elements on live Flow DOM.
    # `page.locator` must NEVER be called for preset lookup — a regression that
    # reintroduces either strategy would trip this assertion.
    page.locator.assert_not_called()

    # Strategy contract: get_by_text MUST be called with exact=True to avoid
    # partial-match hazards (e.g. "Low" colliding with "Lower"). Playwright's
    # native exact-text matching replaces the pre-B12 anchored-regex strategy.
    call_args = page.get_by_text.call_args
    assert "Dolly in" in call_args.args, (
        f"get_by_text must be called with direction, got {call_args}"
    )
    assert call_args.kwargs.get("exact") is True, (
        f"get_by_text MUST receive exact=True, got kwargs={call_args.kwargs}"
    )

    infos = [r.getMessage() for r in caplog.records if r.levelname == "INFO"]
    assert any(
        "get_by_text" in m and "Dolly in" in m for m in infos
    ), f"Expected INFO mentioning get_by_text strategy, got: {infos}"


async def test_click_preset_returns_false_when_preset_absent(caplog):
    """B12: no matching preset visible → return False + ERROR. No click."""
    nothing = _make_locator(visible=False)

    page = MagicMock()
    page.get_by_text = MagicMock(return_value=nothing)
    page.evaluate = AsyncMock(return_value=False)

    with caplog.at_level(logging.ERROR, logger="flow.operations.camera"):
        result = await _click_preset(page, "Nonexistent Preset")

    assert result is False
    nothing.click.assert_not_awaited()
    # Verify is never called when nothing was clickable
    page.evaluate.assert_not_awaited()

    errors = [r.getMessage() for r in caplog.records if r.levelname == "ERROR"]
    assert any(
        "could not click+verify" in m.lower() and "Nonexistent Preset" in m
        for m in errors
    ), f"Expected ERROR log about click+verify failure, got: {errors}"


async def test_click_preset_clicked_but_color_verify_fails(caplog):
    """B12: preset clicked successfully but color-verify False → return False + ERROR.

    Differs from the 'absent' case — click DID happen + verify DID run,
    but the computed-color signal came back negative. Since only one
    strategy remains, there is no fallthrough.
    """
    preset_loc = _make_locator(visible=True)

    page = MagicMock()
    page.get_by_text = MagicMock(return_value=preset_loc)
    page.evaluate = AsyncMock(return_value=False)  # verify: NOT selected

    with caplog.at_level(logging.WARNING, logger="flow.operations.camera"):
        result = await _click_preset(page, "Low")

    assert result is False
    preset_loc.click.assert_awaited_once()
    page.evaluate.assert_awaited_once()

    warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert any(
        "not verified active" in m.lower() and "Low" in m for m in warnings
    ), f"Expected WARNING from verify step, got: {warnings}"
    errors = [r.getMessage() for r in caplog.records if r.levelname == "ERROR"]
    assert any(
        "could not click+verify" in m.lower() and "Low" in m for m in errors
    ), f"Expected ERROR after exhausting strategies, got: {errors}"
