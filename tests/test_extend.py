"""B15 — unit tests for panel-open verify + submit diagnostics + scroll-state
Slate selector in `flow/operations/extend.py`.

Cherry-picks from `stash@{0}` §7.3 KEEP-4 + KEEP-5 + KEEP-6 (see
`docs/session-reports/2026-04-17_stash-triage_flow-refinements.md`):

- **KEEP-4** — `_verify_extend_panel` polls for `editors>=2` OR
  `[data-scroll-state='START']` and is called between the Extend click
  (Step 3) and prompt typing (Step 4). Master silently fell through on a
  missed panel open and only surfaced the failure as a submit timeout with
  no diagnostic.
- **KEEP-5** — When `submit_with_confirmation` returns False, log `page.url`
  and the slate-editor count before raising. Keeps the raise contract but
  gives post-mortem visibility into the page state at timeout.
- **KEEP-6** (partial) — `_type_extend_prompt` adds a Method 1 that targets
  `[data-scroll-state='START'] [data-slate-editor='true']` (extend-panel
  specific) before the existing "last slate editor" Method 2. Master's 4
  placeholder/aria-label fallbacks are PRESERVED (stash H5 rejected by
  supervisor — defense-in-depth with low cost).

Tests mock `page` / `client` with `AsyncMock` / `MagicMock` — no Playwright
runtime. `asyncio.sleep` is stubbed out via fixtures so the Step-3 2s wait
and Step-3.5 1s wait don't bloat runtime.
"""

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from flow.operations import _base
from flow.operations import extend as extend_mod
from flow.operations.extend import (
    _type_extend_prompt,
    _verify_extend_panel,
    extend_video,
)


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """Stub `asyncio.sleep` so the Step-3 / Step-3.5 / per-iteration waits
    don't inflate test runtime. Monkeypatch-scoped — restored after each test.
    """
    monkeypatch.setattr("asyncio.sleep", AsyncMock())


# ---------------------------------------------------------------------------
# KEEP-4: _verify_extend_panel — panel-open detection
# ---------------------------------------------------------------------------


def _make_slate_page(slates_data: list[dict] | None = None):
    """Mock `page` for the inner-text-based _verify_extend_panel detector.

    Each entry in ``slates_data`` is a dict ``{placeholder, inner, parent}``
    describing what each slate editor returns from
    ``get_attribute("data-placeholder")`` / ``inner_text()`` / the 6-level
    parent-text JS walk respectively.

    Live-verified 2026-05-16: Flow's current UI re-uses the single composer
    slate for Extend mode, so detection is content-based (placeholder text
    "What happens next?" / "Tiếp theo là gì?"), NOT editor count or
    data-scroll-state. See `flow/operations/extend.py::_verify_extend_panel`.
    """
    slates_data = slates_data or []
    page = MagicMock()

    handles = []
    slate_mocks = []
    for entry in slates_data:
        handle = MagicMock(name=f"slate_handle_{len(handles)}")
        handles.append(handle)
        slate = MagicMock()
        slate.get_attribute = AsyncMock(return_value=entry.get("placeholder", ""))
        slate.inner_text = AsyncMock(return_value=entry.get("inner", ""))
        slate.element_handle = AsyncMock(return_value=handle)
        slate_mocks.append(slate)

    def _locator(selector):
        loc = MagicMock()
        if selector == "[data-slate-editor='true']":
            loc.count = AsyncMock(return_value=len(slate_mocks))
            loc.nth = MagicMock(side_effect=lambda i: slate_mocks[i])
        else:
            loc.count = AsyncMock(return_value=0)
        return loc

    async def _evaluate(_js, handle):
        idx = handles.index(handle)
        return slates_data[idx].get("parent", "")

    page.locator = MagicMock(side_effect=_locator)
    page.evaluate = AsyncMock(side_effect=_evaluate)
    return page


async def test_verify_returns_true_when_slate_shows_extend_placeholder(caplog):
    """New UI: slate inner_text contains "What happens next?" → True + INFO.

    Live probe 2026-05-16 confirmed: after Extend click, the composer slate
    inner_text changes to "What happens next?\\n\\ufeff\\n". Detector returns
    True from that single slate; no second editor required.
    """
    page = _make_slate_page([
        {"placeholder": "", "inner": "What happens next?\n﻿\n", "parent": ""}
    ])

    with caplog.at_level(logging.INFO, logger="flow.operations.extend"):
        result = await _verify_extend_panel(page, timeout_sec=0.1)

    assert result is True
    infos = [r.getMessage() for r in caplog.records if r.levelname == "INFO"]
    assert any(
        "verified" in m.lower() and "extend placeholder" in m.lower()
        for m in infos
    ), f"Expected INFO mentioning extend placeholder match, got: {infos}"


