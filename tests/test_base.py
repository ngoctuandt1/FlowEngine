"""B14 — unit tests for nav verify + media_id-aware tile click in `_base.py`.

Cherry-picks from `stash@{0}` §7 KEEP-2 + KEEP-3 (see
`docs/session-reports/2026-04-17_stash-triage_flow-refinements.md`):

- **KEEP-2** — After `navigate_to_edit`, verify the URL landed in `/edit/`
  mode. If the resolved `media_id` differs from the requested one, log a
  WARNING and proceed (Flow SPA sometimes redirects to a sibling video in
  the same project; caller still sees *some* edit mode).
- **KEEP-3** — `_click_video_tile` prioritises a JS-side click whose
  link/data-attribute matches the target `media_id`. Master fell back to
  clicking *any* visible video, which in a multi-video project could pick
  the wrong tile (violates INV-5 media_id stability).

Both are mocked at the Playwright boundary — no browser runtime. Sleeps
are stubbed out via an autouse fixture so the 2s / 3s render waits inside
`_click_video_tile` don't inflate test runtime.

The existing bbox-canvas helper (`draw_bbox_on_video`) is orthogonal and
covered by `tests/test_bbox.py`.
"""

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from flow.operations import _base
from flow.operations._base import _click_video_tile, navigate_to_edit


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


def _make_client(url: str, profile: str = "test-profile"):
    """Mock client+page where `page.url` returns the given string.

    `page.goto` is a no-op AsyncMock — it does NOT mutate `page.url`, so
    the test controls what the navigation code sees at each read."""
    client = MagicMock()
    client.profile_name = profile
    page = MagicMock()
    page.url = url
    page.goto = AsyncMock()
    page.locator = MagicMock()
    client.page = page
    return client, page


# ---------------------------------------------------------------------------
# KEEP-2: navigate_to_edit post-nav verify + mismatch warning
# ---------------------------------------------------------------------------


async def test_navigate_warns_on_media_id_mismatch(caplog):
    """KEEP-2: URL landed on a DIFFERENT media_id than requested → WARNING,
    but function still returns (non-fatal — INV-5 says media_id stable; SPA
    redirect to a sibling video in the same project is acceptable)."""
    # Requested MEDIA_ID_A, but page ended up on MEDIA_ID_B (both in same
    # project, so /edit/ is present — just a different media UUID).
    client, _page = _make_client(_edit_url(MEDIA_ID_B))

    job = {
        "edit_url": _edit_url(MEDIA_ID_A),
        "project_url": _project_url(),
        "media_id": MEDIA_ID_A,
    }

    with caplog.at_level(logging.WARNING, logger="flow.operations._base"):
        edit_url, project_id, _locale = await navigate_to_edit(client, job)

    # Non-fatal: returns normally
    assert edit_url == _edit_url(MEDIA_ID_A)
    assert project_id == PROJECT_ID

    warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert any(
        "different media" in w.lower()
        and MEDIA_ID_A[:20] in w
        and MEDIA_ID_B[:20] in w
        for w in warnings
    ), f"Expected media_id mismatch WARNING with both ids, got: {warnings}"


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


async def test_navigate_raises_when_not_in_edit_mode(monkeypatch):
    """KEEP-2: after all nav attempts, URL still lacks `/edit/` → RuntimeError.

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

    with pytest.raises(RuntimeError, match="edit mode"):
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
    page.evaluate.assert_not_called()
    tile_loc.first.click.assert_awaited_once()
