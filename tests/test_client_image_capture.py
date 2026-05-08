from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from flow.client import FlowClient


def _client() -> FlowClient:
    client = FlowClient.__new__(FlowClient)
    client.page = None
    client._video_urls = []
    client._calls = []
    client._media_id_events = []
    client._gen_id = None
    client._image_names = []
    client._account_info = None
    return client


def _response(url: str, body: dict) -> SimpleNamespace:
    return SimpleNamespace(
        url=url,
        status=200,
        headers={},
        request=SimpleNamespace(method="POST"),
        json=AsyncMock(return_value=body),
        text=AsyncMock(return_value=""),
    )


@pytest.mark.asyncio
async def test_image_names_captured_from_batchgenerateimages():
    client = _client()
    response = _response(
        "https://aisandbox-pa.googleapis.com/v1/image:batchGenerateImages",
        {"media": [{"name": "uuid-1"}, {"name": "uuid-2"}]},
    )

    await client._on_response(response)

    assert client._image_names == ["uuid-1", "uuid-2"]


@pytest.mark.asyncio
async def test_image_names_deduplicated():
    client = _client()
    response = _response(
        "https://aisandbox-pa.googleapis.com/v1/image:batchGenerateImages",
        {"media": [{"name": "uuid-1"}]},
    )

    await client._on_response(response)
    await client._on_response(response)

    assert len(client._image_names) == 1


def test_clear_captures_resets_image_names():
    client = _client()
    client._image_names = ["uuid-1"]

    client.clear_captures()

    assert client._image_names == []


def test_pop_image_names_with_offset():
    client = _client()
    client._image_names = ["a", "b", "c"]

    result = client.pop_image_names(before_count=1)

    assert result == ["b", "c"]
    assert client._image_names == ["a", "b", "c"]


@pytest.mark.asyncio
async def test_route_blocking_skipped_when_page_none():
    client = _client()

    result = await client._setup_route_blocking()

    assert result is None
