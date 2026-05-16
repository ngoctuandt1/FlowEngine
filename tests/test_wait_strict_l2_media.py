from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import flow.wait as wait_module


FAILURE_KIND = "no_new_media_event_at_chain_child"
CHAIN_CHILD_JOB_TYPES = sorted(wait_module.CHAIN_CHILD_JOB_TYPES)


def _make_client_no_new_media():
    page = SimpleNamespace(url="https://labs.google/fx/tools/flow")
    return SimpleNamespace(
        page=page,
        _calls=[],
        _video_urls=["https://example.test/old.mp4"],
        _media_id_events=[{"mid": "old-mid"}],
        _image_names=[],
    )


def _make_client_with_new_media(mid="new-mid"):
    client = _make_client_no_new_media()
    client._append_new_media = lambda: client._media_id_events.append({"mid": mid})
    return client


def _make_client_with_wait_start_media(mid="wait-start-mid"):
    client = _make_client_no_new_media()
    client._media_id_events.append({"mid": "submit-old-mid"})
    submit_baseline = len(client._media_id_events)
    client._media_id_events.append({"mid": mid})
    return client, submit_baseline


def _stub_wait_helpers(monkeypatch, *, api=None, dom=None):
    monkeypatch.setattr(wait_module, "_inject_observer", AsyncMock(return_value=None))
    monkeypatch.setattr(
        wait_module,
        "_check_api_signals",
        AsyncMock(return_value=api or {"done": False, "error": None, "progress": 0}),
    )
    monkeypatch.setattr(
        wait_module,
        "_read_observer",
        AsyncMock(return_value=dom or {"progress": 0, "error": "", "new_video": False}),
    )
    monkeypatch.setattr(wait_module, "detect_recaptcha", AsyncMock(return_value=False))
    monkeypatch.setattr(wait_module, "detect_recaptcha_in_network", AsyncMock(return_value=None))
    monkeypatch.setattr(wait_module, "_settle_after_done", AsyncMock(return_value=None))
    monkeypatch.setattr(wait_module.asyncio, "sleep", AsyncMock(return_value=None))


def _capture_mock(monkeypatch):
    capture = AsyncMock(
        return_value={"done": False, "media_ids": [], "video_urls": [], "error": FAILURE_KIND}
    )
    monkeypatch.setattr(wait_module, "_result_with_capture", capture)
    return capture


def _assert_strict_capture(capture, *, job_type, method):
    capture.assert_awaited_once()
    args, kwargs = capture.await_args
    assert args[1] == FAILURE_KIND
    assert kwargs["kind"] == FAILURE_KIND
    assert kwargs["extra"]["job_type"] == job_type
    assert kwargs["extra"]["method"] == method
    assert "elapsed_sec" in kwargs["extra"]


@pytest.mark.asyncio
@pytest.mark.parametrize("job_type", CHAIN_CHILD_JOB_TYPES)
async def test_chain_child_api_done_without_new_media_fails(monkeypatch, job_type):
    _stub_wait_helpers(monkeypatch, api={"done": True, "error": None, "progress": 100})
    capture = _capture_mock(monkeypatch)
    client = _make_client_no_new_media()

    result = await wait_module.wait_for_completion(client, job_type=job_type, timeout=5)

    assert result["done"] is False
    assert result["error"] == FAILURE_KIND
    _assert_strict_capture(capture, job_type=job_type, method="reverse_api")


@pytest.mark.asyncio
@pytest.mark.parametrize("job_type", CHAIN_CHILD_JOB_TYPES)
async def test_chain_child_api_done_with_new_media_succeeds(monkeypatch, job_type):
    client = _make_client_with_new_media("new-mid")

    async def api_done(_client):
        client._append_new_media()
        return {"done": True, "error": None, "progress": 100}

    _stub_wait_helpers(monkeypatch)
    monkeypatch.setattr(wait_module, "_check_api_signals", AsyncMock(side_effect=api_done))
    capture = _capture_mock(monkeypatch)

    result = await wait_module.wait_for_completion(client, job_type=job_type, timeout=5)

    assert result["done"] is True
    assert result["media_ids"] == ["new-mid"]
    capture.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("job_type", CHAIN_CHILD_JOB_TYPES)
