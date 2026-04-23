"""B14/B27/B28/B29 — unit tests for nav verify + media_id-aware tile click +
disabled-button guard + URL-strip guard in `_base.py`.

B14 cherry-picks from `stash@{0}` §7 KEEP-2 + KEEP-3 (see
`docs/session-reports/2026-04-17_stash-triage_flow-refinements.md`):

- **KEEP-2** — After `navigate_to_edit`, verify the URL landed in `/edit/`
  mode. If the resolved `media_id` differs from the requested one, log a
  WARNING and proceed (Flow SPA sometimes redirects to a sibling video in
  the same project; caller still sees *some* edit mode).
- **KEEP-3** — `_click_video_tile` prioritises a JS-side click whose
  link/data-attribute matches the target `media_id`. Master fell back to
  clicking *any* visible video, which in a multi-video project could pick
  the wrong tile (violates INV-5 — engine would operate on the wrong
  media_id, a sibling video rather than the targeted one).

B28 — `click_action_button` must raise a clear extend-child-lockout error
when a mode button (Insert / Remove / Camera) is visible but disabled, so
operators see the B22-inheritance diagnostic immediately instead of a
misleading "Failed to find button" message.

B29 — `navigate_to_edit` must raise a clear SPA-stripped error when the
post-goto URL lacks `/edit/`, pointing operators at the B22-inheritance
root cause (stale media_id post-sibling-extend).

All mocked at the Playwright boundary — no browser runtime. Sleeps are
stubbed via an autouse fixture so the 2s / 3s render waits inside
`_click_video_tile` don't inflate test runtime.

The existing bbox-canvas helper (`draw_bbox_on_video`) is orthogonal and
covered by `tests/test_bbox.py`.
"""

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from flow.operations import _base
from flow.operations._base import (
    _activate_clip_tile,
    _click_video_tile,
    click_action_button,
    navigate_to_edit,
)


# 32-hex-char UUIDs — must match the `{20,64}` regex in flow/navigation.py
# `_MEDIA_RE`, otherwise `extract_media_id` returns None and the mismatch
# branch never fires.
MEDIA_ID_A = "a" * 32
MEDIA_ID_B = "b" * 32
PROJECT_ID = "c" * 32


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """Stub `asyncio.sleep` for this module so the 2s+3s tile-render waits
    don't make each test sleep 5s. Scoped by monkeypatch → restored after."""
    monkeypatch.setattr("asyncio.sleep", AsyncMock())


@pytest.fixture(autouse=True)
def _no_login(monkeypatch):
    """`navigate_to_edit` calls `is_login_page(current_url)` — force False
    so we never enter the login-redirect branch in these tests."""
    monkeypatch.setattr(_base, "is_login_page", lambda u: False)


def _edit_url(media_id: str, project_id: str = PROJECT_ID) -> str:
    return f"https://labs.google/fx/tools/flow/project/{project_id}/edit/{media_id}"


def _project_url(project_id: str = PROJECT_ID) -> str:
    return f"https://labs.google/fx/tools/flow/project/{project_id}"


def _make_locator_mock(wait_for_ok: bool = True):
    """Build a locator whose `.first.wait_for(...)` is an awaitable.

    `wait_for_ok=False` makes `.first.wait_for` raise, which
    `_editor_mounted` treats as "editor never rendered"."""
    loc = MagicMock()
    if wait_for_ok:
        loc.first.wait_for = AsyncMock()
    else:
        loc.first.wait_for = AsyncMock(side_effect=Exception("video never visible"))
    loc.first.click = AsyncMock()
    loc.first.is_visible = AsyncMock(return_value=True)
    loc.first.is_enabled = AsyncMock(return_value=True)
    return loc


def _make_client(url: str, profile: str = "test-profile", editor_mounts: bool = True):
    """Mock client+page where `page.url` returns the given string.

    `page.goto` is a no-op AsyncMock — it does NOT mutate `page.url`, so
    the test controls what the navigation code sees at each read.

    `editor_mounts=False` makes the default `page.locator("video")` wait
    fail, simulating Flow's half-loaded SPA state (B39)."""
    client = MagicMock()
    client.profile_name = profile
    page = MagicMock()
    page.url = url
    page.goto = AsyncMock()
    page.locator = MagicMock(return_value=_make_locator_mock(wait_for_ok=editor_mounts))
    client.page = page
    return client, page


# ---------------------------------------------------------------------------
# KEEP-2: navigate_to_edit post-nav verify + mismatch warning
# ---------------------------------------------------------------------------


