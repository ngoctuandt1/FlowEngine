from unittest.mock import AsyncMock, MagicMock

import pytest

from flow.ai_locator import AILocatorResult
from flow.operations import frames_to_video, ingredients
from flow.operations import generate as generate_op


def _make_locator(*, visible: bool = False):
    loc = MagicMock()
    loc.first = loc
    loc.is_visible = AsyncMock(return_value=visible)
    loc.click = AsyncMock()
    return loc


@pytest.mark.asyncio
async def test_open_composer_menu_skips_click_when_menu_already_open(monkeypatch):
    monkeypatch.setattr("asyncio.sleep", AsyncMock())

    open_chip = _make_locator(visible=True)
    fallback = _make_locator(visible=False)
    page = MagicMock()

    def _locator(selector):
        if selector == "button[aria-haspopup='menu'][data-state='open']":
            return open_chip
        if selector == "[role='menu'][data-state='open']":
            return fallback
        return _make_locator(visible=True)

    page.locator = MagicMock(side_effect=_locator)

    await frames_to_video._open_composer_menu(page)

    open_chip.click.assert_not_awaited()
    assert page.locator.call_count == 1


@pytest.mark.asyncio
async def test_open_composer_menu_clicks_chip_when_menu_closed(monkeypatch, caplog):
    monkeypatch.setattr("asyncio.sleep", AsyncMock())

    closed_state = _make_locator(visible=False)
    chip = _make_locator(visible=True)
    page = MagicMock()
    page.evaluate = AsyncMock()

    def _locator(selector):
        if selector in (
            "button[aria-haspopup='menu'][data-state='open']",
            "[role='menu'][data-state='open']",
        ):
            return closed_state
        if selector == frames_to_video.COMPOSER_MENU_SELECTORS[0]:
            return chip
        return _make_locator(visible=False)

    page.locator = MagicMock(side_effect=_locator)

    with caplog.at_level("WARNING"):
        await frames_to_video._open_composer_menu(page)

    chip.click.assert_awaited_once_with(timeout=3000)
    page.evaluate.assert_not_awaited()
    assert "no composer chip matched" not in caplog.text


@pytest.mark.asyncio
async def test_open_composer_menu_logs_diagnostic_when_all_selectors_miss(monkeypatch, caplog):
    monkeypatch.setattr("asyncio.sleep", AsyncMock())

    closed_state = _make_locator(visible=False)
    missing = _make_locator(visible=False)
    page = MagicMock()
    page.evaluate = AsyncMock(return_value=["Nano Banana", "x1"])
    seen_selectors = []

    def _locator(selector):
        seen_selectors.append(selector)
        if selector in (
            "button[aria-haspopup='menu'][data-state='open']",
            "[role='menu'][data-state='open']",
        ):
            return closed_state
        return missing

    page.locator = MagicMock(side_effect=_locator)

    with caplog.at_level("WARNING"):
        with pytest.raises(RuntimeError, match="Could not open composer chip"):
            await frames_to_video._open_composer_menu(page)

    assert "no composer chip matched" in caplog.text
    assert "Nano Banana" in caplog.text
    assert "x1" in caplog.text
    assert seen_selectors == [
        "button[aria-haspopup='menu'][data-state='open']",
        "[role='menu'][data-state='open']",
        *frames_to_video.COMPOSER_MENU_SELECTORS,
    ]


@pytest.mark.asyncio
async def test_open_composer_menu_all_selector_miss_raises_current_contract(monkeypatch):
    monkeypatch.setattr("asyncio.sleep", AsyncMock())

    closed_state = _make_locator(visible=False)
    page = MagicMock()
    page.evaluate = AsyncMock(return_value=["Nano Banana"])

    def _locator(selector):
        if selector in (
            "button[aria-haspopup='menu'][data-state='open']",
            "[role='menu'][data-state='open']",
        ):
            return closed_state
        return _make_locator(visible=False)

    page.locator = MagicMock(side_effect=_locator)

    with pytest.raises(RuntimeError, match="Could not open composer chip"):
        await frames_to_video._open_composer_menu(page)


def test_ingredients_uses_shared_composer_menu_selectors():
    assert ingredients.COMPOSER_MENU_SELECTORS is frames_to_video.COMPOSER_MENU_SELECTORS


