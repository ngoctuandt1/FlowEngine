"""Unit tests for dispatch_batch routing logic.

PRD: docs/PRD_CLAIM_BATCH_DISPATCH.md §4.2
All tests monkeypatch the underlying primitives so no real Chrome is spun up.
"""

from unittest.mock import AsyncMock, MagicMock, call


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _l1_fresh(job_id: str, **extra) -> dict:
    return {
        "id": job_id,
        "type": "text-to-video",
        "job_level": 1,
        "project_url": "",
        "profile": "prof-a",
        **extra,
    }


def _l2_op(job_id: str, op_type: str = "extend-video", **extra) -> dict:
    return {
        "id": job_id,
        "type": op_type,
        "job_level": 2,
        "profile": "prof-a",
        "project_url": "https://labs.google/fx/tools/flow/project/p-x",
        "parent_edit_url": "https://labs.google/fx/tools/flow/project/p-x/edit/media-x",
        "parent_media_id": "media-x",
        **extra,
    }


# ---------------------------------------------------------------------------
# Test 1 — empty list → []
# ---------------------------------------------------------------------------


async def test_dispatch_batch_empty_returns_empty():
    """dispatch_batch([]) must return [] without calling any primitive."""
    from worker import dispatcher

    result = await dispatcher.dispatch_batch([], MagicMock(), MagicMock())

    assert result == []


# ---------------------------------------------------------------------------
# Test 2 — N=1 → dispatch_job, result list of length 1 with job_id
# ---------------------------------------------------------------------------


async def test_dispatch_batch_singleton_routes_to_dispatch_job(monkeypatch):
    """N=1 delegates to dispatch_job; result list has length 1 with job_id key."""
    from worker import dispatcher

    fake_result = {"status": "completed", "profile": "prof-a"}
    mock_dispatch_job = AsyncMock(return_value=fake_result)
    monkeypatch.setattr(dispatcher, "dispatch_job", mock_dispatch_job)

    mock_l1 = AsyncMock()
    mock_multitab = AsyncMock()
    monkeypatch.setattr(dispatcher, "dispatch_batch_l1_same_project", mock_l1)
    monkeypatch.setattr(dispatcher, "dispatch_batch_multitab", mock_multitab)

    job = _l1_fresh("j-single")
    result = await dispatcher.dispatch_batch([job], MagicMock(), MagicMock())

    mock_dispatch_job.assert_awaited_once()
    mock_l1.assert_not_awaited()
    mock_multitab.assert_not_awaited()
    assert len(result) == 1
    assert result[0]["job_id"] == "j-single"


# ---------------------------------------------------------------------------
# Test 3 — all-L1 t2v with empty project_url → dispatch_batch_l1_same_project
# ---------------------------------------------------------------------------


async def test_dispatch_batch_all_l1_fresh_routes_to_l1_batch(monkeypatch):
    """All L1 t2v with empty project_url routes to dispatch_batch_l1_same_project."""
    from worker import dispatcher

    sentinel = [{"job_id": f"j-l1-{i}", "status": "completed"} for i in range(3)]
    mock_l1 = AsyncMock(return_value=sentinel)
    mock_multitab = AsyncMock()
    monkeypatch.setattr(dispatcher, "dispatch_batch_l1_same_project", mock_l1)
    monkeypatch.setattr(dispatcher, "dispatch_batch_multitab", mock_multitab)

    jobs = [_l1_fresh(f"j-l1-{i}") for i in range(3)]
    result = await dispatcher.dispatch_batch(jobs, MagicMock(), MagicMock())

    mock_l1.assert_awaited_once()
    mock_multitab.assert_not_awaited()
    assert result is sentinel


# ---------------------------------------------------------------------------
# Test 4 — L1 t2v with project_url set → does NOT route to l1-fresh
# ---------------------------------------------------------------------------


async def test_dispatch_batch_l1_with_project_url_falls_to_mixed_path(monkeypatch):
    """L1 jobs with non-empty project_url must NOT go to dispatch_batch_l1_same_project."""
    from worker import dispatcher

    mock_l1 = AsyncMock()
    mock_multitab = AsyncMock()
    mock_dispatch_job = AsyncMock(return_value={"status": "completed", "profile": "prof-a"})
    monkeypatch.setattr(dispatcher, "dispatch_batch_l1_same_project", mock_l1)
    monkeypatch.setattr(dispatcher, "dispatch_batch_multitab", mock_multitab)
    monkeypatch.setattr(dispatcher, "dispatch_job", mock_dispatch_job)

    jobs = [
        _l1_fresh("j-with-purl-0", project_url="https://labs.google/fx/tools/flow/project/p-exists"),
        _l1_fresh("j-with-purl-1"),  # empty project_url — but mixed, so still falls through
    ]
    # One has project_url → not all-fresh → falls to mixed/sequential path.
    await dispatcher.dispatch_batch(jobs, MagicMock(), MagicMock())

    mock_l1.assert_not_awaited()
    mock_multitab.assert_not_awaited()
    # Sequential fallback via dispatch_job was used.
    assert mock_dispatch_job.await_count == 2