async def test_navigate_activates_target_tile_on_media_mismatch(caplog):
    """B32 (2026-04-19): URL landed on a DIFFERENT media than the target
    (B30 walk-up scenario — L3 insert after L2 extend inherits L1 grandparent's
    media but nav lands on L2 extend-output's URL). `navigate_to_edit` must
    invoke `_activate_clip_tile(page, target_media_id)` to switch the active
    clip in Flow's history panel so Insert/Remove/Camera re-enable on the
    target (B28 lockout workaround). Non-fatal: returns normally even if the
    tile isn't found — the caller's mode-click will surface the B28 guard."""
    client, page = _make_client(_edit_url(MEDIA_ID_B))
    # Stub the locator call used inside _activate_clip_tile to claim the
    # tile exists and pretend the JS evaluate returned True.
    tile_locator = MagicMock()
    tile_locator.first.wait_for = AsyncMock()
    page.locator = MagicMock(return_value=tile_locator)
    page.evaluate = AsyncMock(return_value=True)

    job = {
        "edit_url": _edit_url(MEDIA_ID_B),  # direct parent's URL (fresh)
        "project_url": _project_url(),
        "media_id": MEDIA_ID_A,              # walked-up target (grandparent)
    }

    with caplog.at_level(logging.INFO, logger="flow.operations._base"):
        edit_url, project_id, _locale = await navigate_to_edit(client, job)

    # Non-fatal: returns normally
    assert edit_url == _edit_url(MEDIA_ID_B)
    assert project_id == PROJECT_ID

    # _activate_clip_tile must have evaluated the click-dispatch JS with the
    # target media_id (not the URL's media_id)
    page.evaluate.assert_awaited()
    eval_args = page.evaluate.await_args
    assert eval_args.args[1] == MEDIA_ID_A, (
        f"Activate-tile JS must be called with target media_id={MEDIA_ID_A!r}, "
        f"got args={eval_args.args!r}"
    )
    # And the INFO log tells us why
    infos = [r.getMessage() for r in caplog.records if r.levelname == "INFO"]
    assert any(
        "url media differs from target" in i.lower()
        and MEDIA_ID_A[:20] in i
        and MEDIA_ID_B[:20] in i
        for i in infos
    ), f"Expected B32 activation INFO log with both ids, got: {infos}"


async def test_navigate_no_warning_on_media_id_match(caplog):
    """KEEP-2: URL's media_id == requested → no mismatch WARNING, success."""
    client, _page = _make_client(_edit_url(MEDIA_ID_A))

    job = {
        "edit_url": _edit_url(MEDIA_ID_A),
        "project_url": _project_url(),
        "media_id": MEDIA_ID_A,
    }

    with caplog.at_level(logging.WARNING, logger="flow.operations._base"):
        await navigate_to_edit(client, job)

    warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert not any(
        "different media" in w.lower() for w in warnings
    ), f"No mismatch WARNING expected for matching media_id, got: {warnings}"


async def test_navigate_uses_edit_url_as_primary_goto():
    """B27 (2026-04-19): direct `goto(edit_url)` is the fast path.

    Verified live on `ngoctuandt20` EN profile — `scripts/probe_direct_edit_url.py`
    shows the SPA mounts the editor on direct /edit/ goto (submit chip +
    model chip + textarea all present). Pre-B27 code went to project_url
    first, then tile-clicked — 2 pageloads + a wait instead of 1.

    This test asserts the FIRST `page.goto` call targets the edit_url,
    not the project_url. Prevents silent regression to the project-grid-
    first strategy."""
    client, page = _make_client(_edit_url(MEDIA_ID_A))

    job = {
        "edit_url": _edit_url(MEDIA_ID_A),
        "project_url": _project_url(),
        "media_id": MEDIA_ID_A,
    }

    await navigate_to_edit(client, job)

    assert page.goto.await_count >= 1
    first_call = page.goto.await_args_list[0]
    first_url = first_call.args[0]
    assert "/edit/" in first_url, (
        f"Primary goto must target the edit URL (contains /edit/), "
        f"got: {first_url}"
    )
    assert MEDIA_ID_A in first_url, (
        f"Primary goto must carry the requested media_id, got: {first_url}"
    )


