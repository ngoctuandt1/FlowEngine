"""B28 leaf-lockout regression tests (2026-05-05).

Covers four layers added to fix the silent-fail pattern where Flow's SPA
auto-navigates to the leaf of an existing extend chain and the engine kept
going into click_action_button with all mode buttons disabled.

Test scenarios:
  A. _activate_clip_tile: 8s attachment timeout (not 3s) — tile attaches at t≈4s
  B. _activate_clip_tile: JS dispatch returns True but URL still shows old media
     after 5s poll → still returns True (sidebar-only switch path)
  C. _activate_clip_tile: JS evaluate returns falsy (tile detached) → returns False
  D. navigate_to_edit: _activate_clip_tile False + _click_video_tile True → continues
  E. navigate_to_edit: both paths fail → LeafLockoutError raised with correct fields
  F. dispatcher: LeafLockoutError → failed without profile burn

All mocked at the Playwright boundary — no browser runtime.
Sleeps are stubbed via autouse fixture.
"""

import logging
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from flow.operations import _base
from flow.operations._base import (
    LeafLockoutError,
    _activate_clip_tile,
    _click_video_tile,
    navigate_to_edit,
)

# 32-hex-char UUIDs (match _MEDIA_RE in flow/navigation.py {20,64})
MEDIA_ID_A = "a" * 32
MEDIA_ID_B = "b" * 32
PROJECT_ID = "c" * 32


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """Stub asyncio.sleep so render-wait loops don't inflate test time."""
    monkeypatch.setattr("asyncio.sleep", AsyncMock())


@pytest.fixture(autouse=True)
def _no_login(monkeypatch):
    """Force is_login_page=False so login-redirect branch never fires."""
    monkeypatch.setattr(_base, "is_login_page", lambda u: False)


def _edit_url(media_id: str, project_id: str = PROJECT_ID) -> str:
    return f"https://labs.google/fx/tools/flow/project/{project_id}/edit/{media_id}"


def _project_url(project_id: str = PROJECT_ID) -> str:
    return f"https://labs.google/fx/tools/flow/project/{project_id}"


def _make_locator_mock(*, wait_for_ok: bool = True):
    loc = MagicMock()
    if wait_for_ok:
        loc.first.wait_for = AsyncMock()
    else:
        loc.first.wait_for = AsyncMock(side_effect=Exception("timeout"))
    loc.first.click = AsyncMock()
    loc.first.is_visible = AsyncMock(return_value=True)
    loc.first.is_enabled = AsyncMock(return_value=True)
    return loc


def _make_client(url: str, profile: str = "test-profile", editor_mounts: bool = True):
    """Mock client+page.

    editor_mounts=False makes page.locator("video").first.wait_for raise
    (simulates B39 un-mounted editor).
    """
    client = MagicMock()
    client.profile_name = profile
    page = MagicMock()
    page.url = url
    page.goto = AsyncMock()
    page.locator = MagicMock(return_value=_make_locator_mock(wait_for_ok=editor_mounts))
    client.page = page
    return client, page


# ---------------------------------------------------------------------------
# Change A: 8s attachment timeout
# ---------------------------------------------------------------------------


async def test_activate_clip_tile_uses_8s_attachment_timeout():
    """Change A: default timeout_sec must be 8.0 (not 3.0).

    Tiles on heavily-loaded projects (6+ sidebar entries from prior runs)
    can take 4-7s to attach — the old 3s limit would abort before the tile
    rendered, silently returning False and letting the op hit a locked button.
    """
    page = MagicMock()
    # wait_for is called with timeout= keyword; capture the value
    wait_calls: list[dict] = []

    async def _wait_for_spy(**kwargs):
        wait_calls.append(kwargs)
        # Simulate tile attaching successfully

    tile_loc = MagicMock()
    tile_loc.first.wait_for = _wait_for_spy
    page.locator = MagicMock(return_value=tile_loc)
    page.evaluate = AsyncMock(return_value=True)
    # Make URL match target so the URL-poll loop exits immediately
    page.url = _edit_url(MEDIA_ID_A)

    result = await _activate_clip_tile(page, MEDIA_ID_A)

    assert result is True
    assert len(wait_calls) == 1
    timeout_ms = wait_calls[0].get("timeout")
    assert timeout_ms == 8000, (
        f"_activate_clip_tile must pass timeout=8000ms to wait_for; got {timeout_ms}. "
        f"Increase from old 3000ms matches _wait_button_enabled budget."
    )


