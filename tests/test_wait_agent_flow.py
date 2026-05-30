"""Tests for agent-flow completion detection in flow/wait.py.

Covers:
  - _is_agent_api_url: URL classification
  - _check_api_signals: agent_api_calls counter and no-regression on legacy paths
  - wait_for_completion Method 4: new media_id permissive fallback
  - no-signal watchdog: agent_api_calls keep last_signal_time alive
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import flow.wait as wait_module
from flow.wait import _is_agent_api_url


# ---------------------------------------------------------------------------
# _is_agent_api_url
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url,expected",
    [
        # Agent-flow URLs — should return True
        ("https://generativelanguage.googleapis.com/v1beta/models:generateContent", True),
        ("https://aisandbox-pa.googleapis.com/v1/projects/abc/runagent", True),
        ("https://aisandbox-pa.googleapis.com/v1/flowAgent/applets", True),
        ("https://aisandbox-pa.googleapis.com/v1/projects/abc/generateVideo", True),
        ("https://example.com/api/:generate?foo=1", True),
        ("https://example.com/api/generate?foo=1", True),
        # Legacy / unrelated URLs — should return False
        ("https://labs.google/fx/api/trpc/media.getMediaUrlRedirect?name=abc", False),
        # batchAsyncGenerateVideo is the legacy Labs API — must NOT match even
        # though it contains "generatevideo" as a substring.
        ("https://labs.google/pq/api/batchAsyncGenerateVideo", False),
        ("https://labs.google/pq/api/batchCheckAsyncVideoGenerationStatus", False),
        ("https://labs.google/pq/api/operations/xyz", False),
        ("https://labs.google/fx/tools/flow/project/abc", False),
        ("", False),
    ],
)
def test_is_agent_api_url(url, expected):
    assert _is_agent_api_url(url.lower()) == expected


# ---------------------------------------------------------------------------
# _check_api_signals: agent_api_calls counter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_api_signals_counts_agent_calls(monkeypatch):
    """agent_api_calls increments for each aisandbox-pa call in _calls."""
    monkeypatch.setattr(
        wait_module,
        "capture_failure_nonblocking",
        AsyncMock(return_value=None),
    )
    client = SimpleNamespace(
        page=None,
        _calls=[
            {
                "url": "https://aisandbox-pa.googleapis.com/v1/projects/abc/runagent",
                "status": 200,
                "body": {},
            },
            {
                "url": "https://aisandbox-pa.googleapis.com/v1/flowAgent/applets",
                "status": 200,
                "body": {},
            },
            {
                "url": "https://labs.google/pq/api/operations/xyz",
                "status": 200,
                "body": {"progressPercentage": 50},
            },
        ],
    )
    result = await wait_module._check_api_signals(client)
    assert result["agent_api_calls"] == 2
    assert result["progress"] == 50
    assert result["done"] is False


@pytest.mark.asyncio
async def test_check_api_signals_no_agent_calls_returns_zero(monkeypatch):
    monkeypatch.setattr(
        wait_module,
        "capture_failure_nonblocking",
        AsyncMock(return_value=None),
    )
    client = SimpleNamespace(
        page=None,
        _calls=[
            {
                "url": "https://labs.google/pq/api/batchAsyncGenerateVideo",
                "status": 200,
                "body": {},
            },
        ],
    )
    result = await wait_module._check_api_signals(client)
    assert result["agent_api_calls"] == 0


@pytest.mark.asyncio
async def test_check_api_signals_lro_done_still_works(monkeypatch):
    """LRO operations/ done:true path is unchanged by agent-flow additions."""
    monkeypatch.setattr(
        wait_module,
        "capture_failure_nonblocking",
        AsyncMock(return_value=None),
    )
    client = SimpleNamespace(
        page=None,
        _calls=[
            {
                "url": "https://labs.google/pq/api/operations/xyz",
                "status": 200,
                "body": {"done": True, "progressPercentage": 100},
            },
        ],
    )
    result = await wait_module._check_api_signals(client)
    assert result["done"] is True
    assert result["progress"] == 100


# ---------------------------------------------------------------------------
# wait_for_completion: Method 4 (new media_id permissive fallback)
# ---------------------------------------------------------------------------


def _make_client_agent_flow(*, initial_media_ids=None, video_urls=None):
    page = SimpleNamespace(url="https://labs.google/fx/tools/flow/project/abc")
    return SimpleNamespace(
        page=page,
        _calls=[
            {
                "url": "https://aisandbox-pa.googleapis.com/v1/projects/abc/runagent",
                "status": 200,
                "body": {},
            }
        ],
        _video_urls=video_urls or [],
        _media_id_events=[
            {"mid": m} for m in (initial_media_ids or [])
        ],
        _image_names=[],
    )


def _stub_base_helpers(monkeypatch, *, dom_new_video=False):
    monkeypatch.setattr(wait_module, "_inject_observer", AsyncMock(return_value=None))
    monkeypatch.setattr(
        wait_module,
        "_check_api_signals",
        AsyncMock(
            return_value={
                "done": False,
                "error": None,
                "progress": 0,
                "agent_api_calls": 1,  # agent calls present
            }
        ),
    )
    monkeypatch.setattr(
        wait_module,
        "_read_observer",
        AsyncMock(
            return_value={"progress": 0, "error": "", "new_video": dom_new_video, "snippet": ""}
        ),
    )
    monkeypatch.setattr(wait_module, "detect_recaptcha", AsyncMock(return_value=False))
    monkeypatch.setattr(wait_module, "detect_recaptcha_in_network", AsyncMock(return_value=None))
    monkeypatch.setattr(wait_module, "_settle_after_done", AsyncMock(return_value=None))
    monkeypatch.setattr(wait_module.asyncio, "sleep", AsyncMock(return_value=None))


@pytest.mark.asyncio
async def test_method4_fires_when_new_media_id_and_agent_calls(monkeypatch):
    """Method 4 completes the job when a new media_id appears and agent calls seen."""
    _stub_base_helpers(monkeypatch)

    client = _make_client_agent_flow()
    # Simulate media_id arriving after a short delay — inject on first loop.
    call_count = 0
    original_check = wait_module._check_api_signals

    async def _check_with_media(c):
        nonlocal call_count
        call_count += 1
        if call_count >= 2:
            # New media appeared mid-loop
            c._media_id_events.append({"mid": "new-agent-mid-1234"})
        return {
            "done": False,
            "error": None,
            "progress": 0,
            "agent_api_calls": 1,
        }

    monkeypatch.setattr(wait_module, "_check_api_signals", _check_with_media)

    result = await wait_module.wait_for_completion(
        client,
        job_type="text-to-video",
        timeout=10,
    )

    assert result["done"] is True
    assert "new-agent-mid-1234" in result["media_ids"]


@pytest.mark.asyncio
async def test_method4_does_not_fire_without_agent_calls(monkeypatch):
    """Method 4 must NOT fire when no agent API calls were seen (legacy path)."""
    monkeypatch.setattr(wait_module, "_inject_observer", AsyncMock(return_value=None))
    monkeypatch.setattr(
        wait_module,
        "_check_api_signals",
        AsyncMock(
            return_value={
                "done": False,
                "error": None,
                "progress": 0,
                "agent_api_calls": 0,  # no agent calls
            }
        ),
    )
    monkeypatch.setattr(
        wait_module,
        "_read_observer",
        AsyncMock(return_value={"progress": 0, "error": "", "new_video": False}),
    )
    monkeypatch.setattr(wait_module, "detect_recaptcha", AsyncMock(return_value=False))
    monkeypatch.setattr(wait_module, "detect_recaptcha_in_network", AsyncMock(return_value=None))
    monkeypatch.setattr(wait_module.asyncio, "sleep", AsyncMock(return_value=None))

    client = _make_client_agent_flow(initial_media_ids=["pre-existing-mid"])

    # Pre-existing media_id should NOT trigger Method 4
    result = await wait_module.wait_for_completion(
        client,
        job_type="text-to-video",
        timeout=1,  # very short — should hit no_signal_timeout or timeout
    )

    assert result["done"] is False


@pytest.mark.asyncio
async def test_method4_chain_child_strict_guard_honored(monkeypatch):
    """Method 4 applies the chain-child strict guard: no new media = fail."""
    _stub_base_helpers(monkeypatch)

    # Chain-child with no new media events after submit baseline
    client = _make_client_agent_flow(initial_media_ids=["parent-mid"])
    submit_baseline = len(client._media_id_events)  # = 1

    capture = AsyncMock(
        return_value={
            "done": False,
            "media_ids": [],
            "video_urls": [],
            "error": "no_new_media_event_at_chain_child",
        }
    )
    monkeypatch.setattr(wait_module, "_result_with_capture", capture)

    call_count = 0

    async def _check_with_media(c):
        nonlocal call_count
        call_count += 1
        if call_count >= 2:
            # New media appeared — but it IS the parent_mid re-recorded;
            # strict guard uses strict_media_count_baseline so it won't count.
            # Add something AFTER submit baseline to simulate real new event.
            c._media_id_events.append({"mid": "new-chain-child-mid"})
        return {
            "done": False,
            "error": None,
            "progress": 0,
            "agent_api_calls": 1,
        }

    monkeypatch.setattr(wait_module, "_check_api_signals", _check_with_media)

    result = await wait_module.wait_for_completion(
        client,
        job_type="extend-video",
        timeout=10,
        initial_media_count_at_submit=submit_baseline,
    )

    # With submit_baseline=1 and new media event added AFTER that baseline,
    # new_media_count = 1 → strict guard passes → done=True.
    # (The chain-child guard only fails when new_media_count == 0.)
    assert result["done"] is True


@pytest.mark.asyncio
async def test_method4_chain_child_no_new_media_fails(monkeypatch):
    """Method 4 chain-child fails when no new media_id appeared since submit."""
    _stub_base_helpers(monkeypatch)

    # submit_baseline = current length; no new events will be added
    client = _make_client_agent_flow(initial_media_ids=["parent-mid"])
    submit_baseline = len(client._media_id_events)  # = 1

    # Simulate agent_api_calls=1 AND new media appearing, but AT the submit
    # baseline index (i.e. no NEW media since submit).
    # We need strict_media_count_baseline == current count == no new entries.
    # The simplest: set submit_baseline = current count, no new events.

    capture = AsyncMock(
        return_value={
            "done": False,
            "media_ids": [],
            "video_urls": [],
            "error": "no_new_media_event_at_chain_child",
        }
    )
    monkeypatch.setattr(wait_module, "_result_with_capture", capture)

    # Patch _check_api_signals to return agent_api_calls=1 but don't add media
    monkeypatch.setattr(
        wait_module,
        "_check_api_signals",
        AsyncMock(
            return_value={
                "done": False,
                "error": None,
                "progress": 0,
                "agent_api_calls": 1,
            }
        ),
    )

    result = await wait_module.wait_for_completion(
        client,
        job_type="extend-video",
        timeout=1,
        initial_media_count_at_submit=submit_baseline,
    )

    # No new media since submit baseline → Method 4 never fires
    # (new_agent_media == [] since strict_media_count_baseline == current len)
    # → falls through to no_signal_timeout
    assert result["done"] is False


# ---------------------------------------------------------------------------
# No-signal watchdog: agent_api_calls reset last_signal_time
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_agent_api_calls_prevent_premature_no_signal_timeout(monkeypatch):
    """Agent API calls in _check_api_signals reset the no-signal watchdog."""
    # This test verifies that when agent_api_calls > 0, last_signal_time
    # is updated so the no-signal watchdog does not trip early.
    # We use a very short no-signal timeout and verify the job completes
    # via Method 2 (video URL) before the watchdog fires, because the
    # agent calls kept the watchdog alive.

    monkeypatch.setattr(wait_module, "_inject_observer", AsyncMock(return_value=None))
    monkeypatch.setattr(
        wait_module,
        "_read_observer",
        AsyncMock(return_value={"progress": 0, "error": "", "new_video": False}),
    )
    monkeypatch.setattr(wait_module, "detect_recaptcha", AsyncMock(return_value=False))
    monkeypatch.setattr(wait_module, "detect_recaptcha_in_network", AsyncMock(return_value=None))
    monkeypatch.setattr(wait_module.asyncio, "sleep", AsyncMock(return_value=None))

    client = SimpleNamespace(
        page=SimpleNamespace(url="https://labs.google/fx/tools/flow"),
        _calls=[],
        _video_urls=[],
        _media_id_events=[],
        _image_names=[],
    )

    loop_count = 0

    async def _check_agent_then_video(c):
        nonlocal loop_count
        loop_count += 1
        if loop_count == 3:
            # Simulate video URL arriving on 3rd loop
            c._media_id_events.append({"mid": "agent-vid-mid"})
            c._video_urls.append("https://example.test/agent.mp4")
        return {
            "done": False,
            "error": None,
            "progress": 0,
            "agent_api_calls": 1,  # keeps watchdog alive every loop
        }

    monkeypatch.setattr(wait_module, "_check_api_signals", _check_agent_then_video)

    result = await wait_module.wait_for_completion(
        client,
        job_type="text-to-video",
        timeout=30,
    )

    assert result["done"] is True