# ---------------------------------------------------------------------------
# Test 5 — all-L2 ops → dispatch_batch_multitab
# ---------------------------------------------------------------------------


async def test_dispatch_batch_all_l2_routes_to_multitab(monkeypatch):
    """All L2+ ops route to dispatch_batch_multitab; l1 path not called."""
    from worker import dispatcher

    sentinel = [{"job_id": f"j-l2-{i}", "status": "completed"} for i in range(3)]
    mock_multitab = AsyncMock(return_value=sentinel)
    mock_l1 = AsyncMock()
    monkeypatch.setattr(dispatcher, "dispatch_batch_multitab", mock_multitab)
    monkeypatch.setattr(dispatcher, "dispatch_batch_l1_same_project", mock_l1)

    jobs = [_l2_op(f"j-l2-{i}") for i in range(3)]
    result = await dispatcher.dispatch_batch(jobs, MagicMock(), MagicMock())

    mock_multitab.assert_awaited_once()
    mock_l1.assert_not_awaited()
    assert result is sentinel


# ---------------------------------------------------------------------------
# Test 6 — L3+ ops also route to dispatch_batch_multitab
# ---------------------------------------------------------------------------


async def test_dispatch_batch_l3_routes_to_multitab(monkeypatch):
    """L3+ ops (level≥2, type in L2_BATCH_OPS) route to dispatch_batch_multitab."""
    from worker import dispatcher

    sentinel = [{"job_id": "j-l3-0", "status": "completed"}]
    mock_multitab = AsyncMock(return_value=sentinel)
    mock_l1 = AsyncMock()
    monkeypatch.setattr(dispatcher, "dispatch_batch_multitab", mock_multitab)
    monkeypatch.setattr(dispatcher, "dispatch_batch_l1_same_project", mock_l1)

    jobs = [
        {**_l2_op("j-l3-0", "camera-move"), "job_level": 3},
        {**_l2_op("j-l3-1", "insert-object"), "job_level": 4},
    ]
    result = await dispatcher.dispatch_batch(jobs, MagicMock(), MagicMock())

    mock_multitab.assert_awaited_once()
    mock_l1.assert_not_awaited()
    assert result is sentinel


# ---------------------------------------------------------------------------
# Test 7 — mixed L1+L2 → sequential dispatch_job; multitab NOT called
# ---------------------------------------------------------------------------


async def test_dispatch_batch_mixed_falls_to_sequential(monkeypatch):
    """Mixed L1+L2 batch falls back to per-job dispatch_job; multitab not called."""
    from worker import dispatcher

    mock_multitab = AsyncMock()
    mock_l1 = AsyncMock()
    mock_dispatch_job = AsyncMock(return_value={"status": "completed", "profile": "prof-a"})
    monkeypatch.setattr(dispatcher, "dispatch_batch_multitab", mock_multitab)
    monkeypatch.setattr(dispatcher, "dispatch_batch_l1_same_project", mock_l1)
    monkeypatch.setattr(dispatcher, "dispatch_job", mock_dispatch_job)

    jobs = [_l1_fresh("j-mix-l1"), _l2_op("j-mix-l2")]
    await dispatcher.dispatch_batch(jobs, MagicMock(), MagicMock())

    mock_multitab.assert_not_awaited()
    mock_l1.assert_not_awaited()
    assert mock_dispatch_job.await_count == 2


# ---------------------------------------------------------------------------
# Test 8 — result ordering preserved
# ---------------------------------------------------------------------------


async def test_dispatch_batch_result_order_preserved(monkeypatch):
    """Output list must be ordered the same as the input list."""
    from worker import dispatcher

    async def fake_l1_batch(jobs, *a, **kw):
        return [{"job_id": j["id"], "status": "completed"} for j in jobs]

    monkeypatch.setattr(dispatcher, "dispatch_batch_l1_same_project", fake_l1_batch)

    jobs = [_l1_fresh(f"order-job-{i}") for i in range(4)]
    result = await dispatcher.dispatch_batch(jobs, MagicMock(), MagicMock())

    assert len(result) == 4
    for i, r in enumerate(result):
        assert r["job_id"] == f"order-job-{i}", (
            f"Position {i}: expected order-job-{i}, got {r['job_id']}"
        )
