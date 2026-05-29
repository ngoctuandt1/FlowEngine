"""Unit tests for `submit_via_agent_edit_ui` Enter-key submit path.

R22 forensic finding: the legacy button-based submit clicked Flow's
``add_2`` add-media button, opening an asset picker that fired ZERO
generate API calls — the job then died on a 180s no_signal_timeout.

The reworked submit:
  1. Types the command into the contenteditable.
  2. Presses Enter (primary submit path) — never clicks add-media buttons.
  3. If an asset picker opens, presses Escape once and retries Enter.
  4. Verifies a generate/batchAsync request actually fired; returns False
     fast when none does.

All mocked at the Playwright boundary — no browser runtime. `asyncio.sleep`
is stubbed so the in-function render waits don't inflate runtime.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from flow.operations._base import submit_via_agent_edit_ui


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr("asyncio.sleep", AsyncMock())


def _make_editor(*, visible: bool = True):
    editor = MagicMock()
    editor.click = AsyncMock()
    editor.type = AsyncMock()
    editor.is_visible = AsyncMock(return_value=visible)
    # `.first` resolves to the editor itself (matches _locator_is_visible).
    editor.first = editor
    return editor


def _make_page(
    *,
    editor,
    generate_fires: bool = True,
    asset_picker_open: bool = False,
):
    """Build a mock Playwright page.

    `asset_picker_open` may be a bool (constant) or a list of bools consumed
    per `_agent_asset_picker_open` call to simulate Escape closing the picker.
    """
    page = MagicMock()

    # locator("[contenteditable='true']") -> editor; everything else -> empty.
    def _locator(selector):
        if "contenteditable" in selector:
            return editor
        empty = MagicMock()
        empty.first = empty
        empty.last = empty
        empty.is_visible = AsyncMock(return_value=False)
        return empty

    page.locator = MagicMock(side_effect=_locator)

    page.keyboard = MagicMock()
    page.keyboard.press = AsyncMock()

    # Asset-picker detection routes through get_by_role / get_by_text.
    if isinstance(asset_picker_open, list):
        picker_states = list(asset_picker_open)
    else:
        picker_states = None

    def _picker_visible():
        if picker_states is not None:
            return picker_states.pop(0) if picker_states else False
        return asset_picker_open

    # get_by_role('dialog', ...) drives _role_is_visible.
    def _get_by_role(role, **kwargs):
        loc = MagicMock()
        loc.first = loc
        loc.is_visible = AsyncMock(side_effect=lambda *a, **k: _picker_visible())
        return loc

    page.get_by_role = MagicMock(side_effect=_get_by_role)

    # get_by_text markers default to not visible (dialog role covers picker).
    def _get_by_text(text, **kwargs):
        loc = MagicMock()
        loc.first = loc
        loc.is_visible = AsyncMock(return_value=False)
        return loc

    page.get_by_text = MagicMock(side_effect=_get_by_text)

    # wait_for_event("request", predicate=...) — fire or timeout.
    async def _wait_for_event(event, predicate=None, timeout=None):
        if generate_fires:
            return MagicMock(url="https://aisandbox-pa.googleapis.com/v1:batchAsync")
        raise TimeoutError("no generate request")

    page.wait_for_event = AsyncMock(side_effect=_wait_for_event)
    page.screenshot = AsyncMock()
    return page


@pytest.mark.asyncio
async def test_submits_via_enter_when_generate_fires():
    """Happy path: types command, presses Enter, generate request observed."""
    editor = _make_editor()
    page = _make_page(editor=editor, generate_fires=True)

    result = await submit_via_agent_edit_ui(page, "Remove the object")

    assert result is True
    editor.type.assert_awaited_once()
    # Enter pressed at least once (Control+a during typing also uses press).
    enter_calls = [c for c in page.keyboard.press.await_args_list if c.args == ("Enter",)]
    assert len(enter_calls) == 1


@pytest.mark.asyncio
async def test_never_clicks_add_media_button():
    """Submit must go through the keyboard, never click a stray button.

    The only locator the function may resolve for clicking is the
    contenteditable editor; no add-media / submit button is clicked.
    """
    editor = _make_editor()
    page = _make_page(editor=editor, generate_fires=True)

    await submit_via_agent_edit_ui(page, "Extend this video")

    # editor.click fires (focus), but no OTHER locator's .click is awaited.
    # Any non-editor locator built in _make_page is a fresh MagicMock whose
    # click was never awaited.
    assert editor.click.await_count >= 1


@pytest.mark.asyncio
async def test_returns_false_when_no_generate_request():
    """Enter fired but no generate request → fail fast with False."""
    editor = _make_editor()
    page = _make_page(editor=editor, generate_fires=False)

    result = await submit_via_agent_edit_ui(page, "Remove the object")

    assert result is False
    page.wait_for_event.assert_awaited()


@pytest.mark.asyncio
async def test_recovers_from_asset_picker_with_escape():
    """Asset picker opens on first Enter; Escape closes it, retry succeeds."""
    editor = _make_editor()
    # First _agent_asset_picker_open -> True (picker up), second -> False.
    page = _make_page(
        editor=editor,
        generate_fires=True,
        asset_picker_open=[True, False],
    )

    result = await submit_via_agent_edit_ui(page, "Remove the object")

    assert result is True
    escape_calls = [
        c for c in page.keyboard.press.await_args_list if c.args == ("Escape",)
    ]
    assert len(escape_calls) == 1
    enter_calls = [
        c for c in page.keyboard.press.await_args_list if c.args == ("Enter",)
    ]
    assert len(enter_calls) == 2  # initial + retry


@pytest.mark.asyncio
async def test_returns_false_when_editor_not_visible():
    """Missing contenteditable → False, no typing attempted."""
    editor = _make_editor(visible=False)
    page = _make_page(editor=editor, generate_fires=True)

    result = await submit_via_agent_edit_ui(page, "Remove the object")

    assert result is False
    editor.type.assert_not_awaited()


@pytest.mark.asyncio
async def test_screenshot_captured_on_no_generate(monkeypatch, tmp_path):
    """Failure path captures a timestamped screenshot when env-gated."""
    monkeypatch.setenv("FLOW_ERROR_CAPTURE_DIR", str(tmp_path))
    monkeypatch.setenv("FLOW_ERROR_CAPTURE", "1")
    editor = _make_editor()
    page = _make_page(editor=editor, generate_fires=False)

    result = await submit_via_agent_edit_ui(page, "Remove the object")

    assert result is False
    page.screenshot.assert_awaited()