async def test_chain_child_api_done_uses_submit_baseline_for_strict_guard(
    monkeypatch,
    job_type,
):
    _stub_wait_helpers(monkeypatch, api={"done": True, "error": None, "progress": 100})
    capture = _capture_mock(monkeypatch)
    client, submit_baseline = _make_client_with_wait_start_media()

    result = await wait_module.wait_for_completion(
        client,
        job_type=job_type,
        timeout=5,
        initial_media_count_at_submit=submit_baseline,
    )

    assert result["done"] is True
    assert result["media_ids"] == []
    capture.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("job_type", CHAIN_CHILD_JOB_TYPES)
async def test_chain_child_network_video_without_new_media_fails(monkeypatch, job_type):
    _stub_wait_helpers(monkeypatch)
    capture = _capture_mock(monkeypatch)
    client = _make_client_no_new_media()

    async def record_video(_client):
        client._video_urls.append("https://example.test/new.mp4")
        return None

    monkeypatch.setattr(
        wait_module,
        "detect_recaptcha_in_network",
        AsyncMock(side_effect=record_video),
    )

    result = await wait_module.wait_for_completion(client, job_type=job_type, timeout=5)

    assert result["done"] is False
    assert result["error"] == FAILURE_KIND
    _assert_strict_capture(capture, job_type=job_type, method="network_video")


@pytest.mark.asyncio
@pytest.mark.parametrize("job_type", CHAIN_CHILD_JOB_TYPES)
async def test_chain_child_network_video_uses_submit_baseline_for_strict_guard(
    monkeypatch,
    job_type,
):
    _stub_wait_helpers(monkeypatch)
    capture = _capture_mock(monkeypatch)
    client, submit_baseline = _make_client_with_wait_start_media()

    async def record_video(_client):
        client._video_urls.append("https://example.test/new.mp4")
        return None

    monkeypatch.setattr(
        wait_module,
        "detect_recaptcha_in_network",
        AsyncMock(side_effect=record_video),
    )

    result = await wait_module.wait_for_completion(
        client,
        job_type=job_type,
        timeout=5,
        initial_media_count_at_submit=submit_baseline,
    )

    assert result["done"] is True
    capture.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("job_type", CHAIN_CHILD_JOB_TYPES)
async def test_chain_child_dom_video_without_new_media_fails(monkeypatch, job_type):
    _stub_wait_helpers(
        monkeypatch,
        dom={"progress": 100, "error": "", "new_video": True},
    )
    capture = _capture_mock(monkeypatch)
    client = _make_client_no_new_media()

    result = await wait_module.wait_for_completion(client, job_type=job_type, timeout=5)

    assert result["done"] is False
    assert result["error"] == FAILURE_KIND
    _assert_strict_capture(capture, job_type=job_type, method="dom_observer")


@pytest.mark.asyncio
@pytest.mark.parametrize("job_type", CHAIN_CHILD_JOB_TYPES)
async def test_chain_child_dom_video_uses_submit_baseline_for_strict_guard(
    monkeypatch,
    job_type,
):
    _stub_wait_helpers(
        monkeypatch,
        dom={"progress": 100, "error": "", "new_video": True},
    )
    capture = _capture_mock(monkeypatch)
    client, submit_baseline = _make_client_with_wait_start_media()

    result = await wait_module.wait_for_completion(
        client,
        job_type=job_type,
        timeout=5,
        initial_media_count_at_submit=submit_baseline,
    )

    assert result["done"] is True
    capture.assert_not_awaited()


@pytest.mark.asyncio
async def test_text_to_video_api_done_without_new_media_keeps_legacy_success(monkeypatch):
    _stub_wait_helpers(monkeypatch, api={"done": True, "error": None, "progress": 100})
    capture = _capture_mock(monkeypatch)
    client = _make_client_no_new_media()

    result = await wait_module.wait_for_completion(
        client,
        job_type="text-to-video",
        timeout=5,
    )

    assert result["done"] is True
    assert result["media_ids"] == []
    capture.assert_not_awaited()


@pytest.mark.asyncio
async def test_text_to_image_image_api_path_unchanged(monkeypatch):
    _stub_wait_helpers(monkeypatch)
    capture = _capture_mock(monkeypatch)
    client = _make_client_no_new_media()

    async def record_image(_client):
        client._image_names.append("image-mid")
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
    assert result["media_ids"] == ["image-mid"]
    capture.assert_not_awaited()
