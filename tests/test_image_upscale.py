from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from flow import download, upscale


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr("asyncio.sleep", AsyncMock())


@pytest.fixture
def image_client():
    page = MagicMock()
    client = SimpleNamespace(page=page)
    return client, page


class FakeDownload:
    def __init__(self, suggested_filename: str, body: bytes):
        self.suggested_filename = suggested_filename
        self._body = body

    async def save_as(self, path: str) -> None:
        Path(path).write_bytes(self._body)


@pytest.mark.parametrize(
    ("explicit", "env_value", "expected"),
    [
        ("2k", None, "2k"),
        ("4k", None, "4k"),
        ("original", None, "original"),
        ("", None, "original"),
        ("", "2k", "2k"),
        ("", "4K", "4k"),
        ("  2K  ", None, "2k"),
        ("unknown", "4k", "original"),
        ("", "garbage", "original"),
    ],
)
def test_requested_image_quality(monkeypatch, explicit, env_value, expected):
    monkeypatch.delenv(download.IMAGE_QUALITY_ENV, raising=False)
    if env_value is not None:
        monkeypatch.setenv(download.IMAGE_QUALITY_ENV, env_value)
    assert download._requested_image_quality(explicit) == expected


@pytest.mark.parametrize(
    ("suggested", "body", "quality", "expected_suffix"),
    [
        ("flow.jpg", b"\xff\xd8\xff\xe0" + b"x" * 1200, "2k", ".jpg"),
        ("flow.jpeg", b"\xff\xd8\xff\xe0" + b"x" * 1200, "4k", ".jpg"),
        ("flow.png", b"\x89PNG\r\n\x1a\n" + b"x" * 1200, "2k", ".png"),
        ("flow.webp", b"RIFF0000WEBP" + b"x" * 1200, "4k", ".webp"),
        ("flow", b"\x89PNG\r\n\x1a\n" + b"x" * 1200, "2k", ".png"),
        ("flow", b"\xff\xd8\xff\xe0" + b"x" * 1200, "4k", ".jpg"),
    ],
)
@pytest.mark.asyncio
async def test_save_image_download_extensions(
    monkeypatch, tmp_path, suggested, body, quality, expected_suffix
):
    monkeypatch.setattr(upscale.time, "time", lambda: 1234567890)
    result = await upscale._save_image_download(
        FakeDownload(suggested, body),
        prefix="img",
        quality=quality,
        out_dir=tmp_path,
    )
    assert result is not None
    assert Path(result).name == f"img_{quality}_1234567890{expected_suffix}"


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (b"\x89PNG\r\n\x1a\nrest", "image/png"),
        (b"\xff\xd8\xff\xe0rest", "image/jpeg"),
        (b"RIFF\x00\x00\x00\x00WEBPrest", "image/webp"),
        (b"not-an-image", ""),
    ],
)
def test_content_type_from_bytes(body, expected):
    assert upscale._content_type_from_bytes(body) == expected


@pytest.mark.parametrize(
    "text",
    ["1080p ready", "2K ready", "4K ready", "upscale complete", "upscaling done"],
)
def test_done_regex_matches_current_ready_phrases(text):
    assert upscale._DONE_RE.search(text)


@pytest.mark.parametrize("text", ["upscaling", "processing 2k", "processing 4k"])
def test_busy_regex_matches_current_processing_phrases(text):
    assert upscale._BUSY_RE.search(text)


@pytest.mark.parametrize("text", ["", "Error occurred"])
def test_done_and_busy_regex_ignore_non_matching_text(text):
    assert not upscale._DONE_RE.search(text)
    assert not upscale._BUSY_RE.search(text)


def _patch_image_flow(monkeypatch, page):
    monkeypatch.setattr(upscale, "_ensure_edit_view", AsyncMock())
    monkeypatch.setattr(upscale, "_open_edit_download_menu", AsyncMock(return_value=True))
    monkeypatch.setattr(upscale, "_click_menu_image_target", AsyncMock(return_value=True))
    monkeypatch.setattr(upscale, "_close_toast", AsyncMock())
    monkeypatch.setattr(upscale, "_wait_upscale", AsyncMock(return_value="done"))
    monkeypatch.setattr(upscale, "_wait_for_download_or_popup", AsyncMock(return_value=None))
    monkeypatch.setattr(upscale, "_capture_download_from_menu", AsyncMock())
    monkeypatch.setattr(upscale, "_save_image_download", AsyncMock())
    page.on = MagicMock()
    page.remove_listener = MagicMock()


