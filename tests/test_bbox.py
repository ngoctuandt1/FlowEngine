"""B11 — unit tests for `draw_bbox_on_video` in `flow/operations/_base.py`.

Supersedes the B2 test suite (commit `a165105`) after Tier1 live-DOM probe
found the prior helper targeted a 105×60 card-strip thumbnail instead of
the 598×336 preview canvas, and verified overlay via a union selector that
cannot match canvas-painted shapes. Evidence:
`docs/session-reports/2026-04-17_Tier1_dom-validation.md` §7 B2.

After the B11 rewrite, the helper does five things on a Playwright `page`:

1. `page.evaluate(...)` → find the LARGEST visible `<canvas>` with
   `width ≥ 300` (the preview is ≥ 479 CSS px; card-strip canvases are
   thumbnails and are excluded).
2. Validate bbox keys (`x`, `y`, `w`, `h`) ∈ [0, 1].
3. Clamp overflow (`x + w > 1` → `w = 1 - x`, same for y/h).
4. `page.mouse.move / down / up` drag sequence in canvas-relative coords.
5. Return `True` after drag — NO post-drag DOM verify. Flow paints the
   bbox onto the canvas 2D bitmap (not DOM); pixel sampling is unreliable
   because the preview plays video frames continuously. Pointer-trust is
   the chosen verification model (see session report §7).

Tests mock `page` with `AsyncMock` / `MagicMock` — no Playwright runtime.
`page.evaluate` is mocked to return `canvas_rect` on its single call.
"""

import logging
from unittest.mock import AsyncMock, MagicMock

from flow.operations._base import draw_bbox_on_video


def _make_page(canvas_rect):
    """Mock `page` with `page.evaluate` returning `canvas_rect` on each call.

    Post-B11 the helper calls `page.evaluate` exactly ONCE (canvas-find).
    Extra calls should not happen — covered by `test_bbox_no_verify_step`.
    """
    page = MagicMock()
    page.evaluate = AsyncMock(return_value=canvas_rect)
    page.mouse = MagicMock()
    page.mouse.move = AsyncMock()
    page.mouse.down = AsyncMock()
    page.mouse.up = AsyncMock()
    return page


async def test_bbox_rejects_out_of_range(caplog):
    """B11: bbox values outside [0, 1] → return False, log ERROR, no drag."""
    page = _make_page(
        canvas_rect={"left": 0.0, "top": 0.0, "width": 598.0, "height": 336.0, "area": 200928.0},
    )

    with caplog.at_level(logging.ERROR, logger="flow.operations._base"):
        result = await draw_bbox_on_video(page, {"x": 1.5, "y": 0.1, "w": 0.2, "h": 0.2})

    assert result is False, "out-of-range bbox must fail fast"
    page.mouse.move.assert_not_called()
    page.mouse.down.assert_not_called()
    page.mouse.up.assert_not_called()

    errors = [r.getMessage() for r in caplog.records if r.levelname == "ERROR"]
    assert any(
        "out of range" in m.lower() and "x" in m.lower() for m in errors
    ), f"Expected ERROR mentioning out-of-range x, got: {errors}"


async def test_bbox_rejects_no_canvas(caplog):
    """B11: no visible canvas ≥ 300×200 returned → False + ERROR, no drag.

    Replaces the B2 `test_bbox_no_video_element` case. The new target is a
    canvas, not a video tag — the error message must reflect that.
    """
    page = _make_page(canvas_rect=None)

    with caplog.at_level(logging.ERROR, logger="flow.operations._base"):
        result = await draw_bbox_on_video(page, {"x": 0.2, "y": 0.2, "w": 0.4, "h": 0.4})

    assert result is False
    assert page.evaluate.await_count == 1, "only the canvas-find evaluate should run"
    page.mouse.move.assert_not_called()
    page.mouse.down.assert_not_called()

    errors = [r.getMessage() for r in caplog.records if r.levelname == "ERROR"]
    assert any(
        "canvas" in m.lower() for m in errors
    ), f"Expected ERROR about missing canvas, got: {errors}"


async def test_bbox_targets_largest_canvas_rect():
    """B11: drag coords derive from the canvas rect returned by the JS.

    The JS filters to the largest canvas ≥ 300×200 (see
    `test_bbox_evaluate_script_targets_canvas`). This test feeds in a
    600×400 canvas rect and asserts the drag start/end map to it, proving
    the helper uses the canvas rect (not something else) as its coordinate
    basis. Prevents accidental regression to computing drag coords from
    viewport or `<video>` rects.
    """
    canvas_rect = {"left": 100.0, "top": 50.0, "width": 600.0, "height": 400.0, "area": 240000.0}
    page = _make_page(canvas_rect=canvas_rect)

    result = await draw_bbox_on_video(page, {"x": 0.25, "y": 0.25, "w": 0.5, "h": 0.5})

    assert result is True
    moves = page.mouse.move.call_args_list
    assert moves, "expected at least one mouse.move call"
    # Drag start = (left + 0.25*width, top + 0.25*height) = (100+150, 50+100) = (250, 150)
    start = moves[0].args
    assert abs(start[0] - 250.0) < 0.01, f"start_x should be 250, got {start[0]}"
    assert abs(start[1] - 150.0) < 0.01, f"start_y should be 150, got {start[1]}"
    # Drag end   = (left + 0.75*width, top + 0.75*height) = (100+450, 50+300) = (550, 350)
    end = moves[-1].args
    assert abs(end[0] - 550.0) < 0.01, f"end_x should be 550, got {end[0]}"
    assert abs(end[1] - 350.0) < 0.01, f"end_y should be 350, got {end[1]}"


