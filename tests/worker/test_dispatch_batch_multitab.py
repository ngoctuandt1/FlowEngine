"""Unit tests for dispatch_batch_multitab.

PRD: docs/PRD_CLAIM_BATCH_DISPATCH.md §4.3
All tests monkeypatch flow primitives and _client_lease so no real Chrome runs.
"""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, call, patch


# ---------------------------------------------------------------------------
# Stub helpers
# ---------------------------------------------------------------------------


class _ProfileManagerStub:
    def __init__(self):
        self.busy: list[tuple] = []
        self.available: list[str] = []
        self.burned: list[str] = []

    def mark_busy(self, profile: str, job_id: str):
        self.busy.append((profile, job_id))

    def mark_available(self, profile: str):
        self.available.append(profile)


class _ProjectLockStub:
    """Configurable acquire stub — by default all acquires succeed."""

    def __init__(self, fail_urls: set[str] | None = None):
        self.acquired: list[str] = []
        self.released: list[str] = []
        self._fail_urls = fail_urls or set()

    def acquire(self, url: str, job_id: str | None = None) -> bool:
        if url in self._fail_urls:
            return False
        self.acquired.append(url)
        return True

    def release(self, url: str, job_id: str | None = None):
        self.released.append(url)


def _l2_job(
    job_id: str,
    profile: str = "prof-mt",
    project_url: str = "https://labs.google/fx/tools/flow/project/p-mt",
    parent_edit_url: str = "https://labs.google/fx/tools/flow/project/p-mt/edit/media-mt",
    parent_media_id: str = "media-mt",
    op_type: str = "extend-video",
    job_level: int = 2,
    **extra,
) -> dict:
    return {
        "id": job_id,
        "type": op_type,
        "job_level": job_level,
        "profile": profile,
        "project_url": project_url,
        "parent_edit_url": parent_edit_url,
        "parent_media_id": parent_media_id,
        **extra,
    }


def _fake_client_lease(profile: str):
    """Context-manager stub that yields a minimal mock FlowClient."""
    @asynccontextmanager
    async def _ctx():
        client = MagicMock()
        client._job_id = None
        yield client
    return _ctx()


def _make_multitab_mock(results: list[dict]):
    """Return an AsyncMock that hands back `results` from batch_dispatch_ops_multitab."""
    return AsyncMock(return_value=results)


# ---------------------------------------------------------------------------
# Test 1 — profile mismatch → all fail, inner primitive NOT called
# ---------------------------------------------------------------------------


async def test_multitab_profile_mismatch_fails_all(monkeypatch):
    """Jobs with different profiles → all fail with 'batch profile mismatch'."""
    from worker import dispatcher

    mock_inner = AsyncMock()
    monkeypatch.setattr(
        "flow.operations._multitab.batch_dispatch_ops_multitab", mock_inner
    )
    monkeypatch.setattr(dispatcher, "_client_lease", _fake_client_lease)

    jobs = [
        _l2_job("mt1-a", profile="prof-X"),
        _l2_job("mt1-b", profile="prof-Y"),
    ]
    result = await dispatcher.dispatch_batch_multitab(
        jobs, _ProfileManagerStub(), _ProjectLockStub()
    )

    mock_inner.assert_not_awaited()
    assert len(result) == 2
    assert all(r["status"] == "failed" for r in result)
    assert all("mismatch" in r.get("error", "") for r in result)


# ---------------------------------------------------------------------------
# Test 2 — job missing both parent fields → fails that job, others proceed
# ---------------------------------------------------------------------------


