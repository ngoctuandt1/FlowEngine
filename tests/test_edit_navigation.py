"""Tests for Bug #5 — Navigate by media_id URL instead of grid index.

Acceptance criteria verified:
  AC1. When ``job.media_id`` is present, the pipeline navigates to
       ``/edit/{media_id}`` and does not scan the grid.
  AC2. Grid reordering between jobs does not change which video is edited
       (covered implicitly — the grid is never consulted when media_id is set).
  AC3. Legacy jobs without ``media_id`` still work via the tile-click fallback.
"""

import asyncio
import sys
import types

import pytest

from flow.operations import _base as base_mod
from flow.operations._base import navigate_to_edit


GOOD_MID = "1eb6fea7-f1d4-4fcc-a25f-7ca3e06470be"
PROJ_ID = "5b2553ab-e048-48ab-acfd-62936219ceb6"
PROJECT_URL = f"https://labs.google/fx/tools/flow/project/{PROJ_ID}"
EDIT_URL = f"{PROJECT_URL}/edit/{GOOD_MID}"


class _FakePage:
    """Minimal Playwright Page stand-in that records goto() calls.

    ``on_goto`` lets a test control the URL the page lands on after
    navigation (simulating SPA redirects or direct edit loads).
    """

    def __init__(self, initial_url: str = "about:blank", on_goto=None):
        self.url = initial_url
        self.goto_calls: list[str] = []
        self._on_goto = on_goto or (lambda target: target)

    async def goto(self, target, wait_until=None, timeout=None):
        self.goto_calls.append(target)
        self.url = self._on_goto(target)


class _FakeClient:
    def __init__(self, page):
        self.page = page
        self.profile_name = "test-profile"


@pytest.fixture(autouse=True)
def _fast_sleep(monkeypatch):
    """Skip the real sleeps inside navigate_to_edit so tests run instantly."""
    async def _noop(_):
        return None
    monkeypatch.setattr(base_mod.asyncio, "sleep", _noop)


@pytest.fixture(autouse=True)
def _stub_login(monkeypatch):
    """Assume no login redirect in any test."""
    monkeypatch.setattr(base_mod, "is_login_page", lambda url: False)


# ---------------------------------------------------------------------------
# AC1 + AC2 — media_id present → direct /edit/ navigation, no grid scan
# ---------------------------------------------------------------------------

def test_navigates_directly_to_edit_url_when_media_id_present(monkeypatch):
    """When media_id is set, navigation must go straight to /edit/{media_id}."""
    page = _FakePage(on_goto=lambda target: target)  # land wherever we're told
    client = _FakeClient(page)

    tile_click_calls = []
    async def _fake_tile_click(page, media_id=""):
        tile_click_calls.append(media_id)
        return True
    monkeypatch.setattr(base_mod, "_click_video_tile", _fake_tile_click)

    job = {
        "project_url": PROJECT_URL,
        "media_id": GOOD_MID,
    }

    edit_url_out, project_id, _locale = asyncio.run(navigate_to_edit(client, job))

    # AC1: must have navigated to the /edit/{media_id} URL, not the project grid
    assert page.goto_calls, "expected at least one navigation"
    assert page.goto_calls[0] == EDIT_URL
    # AC1: project grid URL must NOT have been visited
    assert PROJECT_URL not in (c for c in page.goto_calls if c != EDIT_URL)
    # AC2: tile-click / grid-scan path must NOT be used
    assert tile_click_calls == []
    # Returned edit_url is the built one
    assert edit_url_out == EDIT_URL
    assert project_id == PROJ_ID


def test_uses_explicit_edit_url_when_provided(monkeypatch):
    """Job may pre-supply edit_url directly; it must be honored."""
    page = _FakePage(on_goto=lambda target: target)
    client = _FakeClient(page)

    tile_click_calls = []
    async def _fake_tile_click(page, media_id=""):
        tile_click_calls.append(media_id)
        return True
    monkeypatch.setattr(base_mod, "_click_video_tile", _fake_tile_click)

    job = {
        "project_url": PROJECT_URL,
        "media_id": GOOD_MID,
        "edit_url": EDIT_URL,
    }
    edit_url_out, _proj_id, _locale = asyncio.run(navigate_to_edit(client, job))

    assert page.goto_calls[0] == EDIT_URL
    assert tile_click_calls == []
    assert edit_url_out == EDIT_URL


def test_retries_direct_edit_url_if_spa_bounces_to_project(monkeypatch):
    """If the SPA lands on /project/ after /edit/ navigation, re-try edit URL.

    Must NOT fall back to clicking a tile — that is the fragile grid path.
    """
    # First goto: land on project page; second goto: stay on edit URL.
    state = {"count": 0}
    def _on_goto(target):
        state["count"] += 1
        if state["count"] == 1:
            return PROJECT_URL  # SPA redirect
        return target
    page = _FakePage(on_goto=_on_goto)
    client = _FakeClient(page)

    tile_click_calls = []
    async def _fake_tile_click(page, media_id=""):
        tile_click_calls.append(media_id)
        return True
    monkeypatch.setattr(base_mod, "_click_video_tile", _fake_tile_click)

    job = {"project_url": PROJECT_URL, "media_id": GOOD_MID}
    asyncio.run(navigate_to_edit(client, job))

    # Both nav attempts were to the edit URL — never to the project URL as the
    # recovery path, and never via tile click.
    assert page.goto_calls == [EDIT_URL, EDIT_URL]
    assert tile_click_calls == []


# ---------------------------------------------------------------------------
# AC3 — legacy jobs (no media_id) use tile-click fallback
# ---------------------------------------------------------------------------

def test_legacy_job_without_media_id_uses_project_url_and_tile_click(monkeypatch):
    """No media_id → navigate to project grid and click a tile (old behavior)."""
    page = _FakePage(on_goto=lambda target: target)
    client = _FakeClient(page)

    tile_click_calls = []

    async def _fake_tile_click(page, media_id=""):
        tile_click_calls.append(media_id)
        # Simulate tile click successfully entering edit mode.
        page.url = EDIT_URL
        return True

    monkeypatch.setattr(base_mod, "_click_video_tile", _fake_tile_click)

    job = {
        # edit_url pre-supplied so navigate_to_edit doesn't abort; no media_id.
        "project_url": PROJECT_URL,
        "edit_url": EDIT_URL,
    }
    asyncio.run(navigate_to_edit(client, job))

    # Legacy path: first goto is the PROJECT URL, not the edit URL.
    assert page.goto_calls[0] == PROJECT_URL
    # And the tile-click fallback ran.
    assert len(tile_click_calls) == 1


def test_raises_when_neither_edit_url_nor_media_id_available():
    """Can't navigate without either an edit_url or (project_url + media_id)."""
    page = _FakePage()
    client = _FakeClient(page)
    job = {"project_url": PROJECT_URL}  # no media_id, no edit_url
    with pytest.raises(RuntimeError, match="Cannot navigate"):
        asyncio.run(navigate_to_edit(client, job))