async def test_bbox_evaluate_script_targets_canvas():
    """B11 contract trip-wire — JS must use canvas + width threshold.

    The JS body is inspected directly to prevent silent regression:
      - Must target `<canvas>`, not `<video>` (B2 bug: `querySelector('video')`
        hits the 105×60 card-strip thumbnail).
      - Must filter by `width ≥ 300` so the small card-strip canvases
        (e.g. 105 px) are excluded.

    Guards against someone swapping the evaluate script for a broader one
    that re-introduces the thumbnail-target bug.
    """
    canvas_rect = {"left": 0.0, "top": 0.0, "width": 598.0, "height": 336.0, "area": 200928.0}
    page = _make_page(canvas_rect=canvas_rect)

    await draw_bbox_on_video(page, {"x": 0.25, "y": 0.25, "w": 0.5, "h": 0.5})

    js_calls = page.evaluate.call_args_list
    assert js_calls, "expected at least one page.evaluate call"
    js_src = js_calls[0].args[0]

    assert "canvas" in js_src.lower(), "JS must target canvas (not video)"
    assert "querySelector('video')" not in js_src, \
        "Do not query single <video> — hits 105×60 card-strip thumbnail (B2 regression)"
    assert 'querySelector("video")' not in js_src, \
        "Do not query single <video> — hits 105×60 card-strip thumbnail (B2 regression)"
    assert "300" in js_src, \
        "JS must filter canvases by width ≥ 300 to exclude thumbnail canvases"


async def test_bbox_clamps_overflow(caplog):
    """B11: x=0.7, w=0.5 (sum 1.2 > 1) → w clamped to 0.3, drag stays in canvas."""
    canvas_rect = {"left": 100.0, "top": 50.0, "width": 1000.0, "height": 500.0, "area": 500000.0}
    page = _make_page(canvas_rect=canvas_rect)

    with caplog.at_level(logging.INFO, logger="flow.operations._base"):
        result = await draw_bbox_on_video(page, {"x": 0.7, "y": 0.1, "w": 0.5, "h": 0.2})

    assert result is True

    moves = page.mouse.move.call_args_list
    assert len(moves) >= 2, "expected initial move + drag steps"

    # Start: (100 + 0.7*1000, 50 + 0.1*500) = (800, 100)
    start = moves[0].args
    assert abs(start[0] - 800.0) < 0.01, f"start_x should be 800, got {start[0]}"
    assert abs(start[1] - 100.0) < 0.01, f"start_y should be 100, got {start[1]}"

    # End clamped: x+w=1.0, y+h=0.3 → (100 + 1.0*1000, 50 + 0.3*500) = (1100, 200)
    end = moves[-1].args
    assert abs(end[0] - 1100.0) < 0.01, f"end_x should clamp to 1100, got {end[0]}"
    assert abs(end[1] - 200.0) < 0.01, f"end_y should be 200, got {end[1]}"

    infos = [r.getMessage() for r in caplog.records if r.levelname == "INFO"]
    assert any("w=0.30" in m for m in infos), f"Expected clamped w=0.30 in INFO, got: {infos}"


async def test_bbox_returns_true_after_drag_no_post_verify(caplog):
    """B11: valid inputs → True after drag; NO post-drag `page.evaluate`.

    Pointer-trust contract (Option B1, session report §7): once the drag
    lands on the canvas we trust Flow accepted the region. There is no
    useful DOM verify (bbox is canvas-painted) and pixel sampling is noisy
    (video frames are constantly updating the canvas). `page.evaluate`
    must run EXACTLY ONCE (canvas-find), never a second time after drag.

    Trip-wire: if someone re-introduces a post-drag verify step, this test
    breaks — even if the verify happens to return True on the mock.
    """
    canvas_rect = {"left": 0.0, "top": 0.0, "width": 598.0, "height": 336.0, "area": 200928.0}
    page = _make_page(canvas_rect=canvas_rect)

    with caplog.at_level(logging.INFO, logger="flow.operations._base"):
        result = await draw_bbox_on_video(page, {"x": 0.25, "y": 0.25, "w": 0.5, "h": 0.5})

    assert result is True, "valid inputs must succeed under pointer-trust"
    assert page.evaluate.await_count == 1, (
        "page.evaluate must run only once (canvas-find); no post-drag verify step"
    )
    page.mouse.down.assert_awaited_once()
    page.mouse.up.assert_awaited_once()

    infos = [r.getMessage() for r in caplog.records if r.levelname == "INFO"]
    assert any(
        "canvas" in m.lower() and "x=0.25" in m for m in infos
    ), f"Expected INFO mentioning canvas + coords, got: {infos}"
