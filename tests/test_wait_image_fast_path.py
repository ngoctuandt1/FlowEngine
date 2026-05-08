from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import flow.wait as wait_module


def _client(image_names=None):
    page = SimpleNamespace(url="https://labs.google/fx/tools/flow")
    return SimpleNamespace(
        page=page,
        _calls=[],
        _video_urls=[],
        _media_id_events=[],
        _image_names=image_names or [],
    )


def _stub_wait_helpers(monkeypatch):
    monkeypatch.setattr(wait_module, "_inject_observer", AsyncMock(return_value=None))
    monkeypatch.setattr(
        wait_module,
        "_read_observer",
        AsyncMock(return_value={"progress": 0, "error": "", "new_video": False}),
    )
    monkeypatch.setattr(wait_module, "detect_recaptcha", AsyncMock(return_value=False))
    monkeypatch.setattr(wait_module, "detect_recaptcha_in_network", AsyncMock(return_value=None))


@pytest.mark.asyncio
async def test_image_fast_path_returns_done_immediately(monkeypatch):
    _stub_wait_helpers(monkeypatch)
    client = _client()

    async def record_image(_client):
        client._image_names.append("uuid-1")
        return None

    monkeypatch.setattr(
        wait_module,
        "detect_recaptcha_in_network",
        AsyncMock(side_effect=record_image),
    )

    result = await wait_module.wait_for_completion(
        client,
        job_type="text-to-image",
        timeout=5,
    )

    assert result["done"] is True
    assert result["media_ids"] == ["uuid-1"]


@pytest.mark.asyncio
async def test_image_fast_path_ignores_pre_existing_names(monkeypatch):
    _stub_wait_helpers(monkeypatch)
    client = _client(["old-uuid"])

    result = await wait_module.wait_for_completion(
        client,
        job_type="text-to-image",
        timeout=1,
    )

    assert result["done"] is False
    assert result["error"] == "timeout"


@pytest.mark.asyncio
async def test_non_image_job_type_ignores_image_names(monkeypatch):
    _stub_wait_helpers(monkeypatch)
    client = _client(["uuid-1"])

    result = await wait_module.wait_for_completion(
        client,
        job_type="text-to-video",
        timeout=1,
    )

    assert result["done"] is False
    assert result["error"] == "timeout"


def test_text_to_image_timeout_constant():
    assert wait_module.TIMEOUTS["text-to-image"] == 120


def test_text_to_image_no_signal_timeout_constant():
    assert wait_module.NO_SIGNAL_TIMEOUTS["text-to-image"] == 60