async def test_navigate_falls_back_to_tile_click_when_spa_bounces(monkeypatch):
    """B27: if direct goto lands on /project/ (SPA bounce), fall back to
    the existing tile-click path. Ensures the simplification does NOT
    remove the defensive fallback."""
    # Page reports /project/ after goto → bounce scenario.
    client, page = _make_client(_project_url())

    # Tile click succeeds (returns True — media_id-aware JS click).
    tile_click = AsyncMock(return_value=True)
    monkeypatch.setattr(_base, "_click_video_tile", tile_click)

    # After tile click, post-nav verify reads page.url again; simulate
    # page having transitioned to /edit/ by mutating the mock.
    def _flip_url_to_edit():
        page.url = _edit_url(MEDIA_ID_A)
    tile_click.side_effect = lambda *a, **kw: (_flip_url_to_edit(), True)[1]

    job = {
        "edit_url": _edit_url(MEDIA_ID_A),
        "project_url": _project_url(),
        "media_id": MEDIA_ID_A,
    }

    edit_url, _pid, _locale = await navigate_to_edit(client, job)

    assert edit_url == _edit_url(MEDIA_ID_A)
    tile_click.assert_awaited()  # fallback triggered


async def test_navigate_recovers_from_flow_landing_via_cta(monkeypatch):
    """If Flow lands on marketing after login, click CTA before failing.

    User-observed on 2026-04-20: after Google login, opening a project/edit
    URL may stall on `labs.google/fx/.../tools/flow` until the page's
    "Create with Flow" CTA is clicked. `navigate_to_edit` should attempt that
    recovery before raising the generic homepage-access error.
    """
    client, page = _make_client("https://labs.google/fx/vi/tools/flow")

    recover = AsyncMock(side_effect=lambda *a, **kw: setattr(page, "url", _project_url()) or True)
    monkeypatch.setattr(_base, "recover_from_flow_landing", recover)

    tile_click = AsyncMock(side_effect=lambda *a, **kw: setattr(page, "url", _edit_url(MEDIA_ID_A)) or True)
    monkeypatch.setattr(_base, "_click_video_tile", tile_click)

    job = {
        "edit_url": _edit_url(MEDIA_ID_A),
        "project_url": _project_url(),
        "media_id": MEDIA_ID_A,
    }

    edit_url, _pid, _locale = await navigate_to_edit(client, job)

    assert edit_url == _edit_url(MEDIA_ID_A)
    recover.assert_awaited_once()
    tile_click.assert_awaited_once()


async def test_wait_for_video_loaded_recovers_landing_before_wait(monkeypatch):
    """Landing CTA wins over an `/edit/` URL before video detection runs."""
    page = MagicMock()
    page.url = _edit_url(MEDIA_ID_A)
    video = MagicMock()
    video.wait_for = AsyncMock()
    page.locator.return_value.first = video

    recover = AsyncMock(return_value=True)
    monkeypatch.setattr(_base, "_recover_editor_landing", recover)

    await _base.wait_for_video_loaded(page)

    recover.assert_awaited_once_with(page, page.url)
    video.wait_for.assert_awaited_once()


async def test_navigate_raises_when_not_in_edit_mode(monkeypatch):
    """KEEP-2 + B29: after all nav attempts, URL still lacks `/edit/` →
    RuntimeError with B29 "SPA stripped" diagnostic (pointing operators at
    B22 inheritance as the likely root cause).

    Prevents silent fall-through where a job tries to submit an op against
    a page that's still on the project grid (would click the wrong button)."""
    client, _page = _make_client(_project_url())

    # Tile click also fails → no fallback can recover /edit/
    monkeypatch.setattr(_base, "_click_video_tile", AsyncMock(return_value=False))

    job = {
        "edit_url": _edit_url(MEDIA_ID_A),
        "project_url": _project_url(),
        "media_id": MEDIA_ID_A,
    }

    with pytest.raises(RuntimeError, match="SPA stripped"):
        await navigate_to_edit(client, job)


# ---------------------------------------------------------------------------
# KEEP-3: _click_video_tile media_id-aware priority
# ---------------------------------------------------------------------------


async def test_click_tile_priority1_js_receives_media_id():
    """KEEP-3 P1: media_id given → JS evaluate called with media_id as arg.

    Proof the helper uses a media_id-filtered click, not a generic
    `.first` tile click (which would violate INV-5 in multi-video
    projects)."""
    page = MagicMock()
    page.url = _edit_url(MEDIA_ID_A)  # post-click URL — simulates success
    page.evaluate = AsyncMock(return_value=f"link:{MEDIA_ID_A[:12]}")

    result = await _click_video_tile(page, MEDIA_ID_A, timeout_sec=1.0)

    assert result is True
    assert page.evaluate.await_count == 1
    call = page.evaluate.await_args_list[0]
    assert call.args[1] == MEDIA_ID_A, (
        f"JS evaluate must receive media_id as its second arg, got: {call.args}"
    )


