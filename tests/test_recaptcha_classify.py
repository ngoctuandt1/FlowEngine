from types import SimpleNamespace

import pytest

from flow.recaptcha import detect_recaptcha_in_network


def _client(*calls):
    return SimpleNamespace(_calls=list(calls))


@pytest.mark.asyncio
async def test_detect_recaptcha_in_network_replays_2026_05_01_live_evidence():
    client = _client(
        {
            "url": "https://www.google.com/recaptcha/enterprise/reload?k=6LdsFiUsAAAA",
            "status": 200,
            "ts": 100.0,
        },
        {
            "url": "https://www.google.com/recaptcha/enterprise/clr?k=6LdsFiUsAAAA",
            "status": 200,
            "ts": 101.0,
        },
        {
            "url": "https://aisandbox-pa.googleapis.com/v1/video:batchAsyncGenerateVideoText",
            "status": 403,
            "ts": 102.0,
        },
    )

    assert await detect_recaptcha_in_network(client) == "v3_invisible"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("calls", "expected"),
    [
        (
            [
                {
                    "url": "https://www.google.com/recaptcha/enterprise/clr?k=6LdsFiUsAAAA",
                    "status": 200,
                    "ts": 100.0,
                }
            ],
            "v3_invisible",
        ),
        (
            [
                {
                    "url": "https://www.google.com/recaptcha/api2/anchor?k=6LdsFiUsAAAA",
                    "status": 200,
                    "ts": 100.0,
                }
            ],
            "v2_visible",
        ),
        (
            [
                {
                    "url": "https://aisandbox-pa.googleapis.com/v1/video:batchAsyncGenerateVideoText",
                    "status": 403,
                    "ts": 100.0,
                }
            ],
            None,
        ),
        (
            [
                {
                    "url": "https://aisandbox-pa.googleapis.com/v1/video:batchAsyncGenerateVideoText",
                    "status": 403,
                    "ts": 100.0,
                },
                {
                    "url": "https://www.google.com/recaptcha/enterprise/clr?k=6LdsFiUsAAAA",
                    "status": 200,
                    "ts": 105.0,
                },
            ],
            "v3_invisible",
        ),
        ([], None),
    ],
)
async def test_detect_recaptcha_in_network_classifies_expected_calls(calls, expected):
    assert await detect_recaptcha_in_network(_client(*calls)) == expected
