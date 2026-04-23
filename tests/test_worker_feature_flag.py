from unittest.mock import AsyncMock

import pytest


@pytest.fixture
def _fresh_shutdown(monkeypatch):
    from worker import main

    monkeypatch.setattr(main, "_shutdown", main.asyncio.Event())
    return main


class _ProfileManagerStub:
    def get_available(self):
        return ["profile-a"]


async def test_claim_loop_uses_claim_job_when_batch_mode_false(monkeypatch, _fresh_shutdown):
    main = _fresh_shutdown
    api = AsyncMock()
    profile_mgr = _ProfileManagerStub()
    project_lock = object()

    async def _claim_job(_available):
        main._shutdown.set()
        return None

    api.claim_job.side_effect = _claim_job
    monkeypatch.setenv("FLOW_BATCH_MODE", "false")
    monkeypatch.setattr(main, "_sleep_or_shutdown", AsyncMock())

    await main.claim_loop(api, profile_mgr, project_lock)

    api.claim_job.assert_awaited_once_with(["profile-a"])
    api.claim_batch.assert_not_called()


async def test_claim_loop_uses_claim_batch_when_batch_mode_true(monkeypatch, _fresh_shutdown):
    main = _fresh_shutdown
    api = AsyncMock()
    profile_mgr = _ProfileManagerStub()
    project_lock = object()

    async def _claim_batch(_available, max_size):
        main._shutdown.set()
        return []

    api.claim_batch.side_effect = _claim_batch
    monkeypatch.setenv("FLOW_BATCH_MODE", "true")
    monkeypatch.setenv("FLOW_BATCH_SIZE", "7")
    monkeypatch.setattr(main, "_sleep_or_shutdown", AsyncMock())

    await main.claim_loop(api, profile_mgr, project_lock)

    api.claim_batch.assert_awaited_once_with(["profile-a"], max_size=7)
    api.claim_job.assert_not_called()
