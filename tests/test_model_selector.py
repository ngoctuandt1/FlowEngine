"""B17 — unit tests for LP items pre-check before opening the model dropdown.

Cherry-picks from ``stash@{0}`` §7.1 KEEP-1 (see
``docs/session-reports/2026-04-17_stash-triage_flow-refinements.md``):

- **KEEP-1** — in extend mode the model panel may already show LP options
  directly (no dropdown click needed). Calling ``_open_model_dropdown``
  then would TOGGLE the panel closed, hiding the LP items. Pre-check
  counts visible "Lower Priority" items before the open call; skip the
  open if items are already there.

REJECTED from the same stash (explicit user decision — master's approach
preserved):

- **H1** capture ``chip_handle`` before chip click (dep of H4).
- **H3** thread ``chip_handle`` + ``chip_tagged_js`` through the 4 call
  sites of ``_close_model_panel`` (signature change — dep of H4).
- **H4** rewrite ``_close_model_panel`` to toggle-close by re-clicking
  the chip. User keeps master's click-outside (Slate editor) + single
  Escape fallback from B8 commit ``7245ae8``.

Tests mock ``page`` with ``AsyncMock`` / ``MagicMock`` — no Playwright
runtime. ``asyncio.sleep`` is stubbed via an autouse fixture so the
Step-2 (0.5s) and retry-loop (1.5s) waits don't inflate runtime.
"""

import inspect
import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from flow import model_selector as model_selector_mod
from flow.model_selector import _close_model_panel, select_model


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """Stub asyncio.sleep so Step-2 / retry waits don't pad runtime."""
    monkeypatch.setattr("asyncio.sleep", AsyncMock())


# ---------------------------------------------------------------------------
# Mock page builder
# ---------------------------------------------------------------------------


def _make_select_model_page(lp_count: int = 2):
    """Build a mock ``page`` that lets ``select_model`` reach the KEEP-1
    pre-check zone and (when possible) proceed to a successful item click.

    - Chip selectors: first one (``button:has-text('Veo')``) is visible and
      click-OK, so Step-1 opens the panel via the Playwright branch (no JS
      fallback needed).
    - ``MODEL_ITEM_SELECTORS`` locator: ``.filter(has_text=...)`` returns a
      locator whose ``.count()`` yields ``lp_count``. ``nth(i).inner_text``
      returns ``"Veo 3.1 - Fast [Lower Priority]"`` so the retry loop's
      ``base_name`` match succeeds. ``nth(i).click`` is a no-op.
    - ``[data-slate-editor='true']`` locator: resolves for
      ``_close_model_panel`` so click-outside succeeds (master behavior;
      H4 rewrite rejected).
    - ``page.evaluate``: returns ``False`` so JS fallbacks don't short the
      flow when the pre-check skips the open call.
    """
    page = MagicMock()

    chip = MagicMock()
    chip.first = chip
    chip.is_visible = AsyncMock(return_value=True)
    chip.click = AsyncMock(return_value=None)
    chip.inner_text = AsyncMock(return_value="Veo 3.1 - Fast x1")

    def _make_nth(_idx):
        nth = MagicMock()
        nth.inner_text = AsyncMock(return_value="Veo 3.1 - Fast [Lower Priority]")
        nth.click = AsyncMock(return_value=None)
        return nth

    filtered = MagicMock()
    filtered.count = AsyncMock(return_value=lp_count)
    filtered.first = MagicMock()
    filtered.first.inner_text = AsyncMock(return_value="Veo 3.1 - Fast [Lower Priority]")
    filtered.first.click = AsyncMock(return_value=None)
    filtered.first.is_visible = AsyncMock(return_value=True)
    filtered.nth = MagicMock(side_effect=_make_nth)

    model_items_loc = MagicMock()
    model_items_loc.filter = MagicMock(return_value=filtered)

    editor_loc = MagicMock()
    editor_loc.count = AsyncMock(return_value=1)
    editor_loc.last = MagicMock()
    editor_loc.last.click = AsyncMock(return_value=None)

    def _locator(selector):
        if "menuitem" in selector and "role='menuitem'" in selector:
            return model_items_loc
        if selector == "[data-slate-editor='true']":
            return editor_loc
        return chip

    page.locator = MagicMock(side_effect=_locator)
    page.evaluate = AsyncMock(return_value=False)
    page.keyboard = MagicMock()
    page.keyboard.press = AsyncMock(return_value=None)

    # Expose the filtered locator so tests can tamper with `.count()` behavior
    # (e.g. raise on first call to simulate a locator error).
    page._filtered_locator = filtered  # type: ignore[attr-defined]
    return page


