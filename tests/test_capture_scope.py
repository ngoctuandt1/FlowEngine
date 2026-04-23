"""Per-sibling capture-cursor scoping for batch-mode L2 ops (#38).

Regression from 2026-04-24 live MCP run: two camera-move siblings on one
FlowClient both wrote the SAME media_id because the shared
``_media_id_events`` buffer contained both generations' mids and every
sibling's ``finalize_operation`` picked the first non-parent mid.

Fix: each ``submit_*`` snapshots a cursor into ctx; ``wait_for_completion``
accepts ``initial_media_count`` and filters events to those landing after
the cursor.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from flow.client import FlowClient
from flow.wait import _collect_media_ids


def test_capture_cursor_returns_current_event_count(tmp_path):
    client = FlowClient(profile_name="x", profile_base_dir=str(tmp_path))
    assert client.capture_cursor() == 0
    client._media_id_events.append({"mid": "A", "source": "t", "url": "", "ts": 0})
    assert client.capture_cursor() == 1
    client._media_id_events.append({"mid": "B", "source": "t", "url": "", "ts": 0})
    assert client.capture_cursor() == 2


def test_collect_media_ids_honors_start_index():
    client = SimpleNamespace(_media_id_events=[
        {"mid": "A"}, {"mid": "B"}, {"mid": "C"},
    ])
    assert set(_collect_media_ids(client, start_index=0)) == {"A", "B", "C"}
    assert set(_collect_media_ids(client, start_index=1)) == {"B", "C"}
    assert list(_collect_media_ids(client, start_index=3)) == []


async def test_wait_for_completion_filters_events_by_initial_count(monkeypatch):
    """Two siblings share one buffer — wait returns only the caller's slice."""
    from flow import wait as wait_mod

    # Stub out DOM / observer / sleep so wait_for_completion hits the
    # api-signal-done branch on first iteration.
    async def _noop(*_args, **_kwargs):
        return None
    monkeypatch.setattr(wait_mod, "_inject_observer", _noop)
    monkeypatch.setattr(wait_mod.asyncio, "sleep", AsyncMock())
    monkeypatch.setattr(wait_mod, "detect_recaptcha", AsyncMock(return_value=False))
    monkeypatch.setattr(
        wait_mod, "detect_recaptcha_in_network", AsyncMock(return_value=False)
    )

    def _api_done(_client):
        return {"done": True, "error": None, "progress": 100, "media_ids": []}

    monkeypatch.setattr(wait_mod, "_check_api_signals", _api_done)
    # _settle_after_done is awaited but we don't need its side effects.
    monkeypatch.setattr(wait_mod, "_settle_after_done", AsyncMock())

    page = MagicMock()
    page.url = "https://labs.google/fx/tools/flow/project/p/edit/old"
    client = SimpleNamespace(
        page=page,
        _video_urls=[],
        _media_id_events=[
            {"mid": "sibling1_mid"},
            {"mid": "sibling2_mid"},
        ],
    )

    # Sibling 1 submitted when the buffer was empty → cursor=0.
    res1 = await wait_mod.wait_for_completion(
        client, job_type="camera-move", initial_media_count=0
    )
    assert res1["done"] is True
    assert set(res1["media_ids"]) == {"sibling1_mid", "sibling2_mid"}

    # Sibling 2 submitted after sibling 1's mid landed → cursor=1.
    res2 = await wait_mod.wait_for_completion(
        client, job_type="camera-move", initial_media_count=1
    )
    assert res2["done"] is True
    assert set(res2["media_ids"]) == {"sibling2_mid"}


async def test_wait_for_completion_default_snapshots_at_entry(monkeypatch):
    """Single-op callers (initial_media_count=None) keep pre-#38 behavior:
    only events that land DURING the wait are returned."""
    from flow import wait as wait_mod

    async def _noop(*_args, **_kwargs):
        return None
    monkeypatch.setattr(wait_mod, "_inject_observer", _noop)
    monkeypatch.setattr(wait_mod.asyncio, "sleep", AsyncMock())
    monkeypatch.setattr(wait_mod, "detect_recaptcha", AsyncMock(return_value=False))
    monkeypatch.setattr(
        wait_mod, "detect_recaptcha_in_network", AsyncMock(return_value=False)
    )
    monkeypatch.setattr(
        wait_mod, "_check_api_signals",
        lambda _c: {"done": True, "error": None, "progress": 100, "media_ids": []},
    )
    monkeypatch.setattr(wait_mod, "_settle_after_done", AsyncMock())

    page = MagicMock()
    page.url = "https://labs.google/fx/tools/flow/project/p/edit/old"
    client = SimpleNamespace(
        page=page,
        _video_urls=[],
        # One stale event present at entry (e.g. pre-op noise).
        _media_id_events=[{"mid": "stale"}],
    )

    res = await wait_mod.wait_for_completion(client, job_type="camera-move")
    # default snapshots at entry → the stale mid is excluded.
    assert list(res["media_ids"]) == []


async def test_submit_camera_returns_capture_start_ctx(monkeypatch, tmp_path):
    """submit_camera_move's returned ctx carries the pre-submit cursor."""
    from flow.operations import camera as camera_mod

    # Stub every browser interaction inside submit_camera_move.
    monkeypatch.setattr(
        camera_mod, "navigate_to_edit",
        AsyncMock(return_value=("http://edit", "pid", "en")),
    )
    monkeypatch.setattr(camera_mod, "wait_for_video_loaded", AsyncMock())
    monkeypatch.setattr(camera_mod, "click_action_button", AsyncMock(return_value=True))
    monkeypatch.setattr(camera_mod, "count_visible_cards", AsyncMock(return_value=0))
    monkeypatch.setattr(camera_mod.asyncio, "sleep", AsyncMock())
    monkeypatch.setattr(
        camera_mod, "submit_with_confirmation", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(camera_mod, "_click_preset", AsyncMock(return_value=True))

    # Fake client with a pre-populated mid buffer (simulating a prior
    # sibling's mid already landed).
    client = FlowClient(profile_name="x", profile_base_dir=str(tmp_path))
    client._media_id_events.extend([{"mid": "prior"}])
    client.page = MagicMock()
    tab = MagicMock()
    tab.is_visible = AsyncMock(return_value=True)
    tab.click = AsyncMock()
    client.page.locator = MagicMock(return_value=MagicMock(first=tab))

    ctx = await camera_mod.submit_camera_move(
        client, {"id": "j1", "media_id": "parent"}, direction="Dolly in"
    )

    # Cursor is snapshotted BEFORE submit, so it captures the 1 prior event.
    assert ctx["capture_start"] == 1
    assert ctx["project_id"] == "pid"
    assert ctx["locale"] == "en"