async def test_verify_returns_true_on_localized_vi_placeholder():
    """VI locale: placeholder "Tiếp theo là gì?" must also match."""
    page = _make_slate_page([
        {"placeholder": "Tiếp theo là gì?", "inner": "", "parent": ""}
    ])
    assert await _verify_extend_panel(page, timeout_sec=0.1) is True


async def test_verify_returns_true_via_parent_text_walk():
    """Slate itself empty but a parent (≤6 ancestors up) carries the hint
    via the page.evaluate JS walk. Real-world variant when Flow renders the
    placeholder as a sibling label rather than slate inner_text.
    """
    page = _make_slate_page([
        {"placeholder": "", "inner": "", "parent": "what happens next"}
    ])
    assert await _verify_extend_panel(page, timeout_sec=0.1) is True


async def test_verify_returns_false_on_timeout(caplog):
    """No slate matches any extend hint → timeout, return False + ERROR diag."""
    page = _make_slate_page([
        {"placeholder": "", "inner": "Type your prompt", "parent": ""}
    ])

    with caplog.at_level(logging.ERROR, logger="flow.operations.extend"):
        result = await _verify_extend_panel(page, timeout_sec=0.1)

    assert result is False
    errors = [r.getMessage() for r in caplog.records if r.levelname == "ERROR"]
    assert any(
        "not detected" in m.lower() and "slate" in m.lower() for m in errors
    ), f"Expected ERROR log diagnosing missing panel, got: {errors}"


async def test_verify_returns_false_when_zero_slates():
    """Edge: zero slate editors on page → timeout False, no crash."""
    page = _make_slate_page([])
    assert await _verify_extend_panel(page, timeout_sec=0.05) is False


async def test_verify_scans_all_slates_until_match():
    """Multi-slate page: first slate empty, second slate has the hint →
    scan continues across all slates within one poll tick.
    """
    page = _make_slate_page([
        {"placeholder": "", "inner": "Type prompt", "parent": ""},
        {"placeholder": "", "inner": "What happens next?", "parent": ""},
    ])
    assert await _verify_extend_panel(page, timeout_sec=0.1) is True


async def test_verify_probes_slate_editor_selector():
    """Trip-wire: helper MUST probe `[data-slate-editor='true']` selector.

    Replaces the legacy two-signal trip-wire — the old data-scroll-state path
    was removed because Flow's current UI re-uses the single composer slate
    rather than mounting a second editor / scroll-state container.
    """
    selectors_seen = []
    page = MagicMock()

    def _locator(selector):
        selectors_seen.append(selector)
        loc = MagicMock()
        loc.count = AsyncMock(return_value=0)
        return loc

    page.locator = MagicMock(side_effect=_locator)

    await _verify_extend_panel(page, timeout_sec=0.05)

    assert "[data-slate-editor='true']" in selectors_seen, (
        "Helper must probe [data-slate-editor='true'] selector"
    )


# ---------------------------------------------------------------------------
# KEEP-4 Step 3.5: extend_video calls _verify_extend_panel + raises on False
# ---------------------------------------------------------------------------


def _mock_page():
    """Mock page with url + default locator returning empty counts."""
    page = MagicMock()
    page.url = "https://labs.google/fx/tools/flow/project/pid/edit/mid"

    def _loc(selector):
        loc = MagicMock()
        loc.count = AsyncMock(return_value=0)
        loc.first = MagicMock()
        loc.first.is_visible = AsyncMock(return_value=False)
        return loc

    page.locator = MagicMock(side_effect=_loc)
    return page


def _mock_client(page):
    client = MagicMock()
    client.page = page
    client.clear_captures = MagicMock()
    return client


