from unittest.mock import AsyncMock

import httpx

from worker.remote_api import RemoteAPI


def _response(status_code: int, body: dict) -> httpx.Response:
    request = httpx.Request("POST", "http://test/api/worker/claim-batch")
    return httpx.Response(status_code, json=body, request=request)


async def test_claim_batch_returns_empty_list_on_empty(monkeypatch):
    api = RemoteAPI("http://test", "worker-1")
    request_mock = AsyncMock(return_value=_response(200, {"jobs": []}))
    monkeypatch.setattr(api, "_request", request_mock)

    jobs = await api.claim_batch(["profile-a"], max_size=3)

    assert jobs == []
    request_mock.assert_awaited_once_with(
        "POST",
        "/api/worker/claim-batch",
        json={
            "worker_id": "worker-1",
            "profiles": ["profile-a"],
            "max_size": 3,
        },
    )


async def test_claim_batch_returns_job_list_on_happy_path(monkeypatch):
    api = RemoteAPI("http://test", "worker-1")
    request_mock = AsyncMock(return_value=_response(200, {"jobs": [{"id": "job-1"}]}))
    monkeypatch.setattr(api, "_request", request_mock)

    jobs = await api.claim_batch(["profile-a"])

    assert jobs == [{"id": "job-1"}]


async def test_claim_batch_retries_transient_failures(monkeypatch):
    api = RemoteAPI("http://test", "worker-1")
    client = AsyncMock()
    client.is_closed = False
    client.request = AsyncMock(side_effect=[
        httpx.ConnectError("boom-1"),
        httpx.ConnectTimeout("boom-2"),
        _response(200, {"jobs": [{"id": "job-1"}]}),
    ])
    sleep_mock = AsyncMock()

    monkeypatch.setattr(api, "_get_client", AsyncMock(return_value=client))
    monkeypatch.setattr("asyncio.sleep", sleep_mock)

    jobs = await api.claim_batch(["profile-a"], max_size=2)

    assert jobs == [{"id": "job-1"}]
    assert client.request.await_count == 3
    assert [call.args for call in sleep_mock.await_args_list] == [(2.0,), (4.0,)]
