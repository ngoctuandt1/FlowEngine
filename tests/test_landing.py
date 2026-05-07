"""Unit tests for Flow landing-page recovery."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from flow.landing import (
    is_flow_canvas_page,
    is_flow_landing_url,
    recover_from_flow_landing,
)


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr("asyncio.sleep", AsyncMock())


def test_is_flow_landing_url_matches_marketing_root() -> None:
    assert is_flow_landing_url("https://labs.google/fx/vi/tools/flow")
    assert is_flow_landing_url("https://labs.google/fx/tools/flow")
    assert not is_flow_landing_url("https://labs.google/fx/tools/flow/project/abc")
    assert not is_flow_landing_url("https://labs.google/fx/tools/flow/project/abc/edit/def")


@pytest.mark.parametrize(
    ("url", "page_text"),
    [
        (
            "https://labs.google/fx/tools/flow",
            "No chain nodes yet\nRun Workflow\nBatch Run\nExport",
        ),
        (
            "https://labs.google/fx/tools/flow",
            "This chain does not have any jobs to render yet",
        ),
        (
            "https://labs.google/fx/tools/flow",
            "AI Agent\nOpen gallery\nIdeas",
        ),
        (
            "https://labs.google/fx/tools/flow/chain/abc123",
            "Loading",
        ),
    ],
)
def test_is_flow_canvas_page_detects_canvas_signals(url: str, page_text: str) -> None:
    assert is_flow_canvas_page(url, page_text)


def test_is_flow_canvas_page_ignores_project_list() -> None:
    assert not is_flow_canvas_page(
        "https://labs.google/fx/tools/flow",
        "New project\nRecent projects\nCreate with Flow",
    )
    assert not is_flow_canvas_page(
        "https://example.com/chain/abc",
        "No matching Flow page here",
    )


async def test_recover_from_flow_landing_clicks_cta_and_waits_for_project_url():
    page = MagicMock()
    state = {"url": "https://labs.google/fx/vi/tools/flow"}
    type(page).url = property(lambda _self: state["url"])

    cta = MagicMock()
    cta.is_visible = AsyncMock(return_value=True)

    async def _click(timeout):
        state["url"] = "https://labs.google/fx/tools/flow/project/abc"

    cta.click = AsyncMock(side_effect=_click)
    page.locator.return_value.first = cta

    logger = MagicMock()

    recovered = await recover_from_flow_landing(
        page,
        logger,
        "https://labs.google/fx/tools/flow/project/abc/edit/def",
        timeout_sec=1,
    )

    assert recovered is True
    cta.click.assert_awaited_once()
    logger.info.assert_any_call(
        "Flow landing detected — clicking CTA to resume target: %s",
        "https://labs.google/fx/tools/flow/project/abc/edit/def",
    )


async def test_recover_from_flow_landing_ignores_url_if_cta_is_visible():
    page = MagicMock()
    state = {
        "url": "https://labs.google/fx/tools/flow/project/abc/edit/def",
    }
    type(page).url = property(lambda _self: state["url"])

    cta = MagicMock()
    cta.is_visible = AsyncMock(return_value=True)

    async def _click(timeout):
        state["url"] = "https://labs.google/fx/tools/flow/project/abc/edit/xyz"

    cta.click = AsyncMock(side_effect=_click)
    page.locator.return_value.first = cta

    logger = MagicMock()

    recovered = await recover_from_flow_landing(
        page,
        logger,
        "https://labs.google/fx/tools/flow/project/abc/edit/def",
        timeout_sec=1,
    )

    assert recovered is True
    cta.click.assert_awaited_once()