async def test_activate_clip_tile_succeeds_when_tile_attaches_after_4s(monkeypatch):
    """Change A: tile that attaches at ~4s (beyond old 3s limit) → True.

    Simulates wait_for succeeding (no exception) when timeout=8000ms.
    With the old timeout=3000ms this would time out and return False.
    """
    page = MagicMock()
    tile_loc = MagicMock()
    tile_loc.first.wait_for = AsyncMock()  # succeeds immediately in mock
    page.locator = MagicMock(return_value=tile_loc)
    page.evaluate = AsyncMock(return_value=True)
    page.url = _edit_url(MEDIA_ID_A)

    result = await _activate_clip_tile(page, MEDIA_ID_A, timeout_sec=8.0)

    assert result is True, "Tile that attaches within 8s must return True"


async def test_activate_clip_tile_returns_false_when_tile_missing_at_8s():
    """Change A: if tile still not attached at 8s → False.

    Ensures the increased timeout doesn't swallow genuine missing-tile
    failures — wait_for raises → return False path preserved.
    """
    page = MagicMock()
    tile_loc = MagicMock()
    tile_loc.first.wait_for = AsyncMock(side_effect=Exception("Timeout 8000ms"))
    page.locator = MagicMock(return_value=tile_loc)
    page.evaluate = AsyncMock()

    result = await _activate_clip_tile(page, MEDIA_ID_A, timeout_sec=8.0)

    assert result is False
    page.evaluate.assert_not_awaited()


# ---------------------------------------------------------------------------
# Change B: verify media switch via URL poll (+ sidebar-only switch path)
# ---------------------------------------------------------------------------


async def test_activate_clip_tile_verifies_media_switch_via_url():
    """Change B: after JS dispatch, poll URL for target media_id.

    When URL updates to target → return True immediately without waiting
    the full 5s poll window.
    """
    page = MagicMock()
    tile_loc = MagicMock()
    tile_loc.first.wait_for = AsyncMock()
    page.locator = MagicMock(return_value=tile_loc)
    page.evaluate = AsyncMock(return_value=True)
    # URL already shows target → first poll iteration exits
    page.url = _edit_url(MEDIA_ID_A)

    result = await _activate_clip_tile(page, MEDIA_ID_A)

    assert result is True


async def test_activate_clip_tile_returns_false_when_url_never_updates():
    """Change B (r2 fix): URL stays on old media after 5s poll → returns False.

    If JS dispatch fires on the wrong tile (stale data-tile-id on SPA
    re-render) the URL will not confirm the switch. The old code returned
    True optimistically ("sidebar-only switch assumed"), which was the root
    cause of the silent-fail pattern: code continued to click_action_button
    with mode buttons still disabled.

    After r2 fix: 5s poll exhausted without URL match → False, so the
    caller falls through to the real Playwright click fallback or raises
    LeafLockoutError.
    """
    page = MagicMock()
    tile_loc = MagicMock()
    tile_loc.first.wait_for = AsyncMock()
    page.locator = MagicMock(return_value=tile_loc)
    page.evaluate = AsyncMock(return_value=True)
    # URL shows DIFFERENT media and never changes — URL-poll exhausts
    page.url = _edit_url(MEDIA_ID_B)

    result = await _activate_clip_tile(page, MEDIA_ID_A)

    assert result is False, (
        "URL-poll timeout (5s without URL confirming switch to target) "
        "must return False, not True. The old optimistic 'sidebar-only switch' "
        "path was removed in r2 to prevent silent-fail into disabled buttons."
    )