@pytest.mark.asyncio
async def test_generate_video_tab_fast_path_skips_ai(monkeypatch):
    monkeypatch.setenv("FLOW_AI_LOCATOR_ENABLED", "true")
    monkeypatch.setattr("asyncio.sleep", AsyncMock())
    ai_spy = AsyncMock()
    monkeypatch.setattr("flow.ai_locator.ai_locate", ai_spy)

    chip = MagicMock()
    tab = MagicMock()
    monkeypatch.setattr(generate_op, "_open_composer_menu_by_role_text", AsyncMock(return_value=chip))
    monkeypatch.setattr(
        generate_op,
        "_find_open_composer_tab",
        AsyncMock(return_value=(tab, "active", ["'Video'=active"])),
    )
    monkeypatch.setattr(generate_op, "_close_composer_menu_by_click_outside", AsyncMock())

    assert await generate_op._ensure_video_composer_mode(MagicMock()) is chip
    ai_spy.assert_not_awaited()


@pytest.mark.asyncio
async def test_generate_video_tab_ai_fallback_uses_cache_key(monkeypatch):
    monkeypatch.setenv("FLOW_AI_LOCATOR_ENABLED", "true")
    monkeypatch.setattr("asyncio.sleep", AsyncMock())
    ai_spy = AsyncMock(
        return_value=AILocatorResult(
            selector="#video-tab",
            coordinates=None,
            method="ai",
            cost_estimate=0.0,
            debug_log=[],
        )
    )
    monkeypatch.setattr("flow.ai_locator.ai_locate", ai_spy)

    chip = MagicMock()
    ai_tab = MagicMock()
    ai_tab.first = ai_tab
    ai_tab.click = AsyncMock()
    legacy_tab = MagicMock()
    legacy_tab.first = legacy_tab
    legacy_tab.get_attribute = AsyncMock(side_effect=RuntimeError("missing legacy tab"))

    page = MagicMock()

    def _locator(selector):
        if selector == '[id$="-trigger-VIDEO"]':
            return legacy_tab
        if selector == "#video-tab":
            return ai_tab
        return _make_locator(visible=False)

    page.locator = MagicMock(side_effect=_locator)
    monkeypatch.setattr(generate_op, "_open_composer_menu_by_role_text", AsyncMock(return_value=chip))
    monkeypatch.setattr(
        generate_op,
        "_find_open_composer_tab",
        AsyncMock(return_value=(None, None, ["'Image'=active"])),
    )
    monkeypatch.setattr(generate_op, "_close_composer_menu_by_click_outside", AsyncMock())

    assert await generate_op._ensure_video_composer_mode(page) is chip
    ai_tab.click.assert_awaited_once_with(timeout=3000)
    ai_spy.assert_awaited_once()
    assert ai_spy.await_args.kwargs["cache_key"] == generate_op._COMPOSER_VIDEO_TAB_AI_CACHE_KEY


@pytest.mark.asyncio
async def test_generate_video_tab_ai_disabled_and_miss_preserve_error(monkeypatch):
    monkeypatch.setattr("asyncio.sleep", AsyncMock())
    ai_spy = AsyncMock()
    monkeypatch.setattr("flow.ai_locator.ai_locate", ai_spy)

    page = MagicMock()
    legacy_tab = MagicMock()
    legacy_tab.first = legacy_tab
    legacy_tab.get_attribute = AsyncMock(side_effect=RuntimeError("missing legacy tab"))
    page.locator.return_value = legacy_tab

    monkeypatch.setattr(generate_op, "_open_composer_menu_by_role_text", AsyncMock(return_value=MagicMock()))
    monkeypatch.setattr(
        generate_op,
        "_find_open_composer_tab",
        AsyncMock(return_value=(None, None, ["'Image'=active"])),
    )
    monkeypatch.setattr(generate_op, "_close_composer_menu_by_click_outside", AsyncMock())

    with pytest.raises(RuntimeError, match="Composer Video tab not found"):
        await generate_op._ensure_video_composer_mode(page)
    ai_spy.assert_not_awaited()

    monkeypatch.setenv("FLOW_AI_LOCATOR_ENABLED", "true")
    ai_spy.return_value = AILocatorResult(
        selector=None,
        coordinates=None,
        method="miss",
        cost_estimate=0.0,
        debug_log=["ai_not_found"],
    )
    with pytest.raises(RuntimeError, match="Composer Video tab not found"):
        await generate_op._ensure_video_composer_mode(page)
    assert ai_spy.await_count == 1