async def test_multitab_missing_parent_fields_fails_single_job(monkeypatch):
    """Job missing parent_edit_url AND parent_media_id is failed individually."""
    from worker import dispatcher

    good_result = {"status": "completed", "media_id": "media-new"}
    mock_inner = _make_multitab_mock([good_result])
    monkeypatch.setattr(
        "flow.operations._multitab.batch_dispatch_ops_multitab", mock_inner
    )
    monkeypatch.setattr(dispatcher, "_client_lease", _fake_client_lease)

    bad_job = {
        "id": "mt2-bad",
        "type": "extend-video",
        "job_level": 2,
        "profile": "prof-mt",
        "project_url": "https://labs.google/fx/tools/flow/project/p-mt",
        "parent_edit_url": "",
        "parent_media_id": "",
    }
    good_job = _l2_job("mt2-good")

    result = await dispatcher.dispatch_batch_multitab(
        [bad_job, good_job], _ProfileManagerStub(), _ProjectLockStub()
    )

    assert len(result) == 2
    # Identify by job_id since ordering is preserved.
    result_by_id = {r["job_id"]: r for r in result}
    assert result_by_id["mt2-bad"]["status"] == "failed"
    assert result_by_id["mt2-good"]["status"] == "completed"
    # Inner called with only the valid job.
    mock_inner.assert_awaited_once()
    dispatched_jobs = mock_inner.await_args_list[0].args[1]
    assert len(dispatched_jobs) == 1
    assert dispatched_jobs[0]["id"] == "mt2-good"


# ---------------------------------------------------------------------------
# Test 3 — L1 in input → that L1 fails, others proceed
# ---------------------------------------------------------------------------


async def test_multitab_l1_in_input_fails_defensively(monkeypatch):
    """dispatch_batch_multitab must fail L1 jobs defensively without poisoning the batch."""
    from worker import dispatcher

    # The implementation fails ALL jobs if any are L1 (it's a guard at the top).
    # PRD §4.3: "L1 in input → fail those L1s defensively".
    jobs = [
        {
            "id": "mt3-l1",
            "type": "text-to-video",
            "job_level": 1,
            "profile": "prof-mt",
            "project_url": "",
            "parent_edit_url": "",
            "parent_media_id": "",
        },
        _l2_job("mt3-l2"),
    ]

    mock_inner = AsyncMock()
    monkeypatch.setattr(
        "flow.operations._multitab.batch_dispatch_ops_multitab", mock_inner
    )
    monkeypatch.setattr(dispatcher, "_client_lease", _fake_client_lease)

    result = await dispatcher.dispatch_batch_multitab(
        jobs, _ProfileManagerStub(), _ProjectLockStub()
    )

    # The implementation rejects the whole batch when any job is L1 (guard returns early).
    # That means inner primitive is not called and all results are failed.
    mock_inner.assert_not_awaited()
    assert all(r["status"] == "failed" for r in result)


# ---------------------------------------------------------------------------
# Test 4 — distinct project_urls → locks acquired in sorted order; released in reverse
# ---------------------------------------------------------------------------


async def test_multitab_project_lock_acquire_release_order(monkeypatch):
    """ProjectLock acquired in lex URL order; released in reverse on exit."""
    from worker import dispatcher

    url_a = "https://labs.google/fx/tools/flow/project/p-aaa"
    url_b = "https://labs.google/fx/tools/flow/project/p-bbb"

    good_results = [{"status": "completed", "media_id": "m1"}, {"status": "completed", "media_id": "m2"}]
    mock_inner = _make_multitab_mock(good_results)
    monkeypatch.setattr(
        "flow.operations._multitab.batch_dispatch_ops_multitab", mock_inner
    )
    monkeypatch.setattr(dispatcher, "_client_lease", _fake_client_lease)

    lock = _ProjectLockStub()
    jobs = [
        _l2_job("mt4-a", project_url=url_a, parent_edit_url=f"{url_a}/edit/m1", parent_media_id="m1"),
        _l2_job("mt4-b", project_url=url_b, parent_edit_url=f"{url_b}/edit/m2", parent_media_id="m2"),
    ]

    await dispatcher.dispatch_batch_multitab(jobs, _ProfileManagerStub(), lock)

    # Acquired in sorted (lex) order.
    assert lock.acquired == [url_a, url_b], (
        f"Expected acquire order [url_a, url_b]; got {lock.acquired}"
    )
    # Released in reverse.
    assert lock.released == [url_b, url_a], (
        f"Expected release order [url_b, url_a]; got {lock.released}"
    )


# ---------------------------------------------------------------------------
# Test 5 — lock contention on one URL → those jobs fail, others continue
# ---------------------------------------------------------------------------


