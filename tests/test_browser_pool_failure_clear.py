from types import SimpleNamespace
from unittest.mock import AsyncMock

from flow import client as client_module


async def test_reset_for_next_job_clears_failure_cache():
    client = client_module.FlowClient("profile-a", real_chrome=False)
    client._last_failure_capture = "/tmp/stale.png"
    client._last_failure_kind = "timeout"

    await client.reset_for_next_job()

    assert not hasattr(client, "_last_failure_capture")
    assert not hasattr(client, "_last_failure_kind")


async def test_start_clears_failure_cache_before_launch(monkeypatch):
    fake_pw = SimpleNamespace(stop=AsyncMock())
    fake_entry = SimpleNamespace(start=AsyncMock(return_value=fake_pw))
    start_persistent = AsyncMock()

    monkeypatch.setattr(client_module, "async_playwright", lambda: fake_entry)
    monkeypatch.setattr(client_module.FlowClient, "_start_persistent", start_persistent)
    monkeypatch.setattr(client_module.FlowClient, "_setup_network_hooks", lambda self: None)

    client = client_module.FlowClient("profile-a", real_chrome=False)
    client._last_failure_capture = "/tmp/stale.png"
    client._last_failure_kind = "timeout"

    await client.start()

    assert not hasattr(client, "_last_failure_capture")
    assert not hasattr(client, "_last_failure_kind")
    start_persistent.assert_awaited_once()

    await client.stop()
