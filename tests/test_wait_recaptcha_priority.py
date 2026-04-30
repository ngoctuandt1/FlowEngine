from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from flow.recaptcha import RecaptchaError
import flow.wait as wait_module


def _client(calls):
    page = SimpleNamespace(url="https://labs.google/fx/tools/flow")
    return SimpleNamespace(
        page=page,
        _calls=calls,
        _video_urls=[],
        _media_id_events=[],
    )


def _stub_wait_helpers(monkeypatch):
    monkeypatch.setattr(wait_module, "_inject_observer", AsyncMock(return_value=None))
    monkeypatch.setattr(
        wait_module,
        "_read_observer",
        AsyncMock(return_value={"progress": 0, "error": "", "new_video": False}),
    )
    monkeypatch.setattr(wait_module, "detect_recaptcha", AsyncMock(return_value=False))


@pytest.mark.asyncio
async def test_wait_prioritizes_recaptcha_over_blocked_403(monkeypatch):
    _stub_wait_helpers(monkeypatch)
    client = _client(
        [
            {
                "url": "https://www.google.com/recaptcha/enterprise/clr?k=6LdsFiUsAAAA",
                "status": 200,
                "ts": 100.0,
            },
            {
                "url": "https://aisandbox-pa.googleapis.com/v1/video:batchAsyncGenerateVideoText",
                "status": 403,
                "ts": 101.0,
            },
        ]
    )

    with pytest.raises(RecaptchaError) as exc_info:
        await wait_module.wait_for_completion(client, job_type="extend-video", timeout=1)

    assert exc_info.value.kind == "v3_invisible"
    assert "recaptcha" in (exc_info.value.url or "")


@pytest.mark.asyncio
async def test_wait_keeps_blocked_403_when_no_recaptcha_signal(monkeypatch):
    _stub_wait_helpers(monkeypatch)
    client = _client(
        [
            {
                "url": "https://aisandbox-pa.googleapis.com/v1/video:batchAsyncGenerateVideoText",
                "status": 403,
                "ts": 100.0,
            }
        ]
    )

    result = await wait_module.wait_for_completion(client, job_type="extend-video", timeout=1)

    assert result["done"] is False
    assert result["error"] == "blocked_403"


@pytest.mark.asyncio
async def test_wait_ignores_healthy_recaptcha_pings(monkeypatch):
    _stub_wait_helpers(monkeypatch)
    client = _client(
        [
            {
                "url": "https://www.google.com/recaptcha/enterprise/clr?k=6LdsFiUsAAAA",
                "status": 200,
                "ts": 100.0,
            },
            {
                "url": "https://www.google.com/recaptcha/enterprise/reload?k=6LdsFiUsAAAA",
                "status": 200,
                "ts": 101.0,
            },
        ]
    )

    result = await wait_module.wait_for_completion(client, job_type="extend-video", timeout=1)

    assert result["done"] is False
    assert result["error"] == "timeout"
