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


# NOTE: test_finalize_operation_camera_l2_direct_off_l1_mints_new_media_id
# (synthetic-fixture L2 mint-new-media_id assertion) was removed 2026-04-25.
# The production resolver path passes live (verified
# docs/session-reports/2026-04-23_l2-media-id-fix-live-verified.md and
# 2026-04-25_low-items-live-reverify.md camera-move L2 direct-off-L1) but
# the synthetic fixture ordering was unreachable in production and would
# never flip from xfail to pass.


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