@pytest.mark.asyncio
async def test_open_composer_menu_reveals_collapsed_then_succeeds(monkeypatch):
    """When no chip is scored, reveal fires and a second collect yields a chip."""
    monkeypatch.setattr("asyncio.sleep", AsyncMock())
    monkeypatch.setattr(generate_op, "_composer_menu_is_open", AsyncMock(return_value=False))

    collected = [
        # First collect: only project-toolbar buttons (score 0).
        [{"index": 0, "text": "add", "score": 0}],
        # After reveal: a real composer chip (score > 0).
        [{"index": 0, "text": "Veo x1 16:9", "score": 75, "dataState": "", "ariaExpanded": ""}],
    ]
    collect_spy = AsyncMock(side_effect=collected)
    monkeypatch.setattr(generate_op, "_collect_composer_menu_button_candidates", collect_spy)
    monkeypatch.setattr(generate_op, "_collect_visible_menu_button_texts", AsyncMock(return_value=["add"]))
    reveal_spy = AsyncMock(return_value=True)
    monkeypatch.setattr(generate_op, "_try_reveal_collapsed_composer", reveal_spy)
    monkeypatch.setattr(generate_op, "_wait_for_composer_menu_open", AsyncMock(return_value=True))

    chip = _make_locator(visible=True)
    chip.nth = MagicMock(return_value=chip)
    page = MagicMock()
    page.locator = MagicMock(return_value=chip)

    result = await generate_op._open_composer_menu_by_role_text(page, purpose="Video mode")

    reveal_spy.assert_awaited_once()
    assert collect_spy.await_count == 2
    assert result is chip


@pytest.mark.asyncio
async def test_open_composer_menu_captures_forensics_on_raise(monkeypatch, tmp_path):
    """Reveal fails -> raise, and a screenshot + full DOM is captured first."""
    monkeypatch.setattr("asyncio.sleep", AsyncMock())
    monkeypatch.setenv("FLOW_ERROR_CAPTURE_DIR", str(tmp_path))
    monkeypatch.setenv("FLOW_ERROR_CAPTURE", "1")
    monkeypatch.setattr(generate_op, "_composer_menu_is_open", AsyncMock(return_value=False))
    monkeypatch.setattr(
        generate_op,
        "_collect_composer_menu_button_candidates",
        AsyncMock(return_value=[{"index": 0, "text": "add", "score": 0}]),
    )
    monkeypatch.setattr(generate_op, "_collect_visible_menu_button_texts", AsyncMock(return_value=["add"]))
    monkeypatch.setattr(generate_op, "_try_reveal_collapsed_composer", AsyncMock(return_value=False))

    page = MagicMock()
    page.locator = MagicMock(return_value=_make_locator(visible=False))
    page.screenshot = AsyncMock()
    page.content = AsyncMock(return_value="<html><body>full dom</body></html>")

    with pytest.raises(RuntimeError, match="Could not open composer menu"):
        await generate_op._open_composer_menu_by_role_text(page, purpose="Video mode")

    page.screenshot.assert_awaited_once()
    page.content.assert_awaited_once()
    html_files = list(tmp_path.glob("*_composer_menu_fail.full.html"))
    assert len(html_files) == 1
    assert html_files[0].read_text(encoding="utf-8") == "<html><body>full dom</body></html>"


@pytest.mark.asyncio
async def test_reveal_collapsed_composer_cancels_file_chooser(monkeypatch):
    """The bare 'add' fallback must cancel a file chooser, not leave it open."""
    monkeypatch.setattr("asyncio.sleep", AsyncMock())
    # All text/aria selectors miss; only the bare-icon selector is visible.
    bare_sel = "button:has(i:text-is('add'))"

    def _locator(selector):
        return _make_locator(visible=(selector == bare_sel))

    page = MagicMock()
    page.locator = MagicMock(side_effect=_locator)

    chooser = MagicMock()
    chooser.set_files = AsyncMock()

    chooser_info = MagicMock()
    # Playwright's chooser_info.value is an awaitable that yields the chooser.
    chooser_info.value = _chooser_awaitable(chooser)

    chooser_ctx = MagicMock()
    chooser_ctx.__aenter__ = AsyncMock(return_value=chooser_info)
    chooser_ctx.__aexit__ = AsyncMock(return_value=False)
    page.expect_file_chooser = MagicMock(return_value=chooser_ctx)
    # No composer chip ever appears -> reveal returns False.
    monkeypatch.setattr(
        generate_op,
        "_collect_composer_menu_button_candidates",
        AsyncMock(return_value=[{"index": 0, "text": "add", "score": 0}]),
    )

    revealed = await generate_op._try_reveal_collapsed_composer(page)

    assert revealed is False
    chooser.set_files.assert_awaited_once_with([])


def _chooser_awaitable(value):
    async def _coro():
        return value

    return _coro()
