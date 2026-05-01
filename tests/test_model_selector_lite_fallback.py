"""Unit tests for LP -> Lite fallback in the free model selector path."""

import logging
import re
from unittest.mock import AsyncMock, MagicMock

import pytest

from flow import model_selector as model_selector_mod
from flow.model_selector import select_model

_MODEL_VARIANT_TOKENS = {"fast", "lite", "quality", "lower", "priority"}


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """Stub selector sleeps so retry logic stays fast in unit tests."""
    monkeypatch.setattr("asyncio.sleep", AsyncMock())


def _normalize_model_text(text: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", " ", text.lower())
    return re.sub(r"\s+", " ", normalized).strip()


def _normalize_model_family(text: str) -> str:
    tokens = [
        token
        for token in _normalize_model_text(text).split()
        if token not in _MODEL_VARIANT_TOKENS
    ]
    return " ".join(tokens)


def _record_click(
    text: str,
    clicked_texts: list[str],
    click_errors: dict[str, Exception],
) -> None:
    error = click_errors.get(text)
    if error is not None:
        raise error
    clicked_texts.append(text)


def _make_filtered_locator(
    texts: list[str],
    clicked_texts: list[str],
    click_errors: dict[str, Exception],
):
    locator = MagicMock()
    locator.count = AsyncMock(return_value=len(texts))

    first = MagicMock()
    first.is_visible = AsyncMock(return_value=bool(texts))
    if texts:
        first.inner_text = AsyncMock(return_value=texts[0])
        first.click = AsyncMock(
            side_effect=lambda *args, _text=texts[0], **kwargs: _record_click(
                _text, clicked_texts, click_errors
            )
        )
    else:
        first.inner_text = AsyncMock(return_value="")
        first.click = AsyncMock(side_effect=AssertionError("No dropdown item should be clicked"))
    locator.first = first

    def _nth(index: int):
        item = MagicMock()
        text = texts[index]
        item.inner_text = AsyncMock(return_value=text)
        item.click = AsyncMock(
            side_effect=lambda *args, _text=text, **kwargs: _record_click(
                _text, clicked_texts, click_errors
            )
        )
        item.is_visible = AsyncMock(return_value=True)
        return item

    locator.nth = MagicMock(side_effect=_nth)
    return locator


def _simulate_js_selection(
    option_texts: list[str],
    clicked_texts: list[str],
    click_errors: dict[str, Exception],
    args: dict,
):
    phases = args.get("candidateTexts") or [""]
    normalized_base = args.get("normalizedBase", "")
    normalized_family = args.get("normalizedFamily", "")

    for candidate_text in phases:
        normalized_candidate = _normalize_model_text(candidate_text)
        matches: list[tuple[str, str]] = []
        for text in option_texts:
            normalized_text = _normalize_model_text(text)
            if normalized_candidate:
                if normalized_candidate not in normalized_text:
                    continue
            elif normalized_base not in normalized_text:
                continue
            matches.append((text, normalized_text))

        exact_matches = [match for match in matches if normalized_base in match[1]]
        if exact_matches:
            clicked_text = exact_matches[0][0]
            _record_click(clicked_text, clicked_texts, click_errors)
            return {
                "status": "clicked",
                "clickedText": clicked_text,
                "candidateText": candidate_text,
            }

        family_matches = [
            match for match in matches if normalized_family and normalized_family in match[1]
        ]
        if len(family_matches) == 1:
            clicked_text = family_matches[0][0]
            _record_click(clicked_text, clicked_texts, click_errors)
            return {
                "status": "clicked",
                "clickedText": clicked_text,
                "candidateText": candidate_text,
            }

        if len(family_matches) > 1 or len(matches) > 1:
            return {
                "status": "ambiguous",
                "candidateText": candidate_text,
                "visibleTexts": [match[0] for match in matches],
            }

        if len(matches) == 1:
            clicked_text = matches[0][0]
            _record_click(clicked_text, clicked_texts, click_errors)
            return {
                "status": "clicked",
                "clickedText": clicked_text,
                "candidateText": candidate_text,
            }

    return {"status": "none"}


def _make_select_model_page(
    option_texts: list[str],
    *,
    js_option_texts: list[str] | None = None,
    force_playwright_miss: bool = False,
    click_errors: dict[str, Exception] | None = None,
):
    page = MagicMock()
    clicked_texts: list[str] = []
    click_errors = click_errors or {}
    js_option_texts = option_texts if js_option_texts is None else js_option_texts

    chip = MagicMock()
    chip.first = chip
    chip.is_visible = AsyncMock(return_value=True)
    chip.click = AsyncMock(return_value=None)
    chip.inner_text = AsyncMock(return_value="Veo 3.1 - Fast x1")

    model_items_loc = MagicMock()
    model_items_loc.count = AsyncMock(return_value=len(option_texts))
    model_items_loc.nth = MagicMock(
        side_effect=lambda index: _make_filtered_locator(
            [option_texts[index]],
            clicked_texts,
            click_errors,
        ).first
    )

    def _filter(*, has_text):
        if force_playwright_miss:
            matches: list[str] = []
        else:
            matches = [text for text in option_texts if has_text.search(text)]
        return _make_filtered_locator(matches, clicked_texts, click_errors)

    model_items_loc.filter = MagicMock(side_effect=_filter)

    def _locator(selector: str):
        if "menuitem" in selector and "role='menuitem'" in selector:
            return model_items_loc
        return chip

    async def _evaluate(script, args=None):
        if not isinstance(args, dict):
            return False
        page._js_calls.append(args)  # type: ignore[attr-defined]
        return _simulate_js_selection(js_option_texts, clicked_texts, click_errors, args)

    page.locator = MagicMock(side_effect=_locator)
    page.evaluate = AsyncMock(side_effect=_evaluate)
    page.keyboard = MagicMock()
    page.keyboard.press = AsyncMock(return_value=None)
    page._clicked_texts = clicked_texts  # type: ignore[attr-defined]
    page._js_calls = []  # type: ignore[attr-defined]
    return page


@pytest.fixture
def _selector_stubs(monkeypatch):
    open_dropdown = AsyncMock(return_value=True)
    verify_credits = AsyncMock(return_value=True)
    close_panel = AsyncMock(return_value=None)

    monkeypatch.setattr(model_selector_mod, "_ensure_video_mode", AsyncMock(return_value=None))
    monkeypatch.setattr(model_selector_mod, "_switch_to_video_tab", AsyncMock(return_value=True))
    monkeypatch.setattr(model_selector_mod, "_open_model_dropdown", open_dropdown)
    monkeypatch.setattr(model_selector_mod, "_verify_credits", verify_credits)
    monkeypatch.setattr(model_selector_mod, "_close_model_panel", close_panel)
    monkeypatch.setattr(model_selector_mod, "_debug_model_options", AsyncMock(return_value=None))

    return {
        "open_dropdown": open_dropdown,
        "verify_credits": verify_credits,
        "close_panel": close_panel,
    }


async def test_select_model_prefers_lower_priority_when_only_lp_exists(
    _selector_stubs, caplog
):
    page = _make_select_model_page(["Veo 3.1 - Fast [Lower Priority]"])

    with caplog.at_level(logging.WARNING, logger="flow.model_selector"):
        result = await select_model(page, model="veo-3.1-fast-lp")

    assert result is True
    assert page._clicked_texts == ["Veo 3.1 - Fast [Lower Priority]"]
    assert "falling back to Lite" not in caplog.text
    _selector_stubs["open_dropdown"].assert_not_called()
    _selector_stubs["verify_credits"].assert_awaited_once_with(page, expected=0)


async def test_select_model_falls_back_to_lite_when_lp_is_missing(
    _selector_stubs, caplog
):
    page = _make_select_model_page(["Veo 3.1 - Lite"])

    with caplog.at_level(logging.WARNING, logger="flow.model_selector"):
        result = await select_model(page, model="veo-3.1-fast-lp")

    assert result is True
    assert page._clicked_texts == ["Veo 3.1 - Lite"]
    assert "LP option not found, falling back to Lite" in caplog.text
    _selector_stubs["open_dropdown"].assert_awaited_once()
    _selector_stubs["verify_credits"].assert_awaited_once_with(page, expected=0)


async def test_select_model_prefers_lp_when_lp_and_lite_both_exist(
    _selector_stubs, caplog
):
    page = _make_select_model_page(
        ["Veo 3.1 - Fast [Lower Priority]", "Veo 3.1 - Lite"]
    )

    with caplog.at_level(logging.WARNING, logger="flow.model_selector"):
        result = await select_model(page, model="veo-3.1-fast-lp")

    assert result is True
    assert page._clicked_texts == ["Veo 3.1 - Fast [Lower Priority]"]
    assert "falling back to Lite" not in caplog.text
    _selector_stubs["open_dropdown"].assert_not_called()
    _selector_stubs["verify_credits"].assert_awaited_once_with(page, expected=0)


async def test_select_model_raises_when_neither_lp_nor_lite_exists(
    _selector_stubs,
):
    page = _make_select_model_page(["Veo Quality", "Veo Imagen"])

    with pytest.raises(
        RuntimeError,
        match="free_model_select_failed: Neither Lower Priority nor Lite model found in dropdown",
    ) as excinfo:
        await select_model(page, model="veo-3.1-fast-lp", profile="profile-a")

    assert page._clicked_texts == []
    message = str(excinfo.value)
    assert "Profile=profile-a" in message
    assert 'Visible=["Veo Quality", "Veo Imagen"]' in message
    _selector_stubs["open_dropdown"].assert_awaited_once()
    _selector_stubs["verify_credits"].assert_not_called()


async def test_select_model_picks_matching_lite_variant_when_multiple_versions_exist(
    _selector_stubs,
):
    page = _make_select_model_page(["Veo 3.0 Lite", "Veo 3.1 Lite"])

    result = await select_model(page, model="veo-3.1-fast-lp")

    assert result is True
    assert page._clicked_texts == ["Veo 3.1 Lite"]
    _selector_stubs["open_dropdown"].assert_awaited_once()
    _selector_stubs["verify_credits"].assert_awaited_once_with(page, expected=0)


async def test_select_model_uses_js_fallback_and_prefers_lp_before_lite(
    _selector_stubs, caplog
):
    page = _make_select_model_page(
        ["Veo Quality"],
        js_option_texts=["Veo 3.1 - Fast [Lower Priority]", "Veo 3.1 Lite"],
        force_playwright_miss=True,
    )

    with caplog.at_level(logging.WARNING, logger="flow.model_selector"):
        result = await select_model(page, model="veo-3.1-fast-lp")

    assert result is True
    assert page._clicked_texts == ["Veo 3.1 - Fast [Lower Priority]"]
    assert page._js_calls[-1]["candidateTexts"] == ["Lower Priority", "Lite"]
    assert "falling back to Lite" not in caplog.text
    _selector_stubs["open_dropdown"].assert_awaited_once()
    _selector_stubs["verify_credits"].assert_awaited_once_with(page, expected=0)


async def test_select_model_uses_js_fallback_and_falls_back_to_lite(
    _selector_stubs, caplog
):
    page = _make_select_model_page(
        ["Veo Quality"],
        js_option_texts=["Veo 3.1 Lite"],
        force_playwright_miss=True,
    )

    with caplog.at_level(logging.WARNING, logger="flow.model_selector"):
        result = await select_model(page, model="veo-3.1-fast-lp")

    assert result is True
    assert page._clicked_texts == ["Veo 3.1 Lite"]
    assert page._js_calls[-1]["candidateTexts"] == ["Lower Priority", "Lite"]
    assert "LP option not found, falling back to Lite" in caplog.text
    _selector_stubs["open_dropdown"].assert_awaited_once()
    _selector_stubs["verify_credits"].assert_awaited_once_with(page, expected=0)


async def test_select_model_raises_when_js_fallback_finds_neither_lp_nor_lite(
    _selector_stubs,
):
    page = _make_select_model_page(
        ["Veo Quality", "Veo Imagen"],
        js_option_texts=["Veo Quality", "Veo Imagen"],
        force_playwright_miss=True,
    )

    with pytest.raises(
        RuntimeError,
        match="free_model_select_failed: Neither Lower Priority nor Lite model found in dropdown",
    ) as excinfo:
        await select_model(page, model="veo-3.1-fast-lp", profile="profile-b")

    assert page._clicked_texts == []
    message = str(excinfo.value)
    assert "Profile=profile-b" in message
    assert 'Visible=["Veo Quality", "Veo Imagen"]' in message
    _selector_stubs["verify_credits"].assert_not_called()


async def test_select_model_raises_when_free_item_click_keeps_failing(
    _selector_stubs,
):
    page = _make_select_model_page(
        ["Veo 3.1 - Fast [Lower Priority]"],
        click_errors={"Veo 3.1 - Fast [Lower Priority]": RuntimeError("detached")},
        js_option_texts=[],
    )

    with pytest.raises(
        RuntimeError,
        match="free_model_select_failed: Failed to select free model after all attempts",
    ) as excinfo:
        await select_model(page, model="veo-3.1-fast-lp")

    message = str(excinfo.value)
    assert "Profile=unknown" in message
    assert 'Visible=["Veo 3.1 - Fast [Lower Priority]"]' in message
    _selector_stubs["verify_credits"].assert_not_called()


async def test_select_model_raises_when_credit_verification_never_reaches_zero(
    _selector_stubs,
):
    page = _make_select_model_page(["Veo 3.1 - Fast [Lower Priority]"])
    _selector_stubs["verify_credits"].return_value = False

    with pytest.raises(
        RuntimeError,
        match="free_model_select_failed: Free model selection did not verify 0 credits after JS fallback",
    ) as excinfo:
        await select_model(page, model="veo-3.1-fast-lp", profile="profile-c")

    message = str(excinfo.value)
    assert "Profile=profile-c" in message
    assert 'Visible=["Veo 3.1 - Fast [Lower Priority]"]' in message
    assert _selector_stubs["verify_credits"].await_count == 4
