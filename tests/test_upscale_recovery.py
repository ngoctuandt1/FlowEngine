"""Tests for landing-page recovery inside the 1080p upscale path."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from flow import upscale


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr("asyncio.sleep", AsyncMock())


async def test_ensure_edit_view_recovers_landing_even_on_edit_url(monkeypatch):
    page = MagicMock()
    page.url = "https://labs.google/fx/tools/flow/project/abc/edit/def"

    recover = AsyncMock(return_value=True)
    monkeypatch.setattr(upscale, "recover_from_flow_landing", recover)

    await upscale._ensure_edit_view(page, media_id="def")

    recover.assert_awaited_once_with(page, upscale.logger, page.url)
    page.locator.assert_not_called()