def _patch_extend_deps(monkeypatch, *, verify=True, submit=True):
    """Stub every helper `extend_video` calls, leaving only the SUT flow
    observable. Returns a dict of the individual mocks for per-test asserts.
    """
    mocks = {
        "navigate_to_edit": AsyncMock(return_value=("edit_url", "pid", "en")),
        "wait_for_video_loaded": AsyncMock(),
        "click_action_button": AsyncMock(return_value=True),
        "_verify_extend_panel": AsyncMock(return_value=verify),
        "_type_extend_prompt": AsyncMock(),
        "select_model": AsyncMock(),
        "count_visible_cards": AsyncMock(return_value=0),
        "submit_with_confirmation": AsyncMock(return_value=submit),
        "finalize_operation": AsyncMock(return_value={"ok": True}),
    }
    for name, mock in mocks.items():
        monkeypatch.setattr(extend_mod, name, mock)
    return mocks


async def test_extend_raises_when_panel_not_open(monkeypatch):
    """Post-B31 (2026-04-19): Extend panel is default-active on /edit/ per
    discrete-2job-verify_en.md:119 ("Extend mode is default active"). So
    extend_video probes panel state FIRST; only clicks Extend button if panel
    isn't open; re-verifies after click. When click succeeds but panel still
    doesn't open, raise panel-specific message. _verify_extend_panel is
    called twice in this path. Fails fast BEFORE submit."""
    page = _mock_page()
    client = _mock_client(page)
    mocks = _patch_extend_deps(monkeypatch, verify=False)

    job = {"media_id": "mid", "edit_url": "url"}

    with pytest.raises(RuntimeError, match="Extend panel did not open"):
        await extend_video(client, job, prompt="hello")

    # Probe (False → need click), re-verify after click (still False → raise)
    assert mocks["_verify_extend_panel"].await_count == 2
    mocks["click_action_button"].assert_awaited_once()
    # Fail-fast: must raise BEFORE submit / finalize
    mocks["submit_with_confirmation"].assert_not_awaited()
    mocks["finalize_operation"].assert_not_awaited()


async def test_extend_proceeds_when_panel_open(monkeypatch):
    """B15 KEEP-4: panel opens → type prompt + select model + submit +
    finalize all run; helper result passes through."""
    page = _mock_page()
    client = _mock_client(page)
    mocks = _patch_extend_deps(monkeypatch, verify=True, submit=True)

    job = {"media_id": "mid", "edit_url": "url"}
    result = await extend_video(client, job, prompt="hello")

    assert result == {"ok": True}
    mocks["_type_extend_prompt"].assert_awaited_once()
    mocks["submit_with_confirmation"].assert_awaited_once()
    mocks["finalize_operation"].assert_awaited_once()


# ---------------------------------------------------------------------------
# KEEP-5: Submit failure diagnostics
# ---------------------------------------------------------------------------


async def test_extend_submit_failure_logs_diagnostics(caplog, monkeypatch):
    """B15 KEEP-5: submit_with_confirmation → False → log ERROR with
    `url=...` AND `editors=...` BEFORE raising. The raise message now
    includes "generation did not start" (stash wording, more specific than
    master's bare "Extend submit not confirmed").
    """
    page = _mock_page()

    # Post-submit state: only main composer editor survives (extend panel
    # somehow closed mid-flight). Diagnostic must capture editor count = 1.
    def _loc(selector):
        loc = MagicMock()
        if selector == "[data-slate-editor='true']":
            loc.count = AsyncMock(return_value=1)
        else:
            loc.count = AsyncMock(return_value=0)
        loc.first = MagicMock()
        loc.first.is_visible = AsyncMock(return_value=False)
        return loc

    page.locator = MagicMock(side_effect=_loc)
    client = _mock_client(page)
    _patch_extend_deps(monkeypatch, verify=True, submit=False)

    job = {"media_id": "mid", "edit_url": "url"}

    with caplog.at_level(logging.ERROR, logger="flow.operations.extend"):
        with pytest.raises(RuntimeError, match="generation did not start"):
            await extend_video(client, job, prompt="hello")

    errors = [r.getMessage() for r in caplog.records if r.levelname == "ERROR"]
    assert any(
        "not confirmed" in m.lower() and "url=" in m and "editors=" in m
        for m in errors
    ), (
        f"Expected diagnostic ERROR containing url= and editors= fields, "
        f"got: {errors}"
    )


async def test_extend_submit_success_skips_diagnostic_log(caplog, monkeypatch):
    """B15 KEEP-5 negative contract: on successful submit, NO diagnostic
    ERROR is logged (the `if not confirmed` branch is skipped entirely).
    """
    page = _mock_page()
    client = _mock_client(page)
    _patch_extend_deps(monkeypatch, verify=True, submit=True)

    job = {"media_id": "mid", "edit_url": "url"}

    with caplog.at_level(logging.ERROR, logger="flow.operations.extend"):
        await extend_video(client, job, prompt="hello")

    errors = [r.getMessage() for r in caplog.records if r.levelname == "ERROR"]
    assert not any(
        "not confirmed" in m.lower() for m in errors
    ), f"Unexpected diagnostic ERROR on successful submit: {errors}"


