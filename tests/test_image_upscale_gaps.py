from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from flow import upscale


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr("asyncio.sleep", AsyncMock())


@pytest.fixture
def image_client():
    page = MagicMock()
    client = SimpleNamespace(page=page)
    return client, page


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
async def test_upscale_and_download_image_retries_when_menu_open_fails_first_attempt(
    monkeypatch, tmp_path, image_client
):
    client, page = image_client
    _patch_image_flow(monkeypatch, page)
    download_obj = object()
    upscale._open_edit_download_menu.side_effect = [False, True]

    async def wait_or_download(_page, downloads):
        downloads.append(download_obj)
        return None

    monkeypatch.setattr(upscale, "_wait_for_download_or_popup", AsyncMock(side_effect=wait_or_download))
    upscale._save_image_download.return_value = str(tmp_path / "img_retry_open.png")

    result = await upscale.upscale_and_download_image(
        client, prefix="img", output_dir=str(tmp_path), media_id="mid"
    )

    assert result == str(tmp_path / "img_retry_open.png")
    assert upscale._open_edit_download_menu.await_count == 2
    upscale._click_menu_image_target.assert_awaited_once_with(page, "2k")
    upscale._save_image_download.assert_awaited_once_with(download_obj, "img", "2k", tmp_path)


@pytest.mark.asyncio
async def test_upscale_and_download_image_busy_timeout_returns_none_after_close_toast(
    monkeypatch, tmp_path, image_client
):
    client, page = image_client
    _patch_image_flow(monkeypatch, page)
    monkeypatch.setattr(upscale, "_wait_for_download_or_popup", AsyncMock(return_value="busy"))
    monkeypatch.setattr(upscale, "_wait_upscale", AsyncMock(return_value="timeout"))

    result = await upscale.upscale_and_download_image(
        client, prefix="img", output_dir=str(tmp_path), media_id="mid"
    )

    assert result is None
    assert upscale._wait_upscale.await_count == 2
    assert upscale._close_toast.await_count == 2
    upscale._capture_download_from_menu.assert_not_awaited()
    upscale._save_image_download.assert_not_awaited()


@pytest.mark.asyncio
async def test_upscale_and_download_image_busy_then_upscale_failed_retries(
    monkeypatch, tmp_path, image_client
):
    client, page = image_client
    _patch_image_flow(monkeypatch, page)
    monkeypatch.setattr(upscale, "_wait_for_download_or_popup", AsyncMock(return_value="busy"))
    monkeypatch.setattr(upscale, "_wait_upscale", AsyncMock(return_value="failed"))

    result = await upscale.upscale_and_download_image(
        client, prefix="img", output_dir=str(tmp_path), media_id="mid"
    )

    assert result is None
    assert upscale._open_edit_download_menu.await_count == 2
    assert upscale._wait_upscale.await_count == 2
    assert upscale._close_toast.await_count == 2
    upscale._capture_download_from_menu.assert_not_awaited()


@pytest.mark.asyncio
async def test_upscale_and_download_image_done_but_capture_returns_none(
    monkeypatch, tmp_path, image_client
):
    client, page = image_client
    _patch_image_flow(monkeypatch, page)
    monkeypatch.setattr(upscale, "_wait_for_download_or_popup", AsyncMock(return_value="done"))
    monkeypatch.setattr(upscale, "_capture_download_from_menu", AsyncMock(return_value=None))

    result = await upscale.upscale_and_download_image(
        client, prefix="img", output_dir=str(tmp_path), media_id="mid"
    )

    assert result is None
    assert upscale._close_toast.await_count == 2
    assert upscale._capture_download_from_menu.await_count == 2
    upscale._save_image_download.assert_not_awaited()


@pytest.mark.asyncio
async def test_upscale_and_download_image_done_but_save_returns_none(
    monkeypatch, tmp_path, image_client
):
    client, page = image_client
    _patch_image_flow(monkeypatch, page)
    download_obj = object()
    monkeypatch.setattr(upscale, "_wait_for_download_or_popup", AsyncMock(return_value="done"))
    monkeypatch.setattr(upscale, "_capture_download_from_menu", AsyncMock(return_value=download_obj))
    monkeypatch.setattr(upscale, "_save_image_download", AsyncMock(return_value=None))

    result = await upscale.upscale_and_download_image(
        client, prefix="img", output_dir=str(tmp_path), media_id="mid"
    )

    assert result is None
    assert upscale._close_toast.await_count == 2
    assert upscale._capture_download_from_menu.await_count == 2
    assert upscale._save_image_download.await_count == 2


