"""Unit tests for Flow landing-page recovery."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from flow.landing import (
    recover_from_flow_canvas_page,
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


async def test_recover_from_flow_canvas_continues_after_goto_abort(monkeypatch):
    page = MagicMock()
    state = {"url": "https://labs.google/fx/tools/flow/chain/LIVETEST_DUMMY"}
    type(page).url = property(lambda _self: state["url"])
    page.is_closed = MagicMock(return_value=False)

    page.evaluate = AsyncMock(return_value="No chain nodes yet")
    async def _goto(*_args, **_kwargs):
        state["url"] = "https://labs.google/fx/tools/flow"
        raise Exception("Page.goto: net::ERR_ABORTED")

    page.goto = AsyncMock(side_effect=_goto)

    async def _reload(*_args, **_kwargs):
        state["url"] = "https://labs.google/fx/tools/flow"

    page.reload = AsyncMock(side_effect=_reload)

    nav_target = MagicMock()
    nav_target.is_visible = AsyncMock(return_value=False)
    page.locator.return_value.first = nav_target

    visible_calls = {"count": 0}

    async def _visible(_page, timeout_ms=1000):
        visible_calls["count"] += 1
        return visible_calls["count"] >= 2

    monkeypatch.setattr("flow.landing._new_project_button_visible", _visible)

    logger = MagicMock()

    recovered = await recover_from_flow_canvas_page(
        page,
        logger,
        "https://labs.google/fx/tools/flow",
    )

    assert recovered is True
    page.goto.assert_awaited_once()
    # recover_from_flow_canvas_page does one reload; dismiss_flow_marketing_landing
    # may add a second reload if the first button-visible check returns False.
    assert page.reload.await_count >= 1


async def test_recover_from_flow_canvas_dismisses_marketing_after_homepage(monkeypatch):
    page = MagicMock()
    state = {"url": "https://labs.google/fx/tools/flow/chain/LIVETEST_DUMMY"}
    type(page).url = property(lambda _self: state["url"])
    page.is_closed = MagicMock(return_value=False)

    page.evaluate = AsyncMock(return_value="No chain nodes yet")
    async def _goto(*_args, **_kwargs):
        state["url"] = "https://labs.google/fx/tools/flow"

    page.goto = AsyncMock(side_effect=_goto)
    page.reload = AsyncMock()

    nav_target = MagicMock()
    nav_target.is_visible = AsyncMock(return_value=False)
    page.locator.return_value.first = nav_target

    visible_calls = {"count": 0}

    async def _visible(_page, timeout_ms=1000):
        visible_calls["count"] += 1
        # First call (before dismiss) returns False; second call (after dismiss) returns True.
        return visible_calls["count"] >= 2

    dismiss_mock = AsyncMock(return_value=True)
    monkeypatch.setattr("flow.landing._new_project_button_visible", _visible)
    monkeypatch.setattr("flow.landing.dismiss_flow_marketing_landing", dismiss_mock)

    logger = MagicMock()

    recovered = await recover_from_flow_canvas_page(
        page,
        logger,
        "https://labs.google/fx/tools/flow",
    )

    assert recovered is True
    dismiss_mock.assert_awaited_once()
