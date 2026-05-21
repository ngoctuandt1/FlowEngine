from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from flow.operations import generate
from worker import dispatcher


class _Locator:
    def __init__(self, *, visible=True, state="active", children=None):
        self.first = self
        self.visible = visible
        self.state = state
        self.children = children or {}
        self.click = AsyncMock()
        self.wait_for = AsyncMock()

    def locator(self, selector):
        for token, locator in self.children.items():
            if token in selector:
                return locator
        return _Locator(visible=False)

    async def is_visible(self, timeout=None):
        return self.visible

    async def get_attribute(self, name, timeout=None):
        if name == "data-state":
            return self.state
        return None


@pytest.mark.asyncio
async def test_select_voice_asset_uses_picker_voices_tab_and_verifies_state(monkeypatch):
    monkeypatch.setattr("asyncio.sleep", AsyncMock())

    trigger = _Locator()
    tab = _Locator(state="active")
    option = _Locator()
    panel = _Locator(children={"Voices": tab, "achernar": option})
    selected_queries = []

    page = MagicMock()

    def locator(selector):
        if "Add Media" in selector:
            return trigger
        if selector == generate._VOICE_ASSET_PICKER_SELECTOR:
            return panel
        return _Locator(visible=False)

    async def evaluate(script, *args):
        selected_queries.append(args)
        return True

    page.locator = MagicMock(side_effect=locator)
    page.evaluate = AsyncMock(side_effect=evaluate)

    await generate._select_voice_asset(page, "achernar")

    trigger.click.assert_awaited_once()
    tab.click.assert_awaited_once()
    option.click.assert_awaited_once()
    assert selected_queries[-1] == ("achernar",)


@pytest.mark.asyncio
async def test_dispatcher_passes_voice_asset_id_to_text_to_video(monkeypatch):
    calls = []

    @asynccontextmanager
    async def fake_client_lease(profile: str, *, target_url: str | None = None):
        yield SimpleNamespace(_job_id=None, profile_name=profile)

    async def fake_text_to_video(client, **kwargs):
        calls.append(kwargs)
        return {"output_files": ["out.mp4"], "media_id": "media-1"}

    monkeypatch.setattr(dispatcher, "_client_lease", fake_client_lease)
    monkeypatch.setattr(generate, "text_to_video", fake_text_to_video)

    await dispatcher.handle_text_to_video(
        {
            "id": "job-voice",
            "type": "text-to-video",
            "profile": "profile-a",
            "prompt": "Say hello",
            "voice_asset_id": "achernar",
        }
    )

    assert calls[0]["voice_asset_id"] == "achernar"