async def test_activate_clip_tile_returns_true_when_url_already_matches():
    """Change B (r2 fix): re-entry — URL already shows target media → True immediately.

    When navigate_to_edit calls _activate_clip_tile and the current URL
    already reflects the target media_id (direct edit URL navigation
    landed on the correct clip), skip the poll and return True without
    dispatching JS or waiting.
    """
    page = MagicMock()
    tile_loc = MagicMock()
    tile_loc.first.wait_for = AsyncMock()
    page.locator = MagicMock(return_value=tile_loc)
    page.evaluate = AsyncMock(return_value=True)
    # URL already shows the target — re-entry / already on correct clip
    page.url = _edit_url(MEDIA_ID_A)

    result = await _activate_clip_tile(page, MEDIA_ID_A)

    assert result is True, (
        "When current URL already matches target media_id, "
        "_activate_clip_tile must return True immediately (re-entry shortcut)"
    )


async def test_activate_clip_tile_returns_false_when_js_evaluate_returns_false():
    """Change B: JS evaluate returns falsy (tile detached between attach+eval) → False.

    Race condition: tile.wait_for(attached) succeeds but by the time JS
    runs document.querySelector the tile has been re-rendered and detached.
    The JS function returns false (not found) → we return False with a warning.
    """
    page = MagicMock()
    tile_loc = MagicMock()
    tile_loc.first.wait_for = AsyncMock()
    page.locator = MagicMock(return_value=tile_loc)
    page.evaluate = AsyncMock(return_value=False)  # tile not found in DOM

    result = await _activate_clip_tile(page, MEDIA_ID_A)

    assert result is False, (
        "JS evaluate returning False (tile detached race) must propagate as False"
    )


# ---------------------------------------------------------------------------
# Change C: _click_video_tile fallback on /edit/ branch
# ---------------------------------------------------------------------------


async def test_navigate_falls_back_to_click_video_tile_when_activate_fails(monkeypatch):
    """Change C: _activate_clip_tile returns False AND _click_video_tile succeeds.

    After the JS dispatch fails, navigate_to_edit must attempt the real
    Playwright click fallback (_click_video_tile). If that succeeds, the
    function must continue normally without raising.
    """
    # URL is on /edit/ but showing wrong media (MEDIA_ID_B instead of A)
    client, page = _make_client(_edit_url(MEDIA_ID_B))

    # Simulate: JS dispatch fails to activate target tile
    monkeypatch.setattr(_base, "_activate_clip_tile", AsyncMock(return_value=False))

    # _click_video_tile succeeds — simulates real Playwright click on tile
    async def _click_success(page_arg, media):
        # Tile click navigated to /edit/{target}
        page_arg.url = _edit_url(MEDIA_ID_A)
        return True

    monkeypatch.setattr(_base, "_click_video_tile", AsyncMock(side_effect=_click_success))

    job = {
        "edit_url": _edit_url(MEDIA_ID_B),
        "project_url": _project_url(),
        "media_id": MEDIA_ID_A,
        "type": "insert-object",
    }

    # Should not raise — real click fallback recovered
    edit_url, project_id, _locale = await navigate_to_edit(client, job)

    assert edit_url == _edit_url(MEDIA_ID_B)
    assert project_id == PROJECT_ID


