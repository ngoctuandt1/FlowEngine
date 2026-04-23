"""Camera Move — Level 2 operation.

Navigates to edit URL, clicks Camera, selects a camera preset
(motion or position), submits, waits, downloads.

Camera mode is DIFFERENT from other operations:
- Replaces the composer entirely with a visual preset grid
- No text prompt, no model selector
- User picks a motion preset (e.g. "Dolly in") or position (e.g. "Center")
"""

import asyncio
import logging

from flow.submit import submit_with_confirmation
from flow.operations._base import (
    navigate_to_edit,
    wait_for_video_loaded,
    click_action_button,
    count_visible_cards,
    finalize_operation,
)

logger = logging.getLogger(__name__)

# Camera button (same in EN + VI)
CAMERA_BUTTONS = ["Camera"]
CAMERA_ICON_SELECTOR = "button:has(span:has-text('videocam'))"

# Known camera presets (DOM generic text, EN)
CAMERA_MOTION_PRESETS = [
    "Dolly in", "Dolly out", "Orbit left", "Orbit right",
    "Orbit up", "Orbit low", "Dolly in zoom out", "Dolly out zoom in",
]

CAMERA_POSITION_PRESETS = [
    "Center", "Left", "Right", "High", "Low", "Closer", "Further",
]

ALL_PRESETS = CAMERA_MOTION_PRESETS + CAMERA_POSITION_PRESETS


async def camera_move(
    client,
    job: dict,
    direction: str = "Dolly in",
) -> dict:
    """Execute full camera-move operation (submit + download).

    Back-compat wrapper around ``submit_camera_move`` + ``download_camera_move``.
    """
    ctx = await submit_camera_move(client, job, direction=direction)
    return await download_camera_move(client, job, ctx)


async def submit_camera_move(
    client,
    job: dict,
    direction: str = "Dolly in",
) -> dict:
    """Navigate → select camera preset → submit. Does NOT wait for completion.

    Steps:
    1. Navigate to edit URL
    2. Wait for video to load
    3. Click "Camera" button (replaces composer with preset grid)
    4. Select the correct tab (Camera motion vs Camera position)
    5. Click the preset thumbnail
    6. Submit

    Returns ``{project_id, locale}`` for ``download_camera_move``.
    """
    page = client.page

    # Step 1: Navigate
    edit_url_val, project_id, locale = await navigate_to_edit(client, job)

    # Step 2: Wait for video
    await wait_for_video_loaded(page)

    # Step 3: Click Camera button
    clicked = await click_action_button(page, CAMERA_BUTTONS)
    if not clicked:
        try:
            icon_btn = page.locator(CAMERA_ICON_SELECTOR).first
            if await icon_btn.is_visible(timeout=2000):
                await icon_btn.click(timeout=3000)
                clicked = True
                logger.info("Clicked Camera via icon fallback")
                await asyncio.sleep(0.5)
        except Exception:
            pass

    if not clicked:
        raise RuntimeError("Failed to find Camera button")

    await asyncio.sleep(1)  # Wait for preset grid to render

    # Step 4: Select correct tab
    is_position = direction in CAMERA_POSITION_PRESETS
    tab_name = "Camera position" if is_position else "Camera motion"

    try:
        tab = page.locator(f"[role='tab']:has-text('{tab_name}')").first
        if await tab.is_visible(timeout=2000):
            await tab.click(timeout=3000)
            logger.info("Switched to tab: %s", tab_name)
            await asyncio.sleep(0.5)
    except Exception:
        # Tab might already be active or not exist — proceed
        logger.warning("Could not switch to tab %s — trying preset anyway", tab_name)

    # Step 5: Click preset thumbnail
    preset_clicked = await _click_preset(page, direction)
    if not preset_clicked:
        raise RuntimeError(f"Failed to find camera preset: {direction}")

    # Step 6: Submit
    before_cards = await count_visible_cards(page)
    client.clear_captures()

    confirmed = await submit_with_confirmation(
        client,
        before_card_count=before_cards,
    )
    if not confirmed:
        raise RuntimeError("Camera submit not confirmed")

    return {"project_id": project_id, "locale": locale}