@pytest.mark.asyncio
async def test_upscale_and_download_image_immediate_download(monkeypatch, tmp_path, image_client):
    client, page = image_client
    _patch_image_flow(monkeypatch, page)
    download_obj = object()

    async def wait_or_download(_page, downloads):
        downloads.append(download_obj)
        return None

    monkeypatch.setattr(upscale, "_wait_for_download_or_popup", AsyncMock(side_effect=wait_or_download))
    upscale._save_image_download.return_value = str(tmp_path / "img_2k.jpg")
    result = await upscale.upscale_and_download_image(client, prefix="img", output_dir=str(tmp_path), media_id="mid")
    assert result == str(tmp_path / "img_2k.jpg")
    upscale._save_image_download.assert_awaited_once_with(download_obj, "img", "2k", tmp_path)


@pytest.mark.asyncio
async def test_upscale_and_download_image_busy_then_done_redownload(monkeypatch, tmp_path, image_client):
    client, page = image_client
    _patch_image_flow(monkeypatch, page)
    monkeypatch.setattr(upscale, "_wait_for_download_or_popup", AsyncMock(return_value="busy"))
    download_obj = object()
    upscale._capture_download_from_menu.return_value = download_obj
    upscale._save_image_download.return_value = str(tmp_path / "img_2k.png")
    result = await upscale.upscale_and_download_image(client, prefix="img", output_dir=str(tmp_path), media_id="mid")
    assert result == str(tmp_path / "img_2k.png")
    upscale._wait_upscale.assert_awaited_once_with(page, upscale.UPSCALE_TIMEOUT_SEC)
    upscale._capture_download_from_menu.assert_awaited_once()


@pytest.mark.asyncio
async def test_upscale_and_download_image_done_toast_redownloads(monkeypatch, tmp_path, image_client):
    client, page = image_client
    _patch_image_flow(monkeypatch, page)
    monkeypatch.setattr(upscale, "_wait_for_download_or_popup", AsyncMock(return_value="done"))
    download_obj = object()
    upscale._capture_download_from_menu.return_value = download_obj
    upscale._save_image_download.return_value = str(tmp_path / "img_4k.webp")
    result = await upscale.upscale_and_download_image(
        client, prefix="img", output_dir=str(tmp_path), media_id="mid", target_quality="4k"
    )
    assert result == str(tmp_path / "img_4k.webp")
    upscale._close_toast.assert_awaited_once_with(page)
    upscale._save_image_download.assert_awaited_once_with(download_obj, "img", "4k", tmp_path)


@pytest.mark.asyncio
async def test_upscale_and_download_image_failed_then_retry_succeeds(monkeypatch, tmp_path, image_client):
    client, page = image_client
    _patch_image_flow(monkeypatch, page)
    download_obj = object()

    async def wait_or_download(_page, downloads):
        if wait_or_download.calls == 0:
            wait_or_download.calls += 1
            return "failed"
        downloads.append(download_obj)
        return None

    wait_or_download.calls = 0
    monkeypatch.setattr(upscale, "_wait_for_download_or_popup", AsyncMock(side_effect=wait_or_download))
    upscale._save_image_download.return_value = str(tmp_path / "img_2k_retry.jpg")
    result = await upscale.upscale_and_download_image(client, prefix="img", output_dir=str(tmp_path), media_id="mid")
    assert result == str(tmp_path / "img_2k_retry.jpg")
    assert upscale._close_toast.await_count == 1
    assert upscale._open_edit_download_menu.await_count == 2


@pytest.mark.asyncio
async def test_upscale_and_download_image_returns_none_when_attempts_exhausted(monkeypatch, tmp_path, image_client):
    client, page = image_client
    _patch_image_flow(monkeypatch, page)
    monkeypatch.setattr(upscale, "_wait_for_download_or_popup", AsyncMock(return_value="failed"))
    result = await upscale.upscale_and_download_image(client, prefix="img", output_dir=str(tmp_path), media_id="mid")
    assert result is None
    assert upscale._close_toast.await_count == 2
    upscale._save_image_download.assert_not_awaited()


@pytest.mark.asyncio
async def test_upscale_and_download_image_returns_none_when_menu_item_missing(monkeypatch, tmp_path, image_client):
    client, page = image_client
    _patch_image_flow(monkeypatch, page)
    upscale._click_menu_image_target.return_value = False
    result = await upscale.upscale_and_download_image(client, prefix="img", output_dir=str(tmp_path), media_id="mid")
    assert result is None
    assert upscale._click_menu_image_target.await_count == 2
    upscale._wait_for_download_or_popup.assert_not_called()


@pytest.mark.asyncio
async def test_upscale_and_download_image_returns_none_on_timeout_without_popup(monkeypatch, tmp_path, image_client):
    client, page = image_client
    _patch_image_flow(monkeypatch, page)
    monkeypatch.setattr(upscale, "_wait_for_download_or_popup", AsyncMock(return_value=None))
    result = await upscale.upscale_and_download_image(client, prefix="img", output_dir=str(tmp_path), media_id="mid")
    assert result is None
    upscale._capture_download_from_menu.assert_not_awaited()
    upscale._save_image_download.assert_not_awaited()