# ---------------------------------------------------------------------------
# KEEP-6: _type_extend_prompt Method 1 (scroll-state) + H5 rejection
# ---------------------------------------------------------------------------


def _text_page():
    """Mock page with keyboard hooks for typing tests."""
    page = MagicMock()
    page.keyboard = MagicMock()
    page.keyboard.press = AsyncMock()
    page.keyboard.type = AsyncMock()
    return page


async def test_type_extend_prompt_method1_uses_scroll_state(caplog):
    """B15 KEEP-6: Method 1 selector `[data-scroll-state='START']
    [data-slate-editor='true']` found + visible → click + type via it.
    Method 2 (last Slate editor) is NOT reached."""
    page = _text_page()

    scroll_loc = MagicMock()
    scroll_loc.count = AsyncMock(return_value=1)
    scroll_loc.first = MagicMock()
    scroll_loc.first.is_visible = AsyncMock(return_value=True)
    scroll_loc.first.click = AsyncMock()

    editors_loc = MagicMock()
    editors_loc.count = AsyncMock(return_value=2)

    def _loc(selector):
        if selector == "[data-scroll-state='START'] [data-slate-editor='true']":
            return scroll_loc
        if selector == "[data-slate-editor='true']":
            return editors_loc
        fallback = MagicMock()
        fallback.first = MagicMock()
        fallback.first.is_visible = AsyncMock(return_value=False)
        return fallback

    page.locator = MagicMock(side_effect=_loc)

    with caplog.at_level(logging.INFO, logger="flow.operations.extend"):
        await _type_extend_prompt(page, "hello world")

    scroll_loc.first.click.assert_awaited_once()
    page.keyboard.type.assert_awaited_once()
    # Method 2 should NOT be consulted
    editors_loc.count.assert_not_awaited()

    infos = [r.getMessage() for r in caplog.records if r.levelname == "INFO"]
    assert any(
        "data-scroll-state" in m for m in infos
    ), f"Expected INFO mentioning data-scroll-state path, got: {infos}"


async def test_type_extend_prompt_method1_contract_selector():
    """B15 KEEP-6 trip-wire: Method 1 MUST probe the compound selector
    `[data-scroll-state='START'] [data-slate-editor='true']` BEFORE any
    other editor selector. Guards against silent regression to a
    Method-2-only implementation (which master had pre-B15)."""
    selectors_called = []
    page = _text_page()

    def _loc(selector):
        selectors_called.append(selector)
        loc = MagicMock()
        loc.count = AsyncMock(return_value=0)
        loc.first = MagicMock()
        loc.first.is_visible = AsyncMock(return_value=False)
        return loc

    page.locator = MagicMock(side_effect=_loc)

    await _type_extend_prompt(page, "hi")

    assert "[data-scroll-state='START'] [data-slate-editor='true']" in selectors_called, (
        "Method 1 scroll-state compound selector must be probed"
    )
    # Ordering contract: scroll-state selector before last-Slate fallback
    scroll_idx = selectors_called.index(
        "[data-scroll-state='START'] [data-slate-editor='true']"
    )
    if "[data-slate-editor='true']" in selectors_called:
        slate_idx = selectors_called.index("[data-slate-editor='true']")
        assert scroll_idx < slate_idx, (
            "Method 1 (scroll-state) must be tried BEFORE Method 2 (last Slate)"
        )