# ---------------------------------------------------------------------------
# KEEP-1: LP pre-check behavior
# ---------------------------------------------------------------------------


async def test_lp_precheck_skips_open_when_items_already_visible(
    monkeypatch, caplog
):
    """KEEP-1 core contract: when LP items are already visible (lp_count > 0),
    ``_open_model_dropdown`` MUST NOT be called. Clicking it would TOGGLE
    the dropdown closed and hide the very items we need.

    RED baseline (master): master always calls ``_open_model_dropdown``
    unconditionally at the Step-2.7 position, so this assertion fails.
    """
    open_spy = AsyncMock(return_value=True)
    monkeypatch.setattr(model_selector_mod, "_open_model_dropdown", open_spy)
    monkeypatch.setattr(
        model_selector_mod, "_switch_to_video_tab", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(
        model_selector_mod, "_verify_credits", AsyncMock(return_value=True)
    )

    page = _make_select_model_page(lp_count=2)

    with caplog.at_level(logging.INFO, logger="flow.model_selector"):
        result = await select_model(page, model="veo-3.1-fast-lp")

    assert result is True, "LP item click should succeed with lp_count=2 mock"
    open_spy.assert_not_called()

    infos = [r.getMessage() for r in caplog.records if r.levelname == "INFO"]
    assert any(
        "already visible" in m.lower() for m in infos
    ), f"Expected INFO noting items already visible; got: {infos}"


async def test_lp_precheck_opens_when_items_not_visible(monkeypatch):
    """KEEP-1 fallthrough: pre-check sees 0 LP items → calls
    ``_open_model_dropdown`` to surface them. Preserves master's behavior
    for the common case where the dropdown is closed.
    """
    open_spy = AsyncMock(return_value=True)
    monkeypatch.setattr(model_selector_mod, "_open_model_dropdown", open_spy)
    monkeypatch.setattr(
        model_selector_mod, "_switch_to_video_tab", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(
        model_selector_mod, "_verify_credits", AsyncMock(return_value=True)
    )

    page = _make_select_model_page(lp_count=0)

    await select_model(page, model="veo-3.1-fast-lp")

    open_spy.assert_called_once()


async def test_non_lp_model_skips_precheck_and_opens_directly(monkeypatch):
    """KEEP-1 else-branch: a non-LP target has no "Lower Priority" to
    pre-check for, so the pre-check block is skipped and the flow goes
    directly to ``_open_model_dropdown``. Pre-check is LP-specific.
    """
    open_spy = AsyncMock(return_value=True)
    monkeypatch.setattr(model_selector_mod, "_open_model_dropdown", open_spy)
    monkeypatch.setattr(
        model_selector_mod, "_switch_to_video_tab", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(
        model_selector_mod, "_verify_credits", AsyncMock(return_value=True)
    )

    # High lp_count — would be detected if precheck ran, but it shouldn't.
    page = _make_select_model_page(lp_count=5)

    await select_model(page, model="veo-3.1-quality", free_mode=False)

    open_spy.assert_called_once()


async def test_precheck_exception_falls_back_to_open(monkeypatch):
    """KEEP-1 resilience contract: if the pre-check locator raises, the
    except branch still calls ``_open_model_dropdown`` — the pre-check is
    strictly an optimization, never a blocker.
    """
    open_spy = AsyncMock(return_value=True)
    monkeypatch.setattr(model_selector_mod, "_open_model_dropdown", open_spy)
    monkeypatch.setattr(
        model_selector_mod, "_switch_to_video_tab", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(
        model_selector_mod, "_verify_credits", AsyncMock(return_value=True)
    )

    page = _make_select_model_page(lp_count=2)
    # First .count() (pre-check) raises; subsequent calls (retry loop)
    # return 2 so the flow can still land on the LP item.
    page._filtered_locator.count = AsyncMock(
        side_effect=[RuntimeError("pre-check locator boom"), 2, 2, 2]
    )

    result = await select_model(page, model="veo-3.1-fast-lp")

    assert result is True
    open_spy.assert_called_once()


# ---------------------------------------------------------------------------
# KEEP-1: contract trip-wire (source-level)
# ---------------------------------------------------------------------------


def test_precheck_source_uses_lp_regex_and_skip_message():
    """KEEP-1 trip-wire: the pre-check block must appear in ``select_model``
    source with (a) the "already visible — skipping dropdown open" log
    string, and (b) at least two occurrences of the ``"Lower Priority"``
    regex — one for the pre-check filter, one for the existing retry-loop
    filter. Keeps pre-check aligned with the retry loop and guards against
    silent drift back to the master "always open" behavior.
    """
    src = Path(model_selector_mod.__file__).read_text(encoding="utf-8")
    select_src = src.split("async def select_model")[1].split("\nasync def ")[0]

    assert "already visible" in select_src, (
        "KEEP-1 missing: the 'LP items already visible' pre-check log is "
        "absent from select_model. Apply the stash @{0} §7.1 KEEP-1 hunk."
    )
    assert "skipping dropdown open" in select_src, (
        "KEEP-1 missing: the 'skipping dropdown open' log string is absent."
    )

    lp_regex_count = select_src.count('re.compile(r"Lower Priority"')
    assert lp_regex_count >= 2, (
        f"Pre-check should use the same 'Lower Priority' regex as the "
        f"retry-loop filter (expected ≥ 2 occurrences in select_model, "
        f"found {lp_regex_count})."
    )


# ---------------------------------------------------------------------------
# H1 / H3 / H4 REJECTED — static contracts (guard against silent drift)
# ---------------------------------------------------------------------------


def test_close_model_panel_signature_unchanged():
    """H3 REJECTED contract: ``_close_model_panel(page, dropdown_was_opened)``
    must keep its 2-argument signature. Stash H3 proposed adding
    ``chip_handle`` and ``chip_tagged_js`` parameters as a dependency of the
    H4 toggle-close rewrite; both rejected by user in favor of master's
    click-outside approach (B8 commit ``7245ae8``).
    """
    sig = inspect.signature(_close_model_panel)
    params = list(sig.parameters.keys())

    assert params == ["page", "dropdown_was_opened"], (
        f"Expected 2-arg signature (page, dropdown_was_opened); got: {params}. "
        f"H3 chip_handle/chip_tagged_js threading was rejected — do not add "
        f"parameters to this function."
    )


def test_close_model_panel_preserves_click_outside_approach():
    """H4 REJECTED contract: master's click-outside (Slate editor) +
    Escape fallback is preserved. ``_close_model_panel`` body must click
    ``[data-slate-editor='true']`` and NOT re-click a captured chip. Stash
    H4 proposed rewriting to re-click ``chip_handle`` / ``data-flow-chip``
    tagged element; rejected.
    """
    src = Path(model_selector_mod.__file__).read_text(encoding="utf-8")
    close_src = src.split("async def _close_model_panel")[1].split("\nasync def ")[0]

    assert "[data-slate-editor='true']" in close_src, (
        "Master's click-outside (Slate editor click) MUST be preserved. "
        "H4 proposed re-clicking the chip — rejected by user."
    )
    for forbidden in ("chip_handle", "chip_tagged_js", "data-flow-chip"):
        assert forbidden not in close_src, (
            f"Stash H1/H3/H4 token '{forbidden}' leaked into "
            f"_close_model_panel — those hunks were rejected."
        )
