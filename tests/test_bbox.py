"""B2 — unit tests for `draw_bbox_on_video` in `flow/operations/_base.py`.

The helper does 5 things on a Playwright `page`:

1. `page.evaluate(...)` → read `<video>` getBoundingClientRect.
2. Validate bbox keys (`x`, `y`, `w`, `h`) ∈ [0, 1].
3. Clamp overflow (`x + w > 1` → `w = 1 - x`, same for y/h).
4. `page.mouse.move / down / up` drag sequence.
5. `page.evaluate(...)` → check overlay rect visible after drag.

Tests mock `page` with `AsyncMock` / `MagicMock` — no Playwright runtime.
The `page.evaluate` mock uses a side-effect that returns video_rect on first
call and overlay_visible on second call (matches the two JS snippets in order).

Manual E2E (POST insert job with real bbox, verify video output) is a
supervisor-side task after merge.
"""

import logging
from unittest.mock import AsyncMock, MagicMock

from flow.operations._base import draw_bbox_on_video


def _make_page(video_rect, overlay_visible):
    """Build a mock page with scripted `page.evaluate` returns + drag mocks.

    `page.evaluate` is called twice by `draw_bbox_on_video`:
    1st call returns `video_rect` (step 1 — read rect)
    2nd call returns `overlay_visible` (step 5 — verify overlay)
    """
    page = MagicMock()
    page.evaluate = AsyncMock(side_effect=[video_rect, overlay_visible])
    page.mouse = MagicMock()
    page.mouse.move = AsyncMock()
    page.mouse.down = AsyncMock()
    page.mouse.up = AsyncMock()
    return page


async def test_bbox_rejects_out_of_range(caplog):
    """B2: bbox values outside [0, 1] → return False, log ERROR."""
    page = _make_page(
        video_rect={"left": 0, "top": 0, "width": 1280, "height": 720},
        overlay_visible=True,  # never reached
    )

    with caplog.at_level(logging.ERROR, logger="flow.operations._base"):
        result = await draw_bbox_on_video(page, {"x": 1.5, "y": 0.1, "w": 0.2, "h": 0.2})

    assert result is False, "out-of-range bbox must fail fast"

    # Drag must NOT have been attempted
    page.mouse.move.assert_not_called()
    page.mouse.down.assert_not_called()
    page.mouse.up.assert_not_called()

    errors = [r.getMessage() for r in caplog.records if r.levelname == "ERROR"]
    assert any(
        "out of range" in m.lower() and "x" in m.lower() for m in errors
    ), f"Expected ERROR mentioning out-of-range x, got: {errors}"


async def test_bbox_clamps_overflow(caplog):
    """B2: x=0.7, w=0.5 (sum 1.2 > 1) → w clamped to 0.3, drag stays in rect."""
    video_rect = {"left": 100.0, "top": 50.0, "width": 1000.0, "height": 500.0}
    page = _make_page(video_rect=video_rect, overlay_visible=True)

    with caplog.at_level(logging.INFO, logger="flow.operations._base"):
        result = await draw_bbox_on_video(page, {"x": 0.7, "y": 0.1, "w": 0.5, "h": 0.2})

    assert result is True

    # First move() = drag start at (left + 0.7*width, top + 0.1*height) = (100+700, 50+50) = (800, 100)
    # Final move endpoint must be clamped: x+w capped at 1 → end_x = left + 1.0*width = 1100
    # (NOT 100 + 1.2*1000 = 1300 which would be outside the video rect)
    all_move_calls = page.mouse.move.call_args_list
    assert len(all_move_calls) >= 2, "expected initial move + drag steps"

    # First call: start point
    start_args = all_move_calls[0].args
    assert abs(start_args[0] - 800.0) < 0.01, f"start_x should be 800, got {start_args[0]}"
    assert abs(start_args[1] - 100.0) < 0.01, f"start_y should be 100, got {start_args[1]}"

    # Last call: end point (after clamp: x+w=1.0, y+h=0.3)
    end_args = all_move_calls[-1].args
    assert abs(end_args[0] - 1100.0) < 0.01, f"end_x should clamp to 1100, got {end_args[0]}"
    assert abs(end_args[1] - 200.0) < 0.01, f"end_y should be 200, got {end_args[1]}"

    # Info log should mention clamped width (0.30) since w was shrunk from 0.5
    infos = [r.getMessage() for r in caplog.records if r.levelname == "INFO"]
    assert any("w=0.30" in m for m in infos), f"Expected clamped w=0.30 in INFO, got: {infos}"


async def test_bbox_no_video_element(caplog):
    """B2: video element missing → return False early (no drag, no verify call)."""
    page = _make_page(video_rect=None, overlay_visible=True)

    with caplog.at_level(logging.ERROR, logger="flow.operations._base"):
        result = await draw_bbox_on_video(page, {"x": 0.2, "y": 0.2, "w": 0.4, "h": 0.4})

    assert result is False

    # Only the first evaluate (rect read) should have been made
    assert page.evaluate.await_count == 1
    page.mouse.move.assert_not_called()
    page.mouse.down.assert_not_called()

    errors = [r.getMessage() for r in caplog.records if r.levelname == "ERROR"]
    assert any(
        "video element not found" in m.lower() or "too small" in m.lower()
        for m in errors
    ), f"Expected ERROR about missing/small video, got: {errors}"


async def test_bbox_success_with_overlay(caplog):
    """B2: valid bbox + overlay visible → return True + INFO log."""
    video_rect = {"left": 0.0, "top": 0.0, "width": 1280.0, "height": 720.0}
    page = _make_page(video_rect=video_rect, overlay_visible=True)

    with caplog.at_level(logging.INFO, logger="flow.operations._base"):
        result = await draw_bbox_on_video(page, {"x": 0.25, "y": 0.25, "w": 0.5, "h": 0.5})

    assert result is True

    # Both evaluate calls were made (rect + overlay-check)
    assert page.evaluate.await_count == 2
    page.mouse.down.assert_awaited_once()
    page.mouse.up.assert_awaited_once()

    # Success log mentions verified + normalized coords
    infos = [r.getMessage() for r in caplog.records if r.levelname == "INFO"]
    assert any(
        "verified" in m.lower() and "x=0.25" in m for m in infos
    ), f"Expected INFO with 'verified' + coords, got: {infos}"


async def test_bbox_no_overlay_detected(caplog):
    """B2: drag completes but overlay NOT visible → return False + WARNING."""
    video_rect = {"left": 0.0, "top": 0.0, "width": 1280.0, "height": 720.0}
    page = _make_page(video_rect=video_rect, overlay_visible=False)

    with caplog.at_level(logging.WARNING, logger="flow.operations._base"):
        result = await draw_bbox_on_video(page, {"x": 0.3, "y": 0.3, "w": 0.4, "h": 0.4})

    assert result is False, "no overlay = unverified drag = False"

    # Drag DID happen — we only reject AFTER verify step
    page.mouse.down.assert_awaited_once()
    page.mouse.up.assert_awaited_once()

    warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert any(
        "no overlay detected" in m.lower() for m in warnings
    ), f"Expected WARNING about missing overlay, got: {warnings}"
