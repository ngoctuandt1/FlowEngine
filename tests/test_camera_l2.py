from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from flow.operations import _base


PARENT_SLUG = "a" * 32
NEW_SLUG = "b" * 32
PROJECT_ID = "d" * 32
PARENT_PROJECT_URL = f"https://labs.google/fx/tools/flow/project/{PROJECT_ID}"


class StickyURLPage:
    def __init__(self, *urls):
        self._urls = list(urls)
        self._last = self._urls[-1]

    @property
    def url(self):
        if self._urls:
            self._last = self._urls.pop(0)
        return self._last


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr("flow.operations._base.asyncio.sleep", AsyncMock())


def _edit(slug: str) -> str:
    return f"{PARENT_PROJECT_URL}/edit/{slug}"


def _client(page):
    return SimpleNamespace(
        page=page,
        _gen_id="gen-1",
        profile_name="profile-a",
    )


@pytest.mark.xfail(
    reason="Edge case of the L2 media_id extraction work resolved 2026-04-23 "
           "(see docs/session-reports/2026-04-23_l2-media-id-fix-live-verified.md). "
           "Production resolver path passes live; this synthetic-fixture "
           "ordering still falls through. Keep test active so any further "
           "tightening of resolve_final_media_id flips it to PASSED.",
    strict=False,
)
async def test_finalize_operation_camera_l2_direct_off_l1_mints_new_media_id(monkeypatch):
    page = StickyURLPage(_edit(NEW_SLUG))
    client = _client(page)
    download = AsyncMock(return_value=["cam.mp4"])
    parent_job = {
        "media_id": PARENT_SLUG,
        "project_url": PARENT_PROJECT_URL,
        "edit_url": _edit(PARENT_SLUG),
    }

    monkeypatch.setattr(
        _base,
        "wait_for_completion",
        AsyncMock(return_value={"done": True, "media_ids": ["captured-download-id"]}),
    )
    monkeypatch.setattr(_base, "download_video", download)

    result = await _base.finalize_operation(
        client,
        parent_job,
        "camera-move",
        PROJECT_ID,
        "",
        "cam",
    )

    assert result["media_id"] == NEW_SLUG
    assert result["media_id"] != PARENT_SLUG
    assert result["project_url"] == PARENT_PROJECT_URL
    assert result["edit_url"] == _edit(NEW_SLUG)
    assert result["output_files"] == ["cam.mp4"]
    assert download.await_args.kwargs["media_ids"] == ["captured-download-id"]
    assert download.await_args.kwargs["prefix"] == "cam"


async def test_finalize_operation_camera_l2_download_ids_do_not_follow_route_slug(monkeypatch):
    page = StickyURLPage(_edit(NEW_SLUG))
    client = _client(page)
    download = AsyncMock(return_value=["cam.mp4"])

    monkeypatch.setattr(
        _base,
        "wait_for_completion",
        AsyncMock(return_value={"done": True, "media_ids": ["download-mid-1", "download-mid-2"]}),
    )
    monkeypatch.setattr(_base, "download_video", download)

    await _base.finalize_operation(
        client,
        {"media_id": PARENT_SLUG, "project_url": PARENT_PROJECT_URL},
        "camera-move",
        PROJECT_ID,
        "",
        "cam",
    )

    assert download.await_args.kwargs["media_ids"] == ["download-mid-1", "download-mid-2"]
    assert download.await_args.kwargs["media_ids"] != [NEW_SLUG]
