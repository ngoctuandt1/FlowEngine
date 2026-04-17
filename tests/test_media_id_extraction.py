"""Tests for media_id extraction and persistence after operations.

Covers the acceptance criteria of issue #2:
  1. Every completed job has a non-empty media_id
  2. Level-2 jobs can read parent.media_id without a grandparent fallback
  3. Applies to all five bg_* / operation entry points
"""

import asyncio
import types
import pytest

from flow.operations._base import extract_final_media_id


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class _FakePage:
    def __init__(self, url: str = "", video_src: str = ""):
        self.url = url
        self._video_src = video_src

    async def evaluate(self, _script):
        return self._video_src


class _FakeClient:
    def __init__(self, url: str = "", events=None, video_src: str = ""):
        self.page = _FakePage(url=url, video_src=video_src)
        self._media_id_events = events or []


GOOD_MID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
OTHER_MID = "11112222-3333-4444-5555-666677778888"


# ---------------------------------------------------------------------------
# Extraction order / sources
# ---------------------------------------------------------------------------

def test_extracts_from_edit_url():
    client = _FakeClient(
        url=f"https://labs.google/fx/tools/flow/project/proj-1/edit/{GOOD_MID}"
    )
    assert asyncio.run(extract_final_media_id(client)) == GOOD_MID


def test_falls_back_to_media_id_events_when_url_missing():
    client = _FakeClient(
        url="https://labs.google/fx/tools/flow/project/proj-1",
        events=[{"mid": GOOD_MID, "source": "response_url"}],
    )
    assert asyncio.run(extract_final_media_id(client)) == GOOD_MID


def test_falls_back_to_dom_video_src_when_url_and_events_empty():
    client = _FakeClient(
        url="https://labs.google/fx/tools/flow/project/proj-1",
        events=[],
        video_src=f"https://storage.googleapis.com/flow/v1?name={GOOD_MID}&alt=media",
    )
    assert asyncio.run(extract_final_media_id(client)) == GOOD_MID


def test_falls_back_to_job_media_id_for_in_place_edits():
    client = _FakeClient(
        url="https://labs.google/fx/tools/flow/project/proj-1",
        events=[],
        video_src="",
    )
    job = {"media_id": GOOD_MID}
    assert asyncio.run(extract_final_media_id(client, job)) == GOOD_MID


def test_returns_none_when_no_source_produces_id():
    client = _FakeClient(url="", events=[], video_src="")
    assert asyncio.run(extract_final_media_id(client, job={})) is None


def test_url_takes_precedence_over_events():
    """If the browser landed on /edit/{id}, that id wins over older capture."""
    client = _FakeClient(
        url=f"https://labs.google/fx/tools/flow/project/proj-1/edit/{GOOD_MID}",
        events=[{"mid": OTHER_MID, "source": "response_url"}],
    )
    assert asyncio.run(extract_final_media_id(client)) == GOOD_MID


def test_newest_event_wins_when_multiple_captured():
    client = _FakeClient(
        url="https://labs.google/fx/tools/flow/project/proj-1",
        events=[
            {"mid": OTHER_MID, "source": "response_url"},
            {"mid": GOOD_MID, "source": "response_url"},
        ],
    )
    assert asyncio.run(extract_final_media_id(client)) == GOOD_MID


def test_ignores_malformed_event_ids():
    client = _FakeClient(
        url="https://labs.google/fx/tools/flow/project/proj-1",
        events=[
            {"mid": "not-a-valid-id"},
            {"mid": GOOD_MID},
            {"mid": "also-invalid"},
        ],
    )
    # Newest valid wins — last event is invalid, one before is valid.
    assert asyncio.run(extract_final_media_id(client)) == GOOD_MID


# ---------------------------------------------------------------------------
# Dispatcher invariant — AC1
# ---------------------------------------------------------------------------

def test_dispatcher_downgrades_completed_without_media_id_to_failed():
    """If extraction fails, a completed job must NOT be persisted without media_id."""
    from worker.dispatcher import dispatch_job

    # Stub out collaborators
    class _PM:
        def mark_busy(self, *a, **k): pass
        def mark_available(self, *a, **k): pass

    class _PL:
        def acquire(self, *a, **k): return True
        def release(self, *a, **k): pass

    # Register a stub handler that returns a result WITHOUT media_id.
    from worker import dispatcher as disp
    async def _stub_handler(job):
        return {
            "project_url": "https://labs.google/fx/tools/flow/project/p",
            "media_id": "",           # extraction failed
            "output_files": ["f.mp4"],
            "edit_url": "",
            "generation_id": None,
        }

    original = disp.HANDLER_MAP.get("text-to-video")
    disp.HANDLER_MAP["text-to-video"] = _stub_handler
    try:
        job = {"id": "j1", "type": "text-to-video", "profile": "default", "job_level": 1}
        result = asyncio.run(dispatch_job(job, _PM(), _PL()))
        assert result["status"] == "failed"
        assert "media_id" in result.get("error", "")
    finally:
        if original is not None:
            disp.HANDLER_MAP["text-to-video"] = original


def test_dispatcher_keeps_completed_when_media_id_present():
    from worker.dispatcher import dispatch_job
    from worker import dispatcher as disp

    class _PM:
        def mark_busy(self, *a, **k): pass
        def mark_available(self, *a, **k): pass

    class _PL:
        def acquire(self, *a, **k): return True
        def release(self, *a, **k): pass

    async def _stub_handler(job):
        return {
            "project_url": "https://labs.google/fx/tools/flow/project/p",
            "media_id": GOOD_MID,
            "output_files": ["f.mp4"],
            "edit_url": f"https://labs.google/fx/tools/flow/project/p/edit/{GOOD_MID}",
            "generation_id": None,
        }

    original = disp.HANDLER_MAP.get("extend-video")
    disp.HANDLER_MAP["extend-video"] = _stub_handler
    try:
        job = {
            "id": "j2", "type": "extend-video", "profile": "default",
            "job_level": 2, "project_url": "https://labs.google/fx/tools/flow/project/p",
            "media_id": "parent-mid",
        }
        result = asyncio.run(dispatch_job(job, _PM(), _PL()))
        assert result["status"] == "completed"
        assert result["media_id"] == GOOD_MID
    finally:
        if original is not None:
            disp.HANDLER_MAP["extend-video"] = original