@pytest.mark.asyncio
async def test_upscale_and_download_image_busy_done_capture_fails(
    monkeypatch, tmp_path, image_client
):
    client, page = image_client
    _patch_image_flow(monkeypatch, page)
    monkeypatch.setattr(upscale, "_wait_for_download_or_popup", AsyncMock(return_value="busy"))
    monkeypatch.setattr(upscale, "_wait_upscale", AsyncMock(return_value="done"))
    monkeypatch.setattr(upscale, "_capture_download_from_menu", AsyncMock(return_value=None))

    result = await upscale.upscale_and_download_image(
        client, prefix="img", output_dir=str(tmp_path), media_id="mid"
    )

    assert result is None
    assert upscale._wait_upscale.await_count == 2
    assert upscale._close_toast.await_count == 2
    assert upscale._capture_download_from_menu.await_count == 2
    upscale._save_image_download.assert_not_awaited()


@pytest.mark.asyncio
async def test_upscale_and_download_image_busy_done_save_fails(
    monkeypatch, tmp_path, image_client
):
    client, page = image_client
    _patch_image_flow(monkeypatch, page)
    download_obj = object()
    monkeypatch.setattr(upscale, "_wait_for_download_or_popup", AsyncMock(return_value="busy"))
    monkeypatch.setattr(upscale, "_wait_upscale", AsyncMock(return_value="done"))
    monkeypatch.setattr(upscale, "_capture_download_from_menu", AsyncMock(return_value=download_obj))
    monkeypatch.setattr(upscale, "_save_image_download", AsyncMock(return_value=None))

    result = await upscale.upscale_and_download_image(
        client, prefix="img", output_dir=str(tmp_path), media_id="mid"
    )

    assert result is None
    assert upscale._wait_upscale.await_count == 2
    assert upscale._close_toast.await_count == 2
    assert upscale._capture_download_from_menu.await_count == 2
    assert upscale._save_image_download.await_count == 2


@pytest.mark.asyncio
async def test_upscale_and_download_image_swallows_playwright_exception(
    monkeypatch, tmp_path, image_client
):
    client, page = image_client
    _patch_image_flow(monkeypatch, page)
    monkeypatch.setattr(upscale, "_click_menu_image_target", AsyncMock(side_effect=RuntimeError("page closed")))

    result = await upscale.upscale_and_download_image(
        client, prefix="img", output_dir=str(tmp_path), media_id="mid"
    )

    assert result is None
    upscale._wait_for_download_or_popup.assert_not_awaited()
    page.remove_listener.assert_called_once()


@pytest.mark.asyncio
async def test_upscale_and_download_image_removes_listener_on_exit(monkeypatch, tmp_path):
    def make_client():
        page = MagicMock()
        page._listeners = []

        def on(event, callback):
            page._listeners.append((event, callback))

        def remove_listener(event, callback):
            page._listeners.remove((event, callback))

        page.on = MagicMock(side_effect=on)
        page.remove_listener = MagicMock(side_effect=remove_listener)
        return SimpleNamespace(page=page), page

    success_client, success_page = make_client()
    _patch_image_flow(monkeypatch, success_page)
    success_page.on.side_effect = success_page.on.side_effect
    success_page.remove_listener.side_effect = success_page.remove_listener.side_effect
    success_download = object()

    async def wait_or_download(_page, downloads):
        downloads.append(success_download)
        return None

    monkeypatch.setattr(upscale, "_wait_for_download_or_popup", AsyncMock(side_effect=wait_or_download))
    monkeypatch.setattr(
        upscale, "_save_image_download", AsyncMock(return_value=str(tmp_path / "img_success.png"))
    )

    success_result = await upscale.upscale_and_download_image(
        success_client, prefix="img", output_dir=str(tmp_path), media_id="mid"
    )

    assert success_result == str(tmp_path / "img_success.png")
    success_page.on.assert_called_once()
    success_page.remove_listener.assert_called_once()
    assert success_page.remove_listener.call_args.args[0] == "download"
    assert success_page.remove_listener.call_args.args[1] is success_page.on.call_args.args[1]
    assert success_page._listeners == []

    failure_client, failure_page = make_client()
    _patch_image_flow(monkeypatch, failure_page)
    failure_page.on.side_effect = failure_page.on.side_effect
    failure_page.remove_listener.side_effect = failure_page.remove_listener.side_effect
    monkeypatch.setattr(upscale, "_wait_for_download_or_popup", AsyncMock(return_value="failed"))

    failure_result = await upscale.upscale_and_download_image(
        failure_client, prefix="img", output_dir=str(tmp_path), media_id="mid"
    )

    assert failure_result is None
    failure_page.on.assert_called_once()
    failure_page.remove_listener.assert_called_once()
    assert failure_page.remove_listener.call_args.args[0] == "download"
    assert failure_page.remove_listener.call_args.args[1] is failure_page.on.call_args.args[1]
    assert failure_page._listeners == []
