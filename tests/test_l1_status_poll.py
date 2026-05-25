from __future__ import annotations

from types import SimpleNamespace

import pytest

from flow.operations._l1_status_poll import (
    detect_recaptcha_from_status_response,
    poll_status_via_api,
)
from flow.wait import RecaptchaError


class FakeStatusResponse:
    def __init__(
        self,
        status: int,
        *,
        text: str = "",
        data: dict | None = None,
        url: str = "https://aisandbox-pa.googleapis.com/v1/video:batchCheckAsyncVideoGenerationStatus",
        headers: dict[str, str] | None = None,
    ):
        self.status = status
        self.url = url
        self.headers = headers or {}
        self._text = text
        self._data = data or {}

    async def text(self) -> str:
        return self._text

    async def json(self) -> dict:
        return self._data


class FakeRequestContext:
    def __init__(self, response: FakeStatusResponse):
        self.response = response
        self.posts: list[dict] = []

    async def post(self, url, *, data, headers, timeout):
        self.posts.append(
            {
                "url": url,
                "data": data,
                "headers": headers,
                "timeout": timeout,
            }
        )
        return self.response


def _client(response: FakeStatusResponse):
    request = FakeRequestContext(response)
    page = SimpleNamespace(
        url="https://labs.google/fx/tools/flow/project/project-1",
        context=SimpleNamespace(request=request),
    )
    return SimpleNamespace(page=page, _batch_requests=[])


@pytest.mark.asyncio
async def test_poll_status_raises_recaptcha_error_on_403_json_recaptcha_body():
    client = _client(
        FakeStatusResponse(
            403,
            text='{"captchaRequired": true}',
        )
    )

    with pytest.raises(RecaptchaError) as exc_info:
        await poll_status_via_api(
            client,
            gen_ids=["gen-000000000001"],
            poll_interval_sec=0,
            hard_timeout_sec=1,
        )

    assert exc_info.value.kind == "v3_invisible_or_block"
    assert "status poll" in str(exc_info.value)


@pytest.mark.asyncio
async def test_poll_status_raises_recaptcha_error_on_429_json_list_body():
    client = _client(
        FakeStatusResponse(
            429,
            text='[{"recaptcha": true}]',
        )
    )

    with pytest.raises(RecaptchaError) as exc_info:
        await poll_status_via_api(
            client,
            gen_ids=["gen-000000000001"],
            poll_interval_sec=0,
            hard_timeout_sec=1,
        )

    assert exc_info.value.kind == "v3_invisible_or_block"
    assert "status poll" in str(exc_info.value)


@pytest.mark.asyncio
async def test_poll_status_raises_recaptcha_error_on_403_plain_text_body():
    client = _client(
        FakeStatusResponse(
            403,
            text="blocked by recaptcha",
        )
    )

    with pytest.raises(RecaptchaError) as exc_info:
        await poll_status_via_api(
            client,
            gen_ids=["gen-000000000001"],
            poll_interval_sec=0,
            hard_timeout_sec=1,
        )

    assert exc_info.value.kind == "v3_invisible_or_block"
    assert "status poll" in str(exc_info.value)


@pytest.mark.asyncio
async def test_poll_status_raises_recaptcha_error_on_200_recaptcha_json_payload():
    client = _client(
        FakeStatusResponse(
            200,
            data={
                "error": {
                    "message": (
                        "blocked by "
                        "https://www.google.com/recaptcha/enterprise/reload?k=site"
                    ),
                },
            },
        )
    )

    with pytest.raises(RecaptchaError) as exc_info:
        await poll_status_via_api(
            client,
            gen_ids=["gen-000000000001"],
            poll_interval_sec=0,
            hard_timeout_sec=1,
        )

    assert exc_info.value.kind == "v3_invisible_or_block"
    assert "HTTP 200 JSON payload" in str(exc_info.value)


@pytest.mark.asyncio
async def test_poll_status_200_does_not_raise_recaptcha_error():
    response = FakeStatusResponse(
        200,
        data={
            "media": [
                {
                    "name": "gen-000000000001",
                    "mediaMetadata": {
                        "mediaStatus": {
                            "mediaGenerationStatus": "MEDIA_GENERATION_STATUS_SUCCESSFUL",
                        }
                    },
                    "downloadUrl": "https://example.test/video.mp4",
                }
            ]
        },
    )
    client = _client(response)

    result = await poll_status_via_api(
        client,
        gen_ids=["gen-000000000001"],
        poll_interval_sec=0,
        hard_timeout_sec=1,
    )

    assert result["gen-000000000001"]["status"] == "completed"


def test_detect_recaptcha_from_status_response_detects_redirect_url():
    response = FakeStatusResponse(
        302,
        url="https://www.google.com/recaptcha/api2/anchor?k=site",
    )

    assert detect_recaptcha_from_status_response(response) is True


def test_detect_recaptcha_from_status_response_detects_dict_payload_recaptcha_url():
    payload = {
        "media": [
            {
                "name": "gen-000000000001",
                "error": {
                    "message": (
                        "blocked by "
                        "https://www.google.com/recaptcha/enterprise/reload?k=site"
                    ),
                },
            }
        ]
    }

    assert detect_recaptcha_from_status_response(payload) is True


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"recaptcha": True}, True),
        ({"captchaRequired": True}, True),
        ({"status": {"captchaRequired": True}}, True),
        ({"recaptcha": False}, False),
        ({"some_field": "blocked by recaptcha"}, True),
    ],
)
def test_detect_recaptcha_from_status_response_detects_captcha_payload_shapes(
    payload,
    expected,
):
    assert detect_recaptcha_from_status_response(payload) is expected


def test_detect_recaptcha_from_status_response_ignores_clean_dict_payload():
    payload = {
        "media": [
            {
                "name": "gen-000000000001",
                "mediaMetadata": {
                    "mediaStatus": {
                        "mediaGenerationStatus": "MEDIA_GENERATION_STATUS_PENDING",
                    }
                },
            }
        ]
    }

    assert detect_recaptcha_from_status_response(payload) is False