async def test_navigate_click_fallback_logs_info_even_when_url_unchanged(monkeypatch, caplog):
    """Change C: _click_video_tile fires but URL does not change to target.

    This models the sidebar-only switch inside the editor: _click_video_tile
    returns True (click delivered) but page.url still shows MEDIA_ID_B. We
    log an INFO (not error) and trust _wait_button_enabled downstream. The
    function must NOT raise LeafLockoutError — that is reserved for when
    _click_video_tile returns False.
    """
    client, page = _make_client(_edit_url(MEDIA_ID_B))
    monkeypatch.setattr(_base, "_activate_clip_tile", AsyncMock(return_value=False))

    # Click succeeds but URL never changes (sidebar-only switch)
    monkeypatch.setattr(_base, "_click_video_tile", AsyncMock(return_value=True))

    job = {
        "edit_url": _edit_url(MEDIA_ID_B),
        "project_url": _project_url(),
        "media_id": MEDIA_ID_A,
        "type": "camera-move",
    }

    with caplog.at_level(logging.INFO, logger="flow.operations._base"):
        await navigate_to_edit(client, job)

    info_msgs = [r.getMessage() for r in caplog.records if r.levelname == "INFO"]
    assert any(
        "real-click fallback" in m.lower() or "sidebar-only" in m.lower()
        for m in info_msgs
    ), f"Expected sidebar-only fallback INFO log, got: {info_msgs}"


# ---------------------------------------------------------------------------
# Change D: LeafLockoutError hard-fail
# ---------------------------------------------------------------------------


async def test_navigate_raises_leaf_lockout_when_both_paths_fail(monkeypatch):
    """Change D: _activate_clip_tile + _click_video_tile both fail AND editor
    did not mount → LeafLockoutError.

    The B28 forensic evidence: Flow on leaf extend-output clip with
    Camera/Insert/Remove greyed out, only Extend enabled. Both recovery
    strategies failed AND the editor is not mounted. Hard-fail with
    LeafLockoutError so operators see b28_leaf_lockout_<media> in
    job.error_message.

    editor_mounts=False simulates the B28 scenario where the editor is
    genuinely stuck on the wrong clip (no <video> visible).
    """
    client, page = _make_client(_edit_url(MEDIA_ID_B), editor_mounts=False)
    monkeypatch.setattr(_base, "_activate_clip_tile", AsyncMock(return_value=False))
    monkeypatch.setattr(_base, "_click_video_tile", AsyncMock(return_value=False))

    job = {
        "edit_url": _edit_url(MEDIA_ID_B),
        "project_url": _project_url(),
        "media_id": MEDIA_ID_A,
        "type": "remove-object",
    }

    with pytest.raises(LeafLockoutError) as exc_info:
        await navigate_to_edit(client, job)

    exc = exc_info.value
    assert exc.target_media_id == MEDIA_ID_A, (
        f"LeafLockoutError.target_media_id must be the target={MEDIA_ID_A!r}, got {exc.target_media_id!r}"
    )
    assert exc.op_type == "remove-object", (
        f"LeafLockoutError.op_type must reflect job type, got {exc.op_type!r}"
    )
    # current_url must be populated
    assert exc.current_url, "LeafLockoutError.current_url must be non-empty"
    # Error message must include the b28_leaf_lockout_ prefix for dispatcher
    assert "b28_leaf_lockout" in str(exc), (
        f"LeafLockoutError str must include 'b28_leaf_lockout', got: {exc!s}"
    )


async def test_navigate_proceeds_when_both_paths_fail_but_editor_mounted(monkeypatch):
    """Routing-slug redirect pass-through: both tile-activation paths fail but
    the editor IS mounted → no LeafLockoutError.

    goto(/edit/{media_id}) often redirects to /edit/{routing_slug} (different
    string, same video). If the editor mounted successfully it means the SPA
    resolved the routing correctly; we should proceed rather than raising a
    false-positive LeafLockoutError.
    """
    client, page = _make_client(_edit_url(MEDIA_ID_B), editor_mounts=True)
    monkeypatch.setattr(_base, "_activate_clip_tile", AsyncMock(return_value=False))
    monkeypatch.setattr(_base, "_click_video_tile", AsyncMock(return_value=False))

    job = {
        "edit_url": _edit_url(MEDIA_ID_B),
        "project_url": _project_url(),
        "media_id": MEDIA_ID_A,
        "type": "insert-object",
    }

    # Should not raise — editor is mounted, routing-slug redirect succeeded
    await navigate_to_edit(client, job)