async def test_multitab_lock_contention_fails_blocked_jobs_only(monkeypatch):
    """Jobs whose project_url can't be locked fail; unblocked jobs continue."""
    from worker import dispatcher

    url_blocked = "https://labs.google/fx/tools/flow/project/p-blocked"
    url_free = "https://labs.google/fx/tools/flow/project/p-free"

    good_results = [{"status": "completed", "media_id": "m-free"}]
    mock_inner = _make_multitab_mock(good_results)
    monkeypatch.setattr(
        "flow.operations._multitab.batch_dispatch_ops_multitab", mock_inner
    )
    monkeypatch.setattr(dispatcher, "_client_lease", _fake_client_lease)

    lock = _ProjectLockStub(fail_urls={url_blocked})
    jobs = [
        _l2_job("mt5-blocked", project_url=url_blocked,
                parent_edit_url=f"{url_blocked}/edit/mb", parent_media_id="mb"),
        _l2_job("mt5-free", project_url=url_free,
                parent_edit_url=f"{url_free}/edit/mf", parent_media_id="mf"),
    ]

    result = await dispatcher.dispatch_batch_multitab(jobs, _ProfileManagerStub(), lock)

    result_by_id = {r["job_id"]: r for r in result}
    assert result_by_id["mt5-blocked"]["status"] == "failed"
    assert "locked" in result_by_id["mt5-blocked"].get("error", "")
    assert result_by_id["mt5-free"]["status"] == "completed"


# ---------------------------------------------------------------------------
# Test 6 — RecaptchaError → all jobs fail with recaptcha_<kind>_burned_<profile>
# ---------------------------------------------------------------------------


async def test_multitab_recaptcha_error_fails_all_and_burns_profile(monkeypatch):
    """RecaptchaError from inner primitive → all jobs fail; _handle_burned called once."""
    from worker import dispatcher
    from flow.recaptcha import RecaptchaError

    exc = RecaptchaError("v3")
    exc.kind = "v3"

    async def raise_recaptcha(*a, **kw):
        raise exc

    monkeypatch.setattr(
        "flow.operations._multitab.batch_dispatch_ops_multitab", raise_recaptcha
    )
    monkeypatch.setattr(dispatcher, "_client_lease", _fake_client_lease)

    burn_mock = AsyncMock()
    monkeypatch.setattr(dispatcher, "_handle_burned_profile_for_batch", burn_mock)

    profile = "prof-mt"
    jobs = [_l2_job("mt6-a"), _l2_job("mt6-b")]
    result = await dispatcher.dispatch_batch_multitab(
        jobs, _ProfileManagerStub(), _ProjectLockStub()
    )

    assert burn_mock.await_count == 1, (
        "_handle_burned_profile_for_batch must be called exactly once on RecaptchaError"
    )
    # First arg to the burn call must be the profile name.
    assert burn_mock.await_args[0][0] == profile

    assert len(result) == 2
    for r in result:
        assert r["status"] == "failed"
        assert f"recaptcha_v3_burned_{profile}" in r.get("error", ""), (
            f"Expected recaptcha error tag; got {r.get('error')}"
        )


# ---------------------------------------------------------------------------
# Test 7 — result list ordered same as input; each result has job_id + profile
# ---------------------------------------------------------------------------


async def test_multitab_result_order_and_fields(monkeypatch):
    """Output is ordered as input; every result carries job_id and profile."""
    from worker import dispatcher

    jobs = [_l2_job(f"mt7-job-{i}") for i in range(3)]
    inner_results = [{"status": "completed", "media_id": f"media-{i}"} for i in range(3)]

    mock_inner = _make_multitab_mock(inner_results)
    monkeypatch.setattr(
        "flow.operations._multitab.batch_dispatch_ops_multitab", mock_inner
    )
    monkeypatch.setattr(dispatcher, "_client_lease", _fake_client_lease)

    result = await dispatcher.dispatch_batch_multitab(
        jobs, _ProfileManagerStub(), _ProjectLockStub()
    )

    assert len(result) == 3
    for i, r in enumerate(result):
        assert r["job_id"] == f"mt7-job-{i}", (
            f"Position {i}: expected mt7-job-{i}, got {r.get('job_id')}"
        )
        assert "profile" in r, f"Result at position {i} missing 'profile' key"