async def download_camera_move(client, job: dict, submit_ctx: dict) -> dict:
    """Wait for the just-submitted camera-move generation and download the output."""
    return await finalize_operation(
        client, job,
        job_type="camera-move",
        project_id=submit_ctx["project_id"],
        locale=submit_ctx["locale"],
        download_prefix="cam",
    )


async def _click_preset(page, direction: str) -> bool:
    """Click a camera preset by name and verify it becomes active.

    Uses Playwright's `get_by_text(direction, exact=True)` — the only strategy
    that matches real Flow DOM. Earlier selector attempts (`[aria-label=...]`,
    `[role='button']` CSS) were removed after Tier1 live-DOM probing confirmed
    they find zero elements on production Flow: presets have no `aria-label`
    and no explicit `role="button"` attribute (they are plain `<button>` tags).

    Playwright's `exact=True` natively prevents partial matches (direction
    "Low" will not match a hypothetical "Lower" button), replacing the
    anchored-regex defense from the pre-B12 implementation.

    See `docs/FLOW_UI_REFERENCE.md` §Camera Preset Selection & Active State
    and `docs/session-reports/2026-04-17_Tier1_dom-validation.md` §7 B3 for
    the live-DOM ground truth.
    """
    try:
        el = page.get_by_text(direction, exact=True).first
        if await el.is_visible(timeout=2000):
            await el.click(timeout=3000)
            logger.info("Clicked preset via get_by_text(exact=True): %s", direction)
            await asyncio.sleep(0.5)
            if await _verify_preset_selected(page, direction):
                return True
    except Exception as e:
        logger.debug("get_by_text strategy failed for %s: %s", direction, e)

    logger.error("Could not click+verify camera preset: %s", direction)
    return False


async def _verify_preset_selected(page, direction: str) -> bool:
    """Verify the named preset is active via the computed label color.

    Flow renders preset buttons using styled-components hash-only class
    names with no stable keyword (`active` / `selected` / `pressed` all
    absent) and no `aria-pressed` / `aria-selected` attributes. The only
    semantic, release-stable selection signal is the computed `color` of
    the inner label DIV inside the preset BUTTON:

        selected   → rgb(48, 48, 48)   (dim; sum 144)
        unselected → rgb(255, 255, 255) (bright; sum 765)

    The JS below walks `<button>` elements, finds the descendant DIV whose
    text equals `direction`, reads `getComputedStyle(lbl).color`, parses
    the `rgb(r, g, b)` form, and returns true when R+G+B < 400. The
    threshold sits halfway between the two ground-truth sums (144 vs 765)
    so small anti-aliasing variance on either side cannot flip the result.

    Returns False on any failure (label not found, color fails to parse,
    `page.evaluate` raises) — caller treats this as unverified.
    """
    try:
        is_selected = await page.evaluate(
            r"""(direction) => {
                const buttons = Array.from(document.querySelectorAll('button'));
                for (const btn of buttons) {
                    const labels = btn.querySelectorAll('div');
                    for (const lbl of labels) {
                        if ((lbl.textContent || '').trim() === direction) {
                            const color = getComputedStyle(lbl).color;
                            const m = color.match(/rgb\((\d+),\s*(\d+),\s*(\d+)\)/);
                            if (!m) return false;
                            const sum = (+m[1]) + (+m[2]) + (+m[3]);
                            return sum < 400;
                        }
                    }
                }
                return false;
            }""",
            direction,
        )
        if is_selected:
            logger.info("Preset verified selected: %s", direction)
            return True
        logger.warning("Preset clicked but not verified active: %s", direction)
        return False
    except Exception as e:
        logger.warning("Preset verify failed for %s: %s", direction, e)
        return False