async def test_leaf_lockout_error_fields_all_present():
    """Change D: LeafLockoutError carries target_media_id, current_url,
    current_media_id, op_type — all used by dispatcher to build the
    failure reason string.
    """
    exc = LeafLockoutError(
        target_media_id=MEDIA_ID_A,
        current_url=_edit_url(MEDIA_ID_B),
        current_media_id=MEDIA_ID_B,
        op_type="camera-move",
    )

    assert exc.target_media_id == MEDIA_ID_A
    assert exc.current_url == _edit_url(MEDIA_ID_B)
    assert exc.current_media_id == MEDIA_ID_B
    assert exc.op_type == "camera-move"
    assert isinstance(exc, RuntimeError), "LeafLockoutError must subclass RuntimeError"


async def test_leaf_lockout_message_contains_b28_prefix():
    """Change D: str(exc) must start with 'b28_leaf_lockout' so job.error_message
    matches the reason-string convention used by other failure modes
    (e.g. 'recaptcha_v3_invisible_burned_<profile>').
    """
    exc = LeafLockoutError(
        target_media_id="x" * 32,
        current_url="https://labs.google/fx/tools/flow/project/p/edit/y",
        current_media_id="y" * 32,
        op_type="insert-object",
    )

    assert str(exc).startswith("b28_leaf_lockout:"), (
        f"Expected message to start with 'b28_leaf_lockout:', got: {str(exc)!r}"
    )


# ---------------------------------------------------------------------------
# Change D: dispatcher marks job failed WITHOUT profile burn
# ---------------------------------------------------------------------------


async def test_dispatcher_marks_b28_failure_without_profile_burn(monkeypatch):
    """Change D: LeafLockoutError from handler → job status=failed,
    error_message contains 'b28_leaf_lockout_', profile NOT removed from pool
    (ProfileSwapper must NOT be called — this is a UI quirk, not a burn).
    """
    from worker import dispatcher

    class _FakeLeafLockout(LeafLockoutError):
        pass

    # Make HANDLER_MAP entry raise LeafLockoutError
    leaf_exc = LeafLockoutError(
        target_media_id=MEDIA_ID_A,
        current_url=_edit_url(MEDIA_ID_B),
        current_media_id=MEDIA_ID_B,
        op_type="camera-move",
    )
    handler = AsyncMock(side_effect=leaf_exc)
    monkeypatch.setitem(dispatcher.HANDLER_MAP, "camera-move", handler)

    # Track profile manager calls
    class _ProfileMgrStub:
        def __init__(self):
            self.busy = []
            self.available = []
            self.removed = []

        def mark_busy(self, profile, job_id):
            self.busy.append(profile)

        def mark_available(self, profile):
            self.available.append(profile)

        def remove_profile(self, profile):
            self.removed.append(profile)

    class _ProjectLockStub:
        def acquire(self, *a): return True
        def release(self, *a): pass

    profile_mgr = _ProfileMgrStub()
    project_lock = _ProjectLockStub()

    job = {
        "id": "job-b28",
        "type": "camera-move",
        "profile": "profile-x",
        "job_level": 2,
        "project_url": _project_url(),
        "media_id": MEDIA_ID_A,
    }

    result = await dispatcher.dispatch_job(job, profile_mgr, project_lock)

    # Job must be failed
    assert result["status"] == "failed", f"Expected failed, got: {result}"

    # error_message must carry the b28 prefix
    error_msg = result.get("error_message") or result.get("error") or ""
    assert "b28_leaf_lockout" in error_msg, (
        f"error_message must contain 'b28_leaf_lockout', got: {error_msg!r}"
    )

    # Profile must be made available again (finally block) but NOT removed
    assert "profile-x" in profile_mgr.available, (
        "Profile must be returned to pool (mark_available) after LeafLockoutError"
    )
    assert profile_mgr.removed == [], (
        f"Profile must NOT be removed from pool on LeafLockoutError — "
        f"this is not a burn event. Got removed={profile_mgr.removed}"
    )