async def test_type_extend_prompt_falls_back_to_last_slate(caplog):
    """B15 KEEP-6: Method 1 finds 0 → Method 2 (last Slate editor) clicks
    the last editor by DOM order. Master's Method 2 is preserved unchanged."""
    page = _text_page()

    method1_loc = MagicMock()
    method1_loc.count = AsyncMock(return_value=0)

    editor_nth = MagicMock()
    editor_nth.is_visible = AsyncMock(return_value=True)
    editor_nth.click = AsyncMock()

    method2_loc = MagicMock()
    method2_loc.count = AsyncMock(return_value=2)
    method2_loc.nth = MagicMock(return_value=editor_nth)

    def _loc(selector):
        if selector == "[data-scroll-state='START'] [data-slate-editor='true']":
            return method1_loc
        if selector == "[data-slate-editor='true']":
            return method2_loc
        fallback = MagicMock()
        fallback.first = MagicMock()
        fallback.first.is_visible = AsyncMock(return_value=False)
        return fallback

    page.locator = MagicMock(side_effect=_loc)

    with caplog.at_level(logging.INFO, logger="flow.operations.extend"):
        await _type_extend_prompt(page, "x")

    editor_nth.click.assert_awaited_once()
    method2_loc.nth.assert_called_with(1)  # last of 2 editors = index 1
    infos = [r.getMessage() for r in caplog.records if r.levelname == "INFO"]
    # Master's Method 2 log: "Extend prompt typed via slate editor (N editors found)".
    # Distinguishes from Method 1's "data-scroll-state editor" log — "slate editor"
    # substring is unique to Method 2.
    assert any(
        "slate editor" in m.lower() and "scroll-state" not in m.lower()
        for m in infos
    ), f"Expected INFO for Method 2 (slate editor) path, got: {infos}"


async def test_type_extend_prompt_preserves_placeholder_fallbacks():
    """B15 H5 REJECTED: master's 4 placeholder/aria-label fallback selectors
    MUST still be probed when both Method 1 AND Method 2 fail. Stash proposed
    removing these; supervisor kept them for defense-in-depth — this test is
    the contract that prevents silent removal."""
    selectors_called = []
    page = _text_page()

    def _loc(selector):
        selectors_called.append(selector)
        loc = MagicMock()
        loc.count = AsyncMock(return_value=0)
        loc.first = MagicMock()
        loc.first.is_visible = AsyncMock(return_value=False)
        return loc

    page.locator = MagicMock(side_effect=_loc)

    await _type_extend_prompt(page, "foo")

    preserved = [
        "[placeholder*='next' i]",
        "[placeholder*='tiếp' i]",
        "[placeholder*='tiep' i]",
        "[aria-label*='extend' i]",
    ]
    for sel in preserved:
        assert sel in selectors_called, (
            f"H5 rejected — placeholder fallback {sel!r} must remain as "
            f"a Method-3 defense layer. Selectors tried: {selectors_called}"
        )


# ---------------------------------------------------------------------------
# T9: extend-video multi-generation handling
# ---------------------------------------------------------------------------


PROJECT_ID = "d" * 32
PARENT_SLUG = "a" * 32
SETTLED_SLUG = "b" * 32
REDIRECT_SLUG_1 = "c" * 32
REDIRECT_SLUG_2 = "e" * 32


def _edit_url(slug: str) -> str:
    return f"https://labs.google/fx/tools/flow/project/{PROJECT_ID}/edit/{slug}"


def _finalize_client(page, media_events=None):
    return SimpleNamespace(
        page=page,
        _media_id_events=list(media_events or []),
        _gen_id="gen-1",
        profile_name="profile-a",
    )


@pytest.mark.skip(reason="depends on T7 video multi-gen download fix on this branch")
async def test_extend_finalize_multi_gen_downloads_all_outputs_after_t7(monkeypatch):
    """T9 #1: once T7 lands, extend-video multi-gen should download every new output."""
    page = MagicMock()
    page.url = _edit_url(SETTLED_SLUG)
    client = _finalize_client(
        page,
        media_events=[
            {"mid": REDIRECT_SLUG_1},
            {"mid": REDIRECT_SLUG_2},
        ],
    )
    monkeypatch.setattr(
        _base,
        "wait_for_completion",
        AsyncMock(return_value={"done": True, "media_ids": [REDIRECT_SLUG_1, REDIRECT_SLUG_2]}),
    )
    monkeypatch.setattr(
        _base,
        "download_video",
        AsyncMock(return_value=["ext_1.mp4", "ext_2.mp4"]),
    )

    result = await _base.finalize_operation(
        client,
        {"media_id": PARENT_SLUG},
        "extend-video",
        PROJECT_ID,
        "",
        "ext",
    )

    assert len(result["output_files"]) == 2


# NOTE: T9 #2 + T9 #3 (settled-route media_id assertion against synthetic
# redirect-id fixtures) were removed 2026-04-25. The production resolver
# uses /pq/api network events authoritatively; the synthetic ordering in
# those fixtures was unreachable in production and the tests would never
# flip from xfail to pass. Live coverage is captured by
# docs/session-reports/2026-04-23_l2-media-id-fix-live-verified.md.