async def test_click_tile_js_script_matches_media_id_selectors():
    """KEEP-3 trip-wire: JS body must match media_id against link[href]
    AND data-tile-id AND data-media-id. Guards against someone swapping
    the script for a generic video-first click that re-introduces the
    wrong-tile bug."""
    page = MagicMock()
    page.url = _edit_url(MEDIA_ID_A)
    page.evaluate = AsyncMock(return_value=None)  # JS finds no match
    # Give the P2+P3 locator fallbacks something safe to return
    idle_loc = MagicMock()
    idle_loc.first = MagicMock()
    idle_loc.first.is_visible = AsyncMock(return_value=False)
    idle_loc.first.click = AsyncMock()
    page.locator = MagicMock(return_value=idle_loc)

    await _click_video_tile(page, MEDIA_ID_A, timeout_sec=1.0)

    assert page.evaluate.await_count >= 1
    js_src = page.evaluate.await_args_list[0].args[0]
    assert 'a[href*="/edit/"]' in js_src, "JS must match link[href*='/edit/']"
    assert "data-tile-id" in js_src, "JS must match [data-tile-id]"
    assert "data-media-id" in js_src, "JS must match [data-media-id]"


async def test_click_tile_priority2_falls_back_to_data_tile_id():
    """KEEP-3 P2: JS returns None → click first `[data-tile-id]` tile."""
    page = MagicMock()
    page.url = _edit_url(MEDIA_ID_A)
    page.evaluate = AsyncMock(return_value=None)

    tile_loc = MagicMock()
    tile_loc.first = MagicMock()
    tile_loc.first.is_visible = AsyncMock(return_value=True)
    tile_loc.first.click = AsyncMock()

    video_loc = MagicMock()
    video_loc.first = MagicMock()
    video_loc.first.is_visible = AsyncMock(return_value=False)
    video_loc.first.click = AsyncMock()

    def route(selector):
        if selector == "[data-tile-id]":
            return tile_loc
        if selector == "video":
            return video_loc
        return MagicMock()

    page.locator = MagicMock(side_effect=route)

    result = await _click_video_tile(page, MEDIA_ID_A, timeout_sec=1.0)

    assert result is True
    tile_loc.first.click.assert_awaited_once()
    video_loc.first.click.assert_not_awaited()


async def test_click_tile_no_media_id_skips_js_priority():
    """KEEP-3: called without media_id → P1 (JS) is skipped entirely; the
    helper goes straight to the `[data-tile-id]` locator fallback. This
    keeps legacy L1-only call sites (no media context) working."""
    page = MagicMock()
    page.url = _edit_url("m" * 32)
    page.evaluate = AsyncMock()

    tile_loc = MagicMock()
    tile_loc.first = MagicMock()
    tile_loc.first.is_visible = AsyncMock(return_value=True)
    tile_loc.first.click = AsyncMock()
    page.locator = MagicMock(return_value=tile_loc)

    result = await _click_video_tile(page, "", timeout_sec=1.0)

    assert result is True


# ---------------------------------------------------------------------------
# B28: click_action_button is_enabled guard — extend-child lockout diagnostic
# ---------------------------------------------------------------------------


def _make_button_locator(*, visible: bool, enabled: bool):
    """Construct a mock `page.locator(...)` return whose `.first` yields a
    button with the requested `is_visible` / `is_enabled` state."""
    btn_loc = MagicMock()
    btn_loc.first = MagicMock()
    btn_loc.first.is_visible = AsyncMock(return_value=visible)
    btn_loc.first.is_enabled = AsyncMock(return_value=enabled)
    btn_loc.first.click = AsyncMock()
    return btn_loc


async def test_click_action_button_raises_on_disabled():
    """B28: visible-but-disabled mode button → raise with extend-child
    lockout message pointing at B22 inheritance. Prevents the pre-B28
    silent failure where `is_enabled=False` caused Playwright `.click()`
    to time out with the misleading `"Failed to find Camera button"`."""
    # Pass 1 title selector hits a visible+disabled button.
    btn_loc = _make_button_locator(visible=True, enabled=False)
    page = MagicMock()
    page.locator = MagicMock(return_value=btn_loc)

    with pytest.raises(RuntimeError, match="extend-child lockout"):
        await click_action_button(page, ["Camera"])

    # Disabled button must NOT be clicked.
    btn_loc.first.click.assert_not_called()


