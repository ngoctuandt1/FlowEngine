"""Camera preset selector aliases and DOM-fallback tests."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from flow.operations import camera
from flow.operations.camera import (
    BUTTON_SCAN_SELECTOR,
    _camera_preset_failure_diagnostics,
    _canonical_preset_for_direction,
    _click_preset,
    _preset_label_candidates,
)


class _TextLocator:
    def __init__(self, label: str, *, visible: bool, clicked: list[str]) -> None:
        self.label = label
        self.first = self
        self._visible = visible
        self.click = AsyncMock(side_effect=lambda **_: clicked.append(label))

    async def is_visible(self, timeout: int = 0) -> bool:
        return self._visible


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    async def _sleep(_: float) -> None:
        return None

    monkeypatch.setattr(camera.asyncio, "sleep", _sleep)


@pytest.mark.parametrize("requested", ["Pan right", "PAN_RIGHT", "pan right"])
async def test_text_alias_variants_click_observed_orbit_right(requested):
    clicked: list[str] = []
    page = MagicMock()

    def get_by_text(label: str, *, exact: bool = False):
        assert exact is True
        return _TextLocator(label, visible=(label == "Orbit right"), clicked=clicked)

    page.get_by_text = MagicMock(side_effect=get_by_text)
    page.evaluate = AsyncMock(return_value=True)

    assert await _click_preset(page, requested) is True
    assert clicked == ["Orbit right"]
    requested_labels = [call.args[0] for call in page.get_by_text.call_args_list]
    assert requested_labels[:2] == [requested, "Orbit right"]


async def test_vietnamese_locale_alias_maps_to_canonical_orbit_right():
    assert _canonical_preset_for_direction("Xoay phải") == "Orbit right"
    assert _canonical_preset_for_direction("Quỹ đạo phải") == "Orbit right"
    assert "Orbit right" in _preset_label_candidates("Xoay phải")


async def test_data_direction_fallback_clicks_matching_button():
    page = MagicMock()
    page.get_by_text = MagicMock(
        return_value=_TextLocator("missing", visible=False, clicked=[])
    )
    page.evaluate = AsyncMock(side_effect=[
        {"index": 2, "source": "data", "matchedValue": "PAN_RIGHT"},
        False,
        False,
        False,
    ])
    button = MagicMock()
    button.click = AsyncMock()
    locator = MagicMock()
    locator.nth.return_value = button
    page.locator = MagicMock(return_value=locator)

    assert await _click_preset(page, "Pan right") is True
    page.locator.assert_called_once_with(BUTTON_SCAN_SELECTOR)
    locator.nth.assert_called_once_with(2)
    button.click.assert_awaited_once_with(timeout=3000)
    payload = page.evaluate.await_args_list[0].args[1]
    assert "pan right" in payload["targetKeys"]
    assert "orbit right" in payload["targetKeys"]


async def test_icon_only_fallback_clicks_matching_button():
    page = MagicMock()
    page.get_by_text = MagicMock(
        return_value=_TextLocator("missing", visible=False, clicked=[])
    )
    page.evaluate = AsyncMock(side_effect=[
        {"index": 4, "source": "icon", "matchedValue": "keyboard_double_arrow_right"},
        False,
        False,
        False,
    ])
    button = MagicMock()
    button.click = AsyncMock()
    locator = MagicMock()
    locator.nth.return_value = button
    page.locator = MagicMock(return_value=locator)

    assert await _click_preset(page, "PAN_RIGHT") is True
    locator.nth.assert_called_once_with(4)
    button.click.assert_awaited_once_with(timeout=3000)
    payload = page.evaluate.await_args_list[0].args[1]
    assert "keyboard double arrow right" in payload["iconKeys"]


async def test_not_found_diagnostic_dumps_visible_buttons():
    page = MagicMock()
    page.evaluate = AsyncMock(return_value=[
        {"index": 0, "text": "Dolly in", "ariaLabel": "", "icons": []},
        {
            "index": 1,
            "text": "",
            "ariaLabel": "Pan right",
            "dataDirection": "PAN_RIGHT",
            "dataPreset": "",
            "icons": ["arrow_forward"],
        },
    ])

    diagnostic = await _camera_preset_failure_diagnostics(page)

    assert "Visible camera preset buttons" in diagnostic
    assert "Dolly in" in diagnostic
    assert "Pan right" in diagnostic
    assert "PAN_RIGHT" in diagnostic
    assert "arrow_forward" in diagnostic
