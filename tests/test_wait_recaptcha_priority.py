from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from flow.recaptcha import RecaptchaError
from flow.wait import wait_for_completion


def _client(calls):
    page = SimpleNamespace(
        url="https://labs.google/fx/tools/flow",
        evaluate=AsyncMock(return_value=None),
    )
    return SimpleNamespace(
        page=page,
        _calls=calls,
        _video_urls=[],
        _media_id_events=[],
    )


@pytest.mark.asyncio
async def test_wait_prioritizes_recaptcha_over_blocked_403():
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
        await wait_for_completion(client, job_type="extend-video", timeout=1)

    assert exc_info.value.kind == "v3_invisible"
    assert "recaptcha" in (exc_info.value.url or "")


@pytest.mark.asyncio
async def test_wait_keeps_blocked_403_when_no_recaptcha_signal():
    client = _client(
        [
            {
                "url": "https://aisandbox-pa.googleapis.com/v1/video:batchAsyncGenerateVideoText",
                "status": 403,
                "ts": 100.0,
            }
        ]
    )

    result = await wait_for_completion(client, job_type="extend-video", timeout=1)

    assert result["done"] is False
    assert result["error"] == "blocked_403"