async def test_click_action_button_clicks_when_enabled():
    """B28 regression guard: visible+enabled → normal click path unchanged."""
    btn_loc = _make_button_locator(visible=True, enabled=True)
    page = MagicMock()
    page.locator = MagicMock(return_value=btn_loc)

    result = await click_action_button(page, ["Camera"])

    assert result is True
    btn_loc.first.click.assert_awaited_once()


# ---------------------------------------------------------------------------
# B29: navigate_to_edit URL-strip guard — stale-media_id diagnostic
# ---------------------------------------------------------------------------


async def test_navigate_to_edit_raises_on_url_strip(monkeypatch):
    """B29: post-goto page.url lacks `/edit/` → raise "SPA stripped" with a
    B22-inheritance hint. Caught the pre-B29 path where a stale L1
    `/edit/{media_id}` (after a sibling extend completed) silently fell
    through to a tile-click fallback that picked the wrong element."""
    client, _page = _make_client(_project_url())  # post-goto URL is /project/
    # Tile-click fallback can't recover — still not /edit/.
    monkeypatch.setattr(_base, "_click_video_tile", AsyncMock(return_value=False))

    job = {
        "edit_url": _edit_url(MEDIA_ID_A),
        "project_url": _project_url(),
        "media_id": MEDIA_ID_A,
    }

    with pytest.raises(RuntimeError, match="SPA stripped"):
        await navigate_to_edit(client, job)


async def test_navigate_to_edit_passes_when_url_intact():
    """B29 regression guard: `/edit/` present in post-goto URL → no raise,
    normal flow returns the edit_url as-is."""
    client, _page = _make_client(_edit_url(MEDIA_ID_A))

    job = {
        "edit_url": _edit_url(MEDIA_ID_A),
        "project_url": _project_url(),
        "media_id": MEDIA_ID_A,
    }

    edit_url, project_id, _locale = await navigate_to_edit(client, job)

    assert edit_url == _edit_url(MEDIA_ID_A)
    assert project_id == PROJECT_ID


# ---------------------------------------------------------------------------
# B39: navigate_to_edit — editor-not-mounted fallback. Flow's SPA sometimes
# keeps /edit/{stale_media} in the URL without rendering the editor (no
# <video>, no mode buttons). Observed 2026-04-23 on L2 insert whose parent
# media had been consumed by a sibling extend. Fallback: first-tile click.
# ---------------------------------------------------------------------------


async def test_navigate_recovers_when_editor_never_mounts(monkeypatch, caplog):
    """B39: URL intact at /edit/{stale} but <video> never appears. Must
    recover via first-tile click and retry the mount check before
    returning. Verifies B39 failure mode caught 2026-04-23 on insert-object
    against a sibling-consumed parent media."""
    client, page = _make_client(_edit_url(MEDIA_ID_A), editor_mounts=False)

    async def _first_tile_recover(page_arg, media):
        # Simulate first-tile click switching to the live /edit/{latest}
        # AND the editor mounting on the retry.
        page_arg.url = _edit_url(MEDIA_ID_B)
        page_arg.locator = MagicMock(return_value=_make_locator_mock(wait_for_ok=True))
        return True

    monkeypatch.setattr(_base, "_click_video_tile", AsyncMock(side_effect=_first_tile_recover))
    monkeypatch.setattr(_base, "_activate_clip_tile", AsyncMock(return_value=False))

    job = {
        "edit_url": _edit_url(MEDIA_ID_A),
        "project_url": _project_url(),
        "media_id": MEDIA_ID_A,
    }

    with caplog.at_level(logging.WARNING, logger="flow.operations._base"):
        edit_url, _pid, _locale = await navigate_to_edit(client, job)

    assert edit_url == _edit_url(MEDIA_ID_A)
    msgs = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert any("Editor did not mount" in m and "first-tile click" in m for m in msgs)