@pytest.mark.asyncio
async def test_download_video_image_branch_iterates_multiple_mids(monkeypatch, tmp_path, image_client):
    client, _ = image_client
    media_ids = ["mid-1", "mid-2", "mid-3"]
    expected = [str(tmp_path / f"{mid}.png") for mid in media_ids]
    monkeypatch.setattr(download, "DOWNLOAD_DIR", str(tmp_path))

    upscale_mock = AsyncMock(side_effect=expected)
    api_mock = AsyncMock()
    ui_mock = AsyncMock()
    monkeypatch.setattr(upscale, "upscale_and_download_image", upscale_mock)
    monkeypatch.setattr(download, "_download_via_api", api_mock)
    monkeypatch.setattr(download, "_download_via_ui", ui_mock)

    result = await download.download_video(
        client,
        media_ids=media_ids,
        prefix="img",
        quality="2k",
        media_kind="image",
    )

    assert result == expected
    assert [call.kwargs["media_id"] for call in upscale_mock.await_args_list] == media_ids
    api_mock.assert_not_awaited()
    ui_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_download_video_image_branch_falls_back_to_api_for_failed_mids(
    monkeypatch, tmp_path, image_client
):
    client, _ = image_client
    media_ids = ["mid-1", "mid-2", "mid-3"]
    monkeypatch.setattr(download, "DOWNLOAD_DIR", str(tmp_path))

    async def upscale_side_effect(*args, **kwargs):
        if kwargs["media_id"] == "mid-2":
            return None
        return str(tmp_path / f'{kwargs["media_id"]}.png')

    upscale_mock = AsyncMock(side_effect=upscale_side_effect)
    api_mock = AsyncMock(return_value=str(tmp_path / "mid-2_api.png"))
    ui_mock = AsyncMock()
    monkeypatch.setattr(upscale, "upscale_and_download_image", upscale_mock)
    monkeypatch.setattr(download, "_download_via_api", api_mock)
    monkeypatch.setattr(download, "_download_via_ui", ui_mock)

    result = await download.download_video(
        client,
        media_ids=media_ids,
        prefix="img",
        quality="2k",
        media_kind="image",
    )

    assert result == [
        str(tmp_path / "mid-1.png"),
        str(tmp_path / "mid-2_api.png"),
        str(tmp_path / "mid-3.png"),
    ]
    assert [call.kwargs["media_id"] for call in upscale_mock.await_args_list] == media_ids
    api_mock.assert_awaited_once()
    assert api_mock.await_args.args[1] == "mid-2"
    ui_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_download_video_image_branch_falls_through_when_all_fail(monkeypatch, tmp_path, image_client):
    client, _ = image_client
    media_ids = ["mid-1", "mid-2", "mid-3"]
    monkeypatch.setattr(download, "DOWNLOAD_DIR", str(tmp_path))

    upscale_mock = AsyncMock(return_value=None)
    api_paths = [str(tmp_path / f"{mid}_api.png") for mid in media_ids]
    api_mock = AsyncMock(side_effect=api_paths)
    ui_mock = AsyncMock()
    monkeypatch.setattr(upscale, "upscale_and_download_image", upscale_mock)
    monkeypatch.setattr(download, "_download_via_api", api_mock)
    monkeypatch.setattr(download, "_download_via_ui", ui_mock)

    result = await download.download_video(
        client,
        media_ids=media_ids,
        prefix="img",
        quality="2k",
        media_kind="image",
    )

    assert result == api_paths
    assert [call.kwargs["media_id"] for call in upscale_mock.await_args_list] == media_ids
    assert [call.args[1] for call in api_mock.await_args_list] == media_ids
    ui_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_download_video_image_branch_seeds_all_events_when_media_ids_empty(
    monkeypatch, tmp_path, image_client
):
    client, _ = image_client
    client._media_id_events = [{"mid": "a"}, {"mid": "b"}, {"mid": "c"}]
    monkeypatch.setattr(download, "DOWNLOAD_DIR", str(tmp_path))

    upscale_mock = AsyncMock(
        side_effect=[str(tmp_path / "a.png"), str(tmp_path / "b.png"), str(tmp_path / "c.png")]
    )
    api_mock = AsyncMock()
    ui_mock = AsyncMock()
    monkeypatch.setattr(upscale, "upscale_and_download_image", upscale_mock)
    monkeypatch.setattr(download, "_download_via_api", api_mock)
    monkeypatch.setattr(download, "_download_via_ui", ui_mock)

    result = await download.download_video(
        client,
        media_ids=[],
        prefix="img",
        quality="2k",
        media_kind="image",
    )

    assert result == [str(tmp_path / "a.png"), str(tmp_path / "b.png"), str(tmp_path / "c.png")]
    assert [call.kwargs["media_id"] for call in upscale_mock.await_args_list] == ["a", "b", "c"]
    api_mock.assert_not_awaited()
    ui_mock.assert_not_awaited()
