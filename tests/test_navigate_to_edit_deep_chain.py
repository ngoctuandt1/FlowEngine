"""Deep-chain navigation recovery tests for Flow edit routing.

L6+ extend chains can show many sibling tiles in the Flow project rail. When
direct `/edit/{media_id}` navigation leaves the editor unmounted, recovery must
click the parent media tile, not blind-click tile[0].
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from flow.operations import _base
from flow.operations._base import _find_tile_by_media_id, navigate_to_edit


PROJECT_ID = "ccd231ae-0fb7-459a-a274-9cbd3b903ee6"
TARGET_MEDIA_ID = "3950beab-fe67-43a8-99dc-6c760667053f"
ROOT_MEDIA_ID = "456c4b4d-61b2-4e16-a814-060bed7118d6"
SLUG_MEDIA_ID = "59abc370-1c26-42fd-a951-09507b96a4f0"


def _edit_url(media_id: str = TARGET_MEDIA_ID) -> str:
    return f"https://labs.google/fx/tools/flow/project/{PROJECT_ID}/edit/{media_id}"


def _project_url() -> str:
    return f"https://labs.google/fx/tools/flow/project/{PROJECT_ID}"


class _Tile:
    def __init__(self, page, selector: str, exists: bool) -> None:
        self.page = page
        self.selector = selector
        self.exists = exists
        self.click = AsyncMock(side_effect=self._click)
        self.wait_for = AsyncMock(side_effect=self._wait_for)
        self.is_visible = AsyncMock(return_value=exists)

    @property
    def first(self):
        return self

    async def count(self) -> int:
        return 1 if self.exists else 0

    async def _click(self, *args, **kwargs) -> None:
        self.page.clicked_selectors.append(self.selector)
        self.page.editor_mounts = True
        self.page.url = _edit_url(TARGET_MEDIA_ID)

    async def _wait_for(self, *args, **kwargs) -> None:
        if not self.exists:
            raise TimeoutError(f"missing {self.selector}")


class _EditorMountLocator:
    def __init__(self, page) -> None:
        self.page = page

    @property
    def first(self):
        return self

    async def wait_for(self, *args, **kwargs) -> None:
        if not self.page.editor_mounts:
            raise TimeoutError("editor not mounted")


class _Page:
    def __init__(self, matches: set[str] | None = None, url: str | None = None) -> None:
        self.url = url or _edit_url()
        self.matches = matches or set()
        self.editor_mounts = False
        self.clicked_selectors: list[str] = []
        self.locators: dict[str, _Tile] = {}
        self.goto = AsyncMock()

    def locator(self, selector: str):
        if selector.startswith("video,"):
            return _EditorMountLocator(self)
        if selector not in self.locators:
            self.locators[selector] = _Tile(self, selector, selector in self.matches)
        return self.locators[selector]


def _client(page: _Page):
    client = MagicMock()
    client.page = page
    client.profile_name = "deep-chain-profile"
    return client


@pytest.fixture(autouse=True)
def _nav_fixtures(monkeypatch):
    monkeypatch.setattr("asyncio.sleep", AsyncMock())
    monkeypatch.setattr(_base, "is_login_page", lambda _url: False)
    monkeypatch.setattr(_base, "_recover_editor_landing", AsyncMock(return_value=False))
    monkeypatch.setattr(_base, "_activate_clip_tile", AsyncMock(return_value=False))
    monkeypatch.setattr(
        _base,
        "message_with_failure_capture",
        AsyncMock(side_effect=lambda _client, _kind, message, **_kwargs: message),
    )


async def test_find_tile_by_media_id_prefers_exact_fe_id_selector():
    selector = f"[data-tile-id='fe_id_{TARGET_MEDIA_ID}']"
    page = _Page(matches={selector})

    tile = await _find_tile_by_media_id(page, TARGET_MEDIA_ID)

    assert tile is page.locators[selector]


async def test_find_tile_by_media_id_falls_back_to_data_tile_contains():
    selector = f"[data-tile-id*='{TARGET_MEDIA_ID}']"
    page = _Page(matches={selector})

    tile = await _find_tile_by_media_id(page, TARGET_MEDIA_ID)

    assert tile is page.locators[selector]


async def test_find_tile_by_media_id_falls_back_to_edit_href():
    selector = f"a[href*='/edit/{TARGET_MEDIA_ID}']"
    page = _Page(matches={selector})

    tile = await _find_tile_by_media_id(page, TARGET_MEDIA_ID)

    assert tile is page.locators[selector]


async def test_navigate_recovery_clicks_media_id_tile_not_first_tile(monkeypatch):
    target_selector = f"[data-tile-id='fe_id_{TARGET_MEDIA_ID}']"
    first_selector = f"[data-tile-id='fe_id_{ROOT_MEDIA_ID}']"
    page = _Page(matches={target_selector, first_selector}, url=_edit_url(TARGET_MEDIA_ID))
    first_tile = page.locator(first_selector)
    click_video_tile = AsyncMock(return_value=True)
    monkeypatch.setattr(_base, "_click_video_tile", click_video_tile)

    await navigate_to_edit(
        _client(page),
        {
            "edit_url": _edit_url(TARGET_MEDIA_ID),
            "project_url": _project_url(),
            "media_id": TARGET_MEDIA_ID,
        },
    )

    assert page.clicked_selectors == [target_selector]
    assert first_tile.click.await_count == 0
    click_video_tile.assert_not_awaited()


async def test_navigate_recovery_uses_first_tile_only_when_media_id_unknown(monkeypatch):
    page = _Page(url=_edit_url(TARGET_MEDIA_ID))
    async def _recover(page_arg, _media_id):
        page_arg.editor_mounts = True
        return True

    click_video_tile = AsyncMock(side_effect=_recover)
    monkeypatch.setattr(_base, "_click_video_tile", click_video_tile)

    await navigate_to_edit(
        _client(page),
        {"edit_url": _edit_url(TARGET_MEDIA_ID), "project_url": _project_url(), "media_id": ""},
    )

    click_video_tile.assert_awaited_once_with(page, "")


async def test_navigate_recovery_fails_clearly_when_target_tile_missing_and_deep_rail(monkeypatch):
    """Deep rail (>=2 tiles, target tile not matched): strict refusal."""
    page = _Page(url=_edit_url(TARGET_MEDIA_ID))
    # Mock the [data-tile-id] count() to return 2 (deep-chain rail)
    deep_tile_loc = MagicMock()
    deep_tile_loc.count = AsyncMock(return_value=2)
    original_locator = page.locator

    def _locator_with_deep_rail(selector):
        if selector == "[data-tile-id]":
            return deep_tile_loc
        return original_locator(selector)
    page.locator = _locator_with_deep_rail

    click_video_tile = AsyncMock(return_value=True)
    monkeypatch.setattr(_base, "_click_video_tile", click_video_tile)

    job = {
        "edit_url": _edit_url(TARGET_MEDIA_ID),
        "project_url": _project_url(),
        "media_id": TARGET_MEDIA_ID,
    }

    with pytest.raises(RuntimeError, match="target tile for media_id") as exc_info:
        await navigate_to_edit(_client(page), job)

    assert TARGET_MEDIA_ID in str(exc_info.value)
    assert "Refusing first-tile recovery" in str(exc_info.value)
    click_video_tile.assert_not_awaited()


async def test_navigate_recovery_allows_first_tile_when_shallow_rail(monkeypatch):
    """Shallow rail (<=1 tile, target not matched): fallback to first-tile click."""
    page = _Page(url=_edit_url(TARGET_MEDIA_ID))
    shallow_loc = MagicMock()
    shallow_loc.count = AsyncMock(return_value=1)
    original_locator = page.locator

    def _locator_with_shallow_rail(selector):
        if selector == "[data-tile-id]":
            return shallow_loc
        return original_locator(selector)
    page.locator = _locator_with_shallow_rail

    # Force _find_tile_by_media_id to return None (no specific tile match)
    monkeypatch.setattr(_base, "_find_tile_by_media_id", AsyncMock(return_value=None))

    async def _recover(page_arg, _media_id):
        page_arg.editor_mounts = True
        return True

    click_video_tile = AsyncMock(side_effect=_recover)
    monkeypatch.setattr(_base, "_click_video_tile", click_video_tile)

    job = {
        "edit_url": _edit_url(TARGET_MEDIA_ID),
        "project_url": _project_url(),
        "media_id": TARGET_MEDIA_ID,
    }

    # Should NOT raise — first-tile fallback is allowed at shallow rail
    await navigate_to_edit(_client(page), job)
    click_video_tile.assert_awaited_once()