async def test_navigate_raises_when_editor_dead_and_recovery_fails(monkeypatch):
    """B39 hard-fail: editor not mounted AND first-tile click can't recover
    → raise with stale-media diagnostic. Guards against silent soft-warn
    (the pre-B39 behaviour that let insert-object proceed and fail later
    at "Failed to find Insert button" with no root-cause hint)."""
    client, _page = _make_client(_edit_url(MEDIA_ID_A), editor_mounts=False)
    monkeypatch.setattr(_base, "_click_video_tile", AsyncMock(return_value=False))
    monkeypatch.setattr(_base, "_activate_clip_tile", AsyncMock(return_value=False))

    job = {
        "edit_url": _edit_url(MEDIA_ID_A),
        "project_url": _project_url(),
        "media_id": MEDIA_ID_A,
    }

    with pytest.raises(RuntimeError, match="Parent media may be stale"):
        await navigate_to_edit(client, job)


async def test_navigate_skips_fallback_when_editor_mounts_normally(monkeypatch):
    """B39 regression guard: on the happy path (<video> visible within
    timeout), `_click_video_tile` must NOT be invoked — the fallback is
    cost-only in non-broken cases."""
    client, _page = _make_client(_edit_url(MEDIA_ID_A))  # editor_mounts=True
    tile_click = AsyncMock(return_value=True)
    monkeypatch.setattr(_base, "_click_video_tile", tile_click)
    monkeypatch.setattr(_base, "_activate_clip_tile", AsyncMock(return_value=True))

    job = {
        "edit_url": _edit_url(MEDIA_ID_A),
        "project_url": _project_url(),
        "media_id": MEDIA_ID_A,
    }

    await navigate_to_edit(client, job)

    tile_click.assert_not_awaited()


# ---------------------------------------------------------------------------
# B32: _activate_clip_tile — history-panel tile click workaround for
# extend-child lockout (B28). Verified live 2026-04-19 on project 513d580b
# per `docs/session-reports/2026-04-19_B32_*` — clicking the tile
# `[data-tile-id="fe_id_{media_id}"]` in the right history panel flips
# Insert/Remove/Camera from disabled → enabled without changing page.url.
# ---------------------------------------------------------------------------


async def test_activate_clip_tile_dispatches_mouse_events():
    """B32: `_activate_clip_tile` dispatches a full MouseEvent sequence
    (pointerdown → mousedown → pointerup → mouseup → click) via JS, not
    `.click()` — Flow's tile handler is on a `<div>` without a button
    ancestor and ignores plain `.click()` calls. Confirms the helper
    calls `page.evaluate` with the target media_id and returns True."""
    client, page = _make_client(_edit_url(MEDIA_ID_B))
    tile_locator = MagicMock()
    tile_locator.first.wait_for = AsyncMock()
    page.locator = MagicMock(return_value=tile_locator)
    page.evaluate = AsyncMock(return_value=True)

    ok = await _activate_clip_tile(page, MEDIA_ID_A)
    assert ok is True

    # Selector goes through the data-tile-id attribute with the fe_id_ prefix
    page.locator.assert_called_with(f"[data-tile-id='fe_id_{MEDIA_ID_A}']")

    # JS script is dispatched with the target media_id
    page.evaluate.assert_awaited_once()
    js_src, mid_arg = page.evaluate.await_args.args
    assert mid_arg == MEDIA_ID_A
    # Contract: all 5 pointer/mouse event types are fired
    for ev in ("pointerdown", "mousedown", "pointerup", "mouseup", "click"):
        assert ev in js_src, f"Dispatch JS must fire {ev}"


async def test_activate_clip_tile_returns_false_when_tile_absent():
    """B32: tile `wait_for(attached)` times out → return False, no JS
    dispatch. Caller logs a warning + the subsequent mode-button click
    will either succeed (URL media already matches the target, no
    activation needed) or raise the B28 extend-child-lockout error."""
    client, page = _make_client(_edit_url(MEDIA_ID_B))
    tile_locator = MagicMock()
    tile_locator.first.wait_for = AsyncMock(side_effect=Exception("timeout"))
    page.locator = MagicMock(return_value=tile_locator)
    page.evaluate = AsyncMock()

    ok = await _activate_clip_tile(page, MEDIA_ID_A)
    assert ok is False
    page.evaluate.assert_not_awaited()


async def test_activate_clip_tile_returns_false_for_empty_media_id():
    """B32: guard against empty `media_id` — e.g. a malformed job row where
    B30 walk-up hit the root without finding a non-extend ancestor. Skip
    the locator call entirely."""
    client, page = _make_client(_edit_url(MEDIA_ID_B))
    page.locator = MagicMock()
    page.evaluate = AsyncMock()

    ok = await _activate_clip_tile(page, "")
    assert ok is False
    page.locator.assert_not_called()
    page.evaluate.assert_not_awaited()
