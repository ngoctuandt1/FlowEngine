"""Shared base for Level-2 operations (extend, insert, remove, camera).

All Level-2 ops navigate to the video's edit URL, perform an action,
wait, download, and return metadata.
"""

import asyncio
import logging

from flow.failure_capture import message_with_failure_capture
from flow.navigation import (
    detect_locale,
    extract_media_id,
    extract_project_id,
    find_latest_tile_slug,
    flow_url,
)
from flow.landing import recover_from_flow_landing
from flow.login import is_login_page, handle_login_redirect
from flow.submit import submit_with_confirmation
from flow.wait import wait_for_completion
from flow.download import download_video

logger = logging.getLogger(__name__)


def failure_kind_from_error(job_type: str, error: str) -> str:
    text = str(error).split("[cap=", 1)[0].strip().lower()
    for token in (
        "blocked_403",
        "blocked_429",
        "no_signal_timeout",
        "timeout",
        "all_failed",
        "no_credits",
        "policy",
    ):
        if token in text:
            return token
    return f"{job_type.replace('-', '_')}_failed"


async def navigate_to_edit(client, job: dict) -> tuple[str, str, str]:
    """Navigate to the video edit page.

    Uses edit_url if available, otherwise constructs from project_url + media_id.

    Returns (edit_url, project_id, locale).
    """
    page = client.page

    edit_url_val = job.get("edit_url") or ""
    project_url_val = job.get("project_url") or ""
    media_id = job.get("media_id") or ""

    # Build edit URL if not directly provided
    if not edit_url_val and project_url_val and media_id:
        locale = detect_locale(project_url_val)
        project_id = extract_project_id(project_url_val)
        if project_id:
            from flow.navigation import edit_url as build_edit_url
            edit_url_val = build_edit_url(project_id, media_id, locale)

    if not edit_url_val:
        message = (
            f"Cannot navigate: no edit_url, project_url={project_url_val}, media_id={media_id}"
        )
        message = await message_with_failure_capture(client, "no_edit_url", message)
        raise RuntimeError(message)

    # Strategy: direct goto(edit_url) is the fast path. On EN-locale Google
    # accounts the Flow SPA mounts the editor on /edit/{media_id} without
    # needing the project grid preloaded — verified 2026-04-19 by
    # `scripts/probe_direct_edit_url.py` on `ngoctuandt20` post-language-
    # switch (submit chip, model chip, textarea all present on direct goto).
    #
    # Fallback: if the SPA bounces to /project/{id} (rare — e.g. temporary
    # locale flap or media moved), the `/edit/ not in page.url` block below
    # falls through to tile-click on the project grid.
    target_url = edit_url_val
    logger.info("Navigating to edit URL: %s", target_url[:100])
    await page.goto(target_url, wait_until="domcontentloaded", timeout=30000)
    await asyncio.sleep(3)

    # Handle login redirect if needed
    current = page.url
    if is_login_page(current):
        logger.warning("Login redirect on edit navigation — resolving")
        profile_name = getattr(client, "profile_name", "") or ""
        login_ok = await handle_login_redirect(
            page, timeout=60, profile_name=profile_name, client=client,
        )
        if not login_ok:
            message = "Google login required — session expired"
            message = await message_with_failure_capture(
                client,
                "google_login_required",
                message,
            )
            raise RuntimeError(message)
        await page.goto(target_url, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(3)

    # Detect homepage redirect — means project doesn't belong to this account
    await _recover_editor_landing(page, target_url)
    current = page.url

    if "/project/" not in current and "/edit/" not in current:
        # Landed on Flow homepage instead of project page
        logger.error(
            "Project not accessible — redirected to homepage. "
            "URL: %s  profile: %s  target: %s",
            current[:100], getattr(client, "profile_name", "?"), target_url[:100],
        )
        message = (
            f"Project not accessible for profile {getattr(client, 'profile_name', '?')} "
            f"— wrong account or project deleted"
        )
        message = await message_with_failure_capture(
            client,
            "project_not_accessible",
            message,
        )
        raise RuntimeError(message)

    # If we're on the project page (not edit), click a video tile
    if "/edit/" not in page.url:
        logger.info("On project view — clicking video tile to enter edit mode")
        entered = await _click_video_tile(page, job.get("media_id", ""))
        if not entered:
            # Last resort: try direct edit URL
            logger.info("Tile click failed — trying direct edit URL: %s", edit_url_val[:80])
            await page.goto(edit_url_val, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(5)
            await _recover_editor_landing(page, target_url)

    # Verify we're in edit mode for the right media
    current = page.url
    if "/edit/" not in current:
        # B29 (2026-04-19): when an L1 /edit/{media_id} points at a media
        # that's been consumed by a sibling extend, the SPA strips `/edit/`
        # and leaves us on /project/ — the tile-click fallback then can't
        # recover. Surface this as a B22-inheritance hint so operators
        # look at the claim-time ancestor rather than hunt DOM changes.
        logger.error("SPA stripped /edit/ segment. URL: %s", current[:100])
        message = (
            f"SPA stripped /edit/ segment → {current}. "
            f"Media may be stale post-sibling-extend. Check B22 inheritance."
        )
        message = await message_with_failure_capture(
            client,
            "spa_stripped_edit_route",
            message,
        )
        raise RuntimeError(message)

    # B32 (2026-04-19): job.media_id is the SEMANTIC target (what the
    # operation wants to edit), which after B30 walk-up may differ from
    # the direct parent's edit_url media (e.g. L3 insert after L2 extend
    # inherits L1 grandparent's media_id but parent's edit_url). If the
    # URL's media differs from the target, activate the target clip via
    # history-panel tile click — Flow's SPA then enables Insert/Remove/
    # Camera on the target clip even though the URL still shows the
    # extend-output. Verified live 2026-04-19 on project 513d580b (B32
    # probe session): clicking [data-tile-id="fe_id_{target_media_id}"]
    # in the right sidebar flips all 4 mode buttons from disabled →
    # enabled without changing page.url.
    current_media = extract_media_id(current)
    # Flow's SPA sometimes accepts `page.goto(/edit/{X})` by updating the URL
    # without remounting the editor (observed 2026-04-23 on L2 remove directly
    # after L2 insert: URL = target, but <video> never rendered → 15s timeout).
    # Activating the target tile forces the SPA to re-hydrate the editor state,
    # so do it unconditionally when media_id is known — not only when the URL
    # disagrees. Idempotent when the tile is already active.
    if media_id:
        if current_media and current_media != media_id:
            logger.info(
                "URL media differs from target: url=%s target=%s — activating target tile",
                current_media[:20], media_id[:20],
            )
        activated = await _activate_clip_tile(page, media_id)
        if not activated and current_media != media_id:
            logger.warning(
                "Could not activate target clip tile for media=%s — sidebar may be disabled (B28 lockout)",
                media_id[:20],
            )

    # B39 (2026-04-23): the URL-strip branch above catches the SPA bouncing
    # to /project/ on stale media, but Flow's other failure mode keeps
    # /edit/{stale_media_id} in the URL with an un-mounted editor — no
    # <video>, no mode buttons. Observed on insert-object whose parent
    # media had been consumed by a sibling extend. Catch it here with a
    # bounded video wait and fall back to first-tile click (the same
    # recovery the /project/ branch above uses) to land on /edit/{latest}.
    if not await _editor_mounted(page, timeout_ms=8000):
        logger.warning(
            "Editor did not mount after nav to %s — falling back to first-tile click",
            edit_url_val[:80],
        )
        recovered = await _click_video_tile(page, "")
        if not recovered or not await _editor_mounted(page, timeout_ms=8000):
            message = (
                f"Editor did not mount for {edit_url_val} and first-tile recovery "
                f"failed. Parent media may be stale (consumed by sibling op)."
            )
            message = await message_with_failure_capture(
                client,
                "editor_not_mounted",
                message,
            )
            raise RuntimeError(message)
        if media_id:
            await _activate_clip_tile(page, media_id)

    locale = detect_locale(page.url)
    project_id = extract_project_id(page.url) or ""

    return edit_url_val, project_id, locale


async def _editor_mounted(page, timeout_ms: int = 8000) -> bool:
    """Return True when the /edit/ composer has rendered its <video>.

    Used as a post-navigation sanity check — distinguishes a truly
    mounted editor from the SPA's half-loaded state where the URL
    reads /edit/{media} but no editor DOM is present.
    """
    try:
        await page.locator("video").first.wait_for(state="visible", timeout=timeout_ms)
        return True
    except Exception:
        return False


async def _recover_editor_landing(page, target_url: str) -> bool:
    """Recover if Flow rendered the marketing landing over the intended route."""
    return await recover_from_flow_landing(page, logger, target_url)


async def _activate_clip_tile(page, media_id: str, timeout_sec: float = 3.0) -> bool:
    """Click the history-panel clip tile for `media_id` to activate it.

    Used after `navigate_to_edit` when the URL's media differs from the
    semantic target (B30 walk-up case: L3 insert/remove/camera after an
    extend-video parent inherits L1's grandparent media_id but lands on
    the extend-output's edit URL).

    Live evidence 2026-04-19 (project 513d580b probe): the history panel
    renders each project clip as a `<div data-tile-id="fe_id_{media_id}">`
    at the right side. The tile has no button ancestor — a DOM-level
    click handler on the DIV switches the active clip. Dispatching a
    real MouseEvent (not `.click()` which may not trigger styled-
    components) re-enables Insert/Remove/Camera for the targeted clip.

    Args:
      page: Playwright Page inside the /edit/ composer.
      media_id: Target media UUID — typically from `job["media_id"]` after
        B30 walk-up (the nearest non-extend ancestor).

    Returns:
      True if the tile was found and clicked, False otherwise.
    """
    if not media_id:
        return False
    # Wait briefly for the history panel to render
    try:
        tile = page.locator(f"[data-tile-id='fe_id_{media_id}']").first
        await tile.wait_for(state="attached", timeout=int(timeout_sec * 1000))
    except Exception:
        logger.debug("Clip tile not found for media=%s within %.1fs", media_id[:20], timeout_sec)
        return False
    try:
        # The tile is a <div> with no button ancestor; click() may miss
        # the custom handler. Dispatch a full pointer sequence via JS.
        ok = await page.evaluate(
            """(mid) => {
                const tile = document.querySelector(
                    `[data-tile-id="fe_id_${mid}"]`
                );
                if (!tile) return false;
                const rect = tile.getBoundingClientRect();
                const cx = rect.x + rect.width / 2;
                const cy = rect.y + rect.height / 2;
                for (const type of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
                    tile.dispatchEvent(new MouseEvent(type, {
                        bubbles: true, cancelable: true, view: window,
                        clientX: cx, clientY: cy, button: 0,
                    }));
                }
                return true;
            }""",
            media_id,
        )
        if ok:
            # Give Flow a beat to swap active clip + re-enable sidebar
            await asyncio.sleep(1)
            logger.info("Activated clip tile for media=%s", media_id[:20])
            return True
    except Exception as e:
        logger.warning("Tile click dispatch failed for media=%s: %s", media_id[:20], e)
    return False


async def wait_for_video_loaded(page, timeout_sec: float = 15.0):
    """Wait until a video element is visible on the edit page."""
    await _recover_editor_landing(page, page.url)
    try:
        video = page.locator("video").first
        await video.wait_for(state="visible", timeout=timeout_sec * 1000)
        logger.info("Video element loaded")
    except Exception:
        logger.warning("Video element not found after %.0fs — proceeding anyway", timeout_sec)


_MODE_ICON_BY_TITLE = {
    "Mở rộng": "keyboard_double_arrow_right",
    "Extend": "keyboard_double_arrow_right",
    "Chèn": "add_box",
    "Insert": "add_box",
    "Xoá": "ink_eraser",
    "Xóa": "ink_eraser",
    "Remove": "ink_eraser",
    "Delete": "ink_eraser",
    "Camera": "videocam",
}


async def click_action_button(
    page,
    button_texts: list[str],
    timeout_ms: int = 5000,
    *,
    client=None,
) -> bool:
    """Click a mode-switch button (Extend/Insert/Remove/Camera) on /edit/.

    Live-verified 2026-04-19 on VI profile: each mode button has a stable
    EXACT ``title`` attribute and an EXACT Material Icon ligature inside
    its ``<i>`` child. Title is the authoritative primary selector; icon
    is a locale-independent fallback.

    Identity (exact):
      * ``button[title="Mở rộng"]``  → icon ``keyboard_double_arrow_right``
      * ``button[title="Chèn"]``     → icon ``add_box``
      * ``button[title="Xoá"]``      → icon ``ink_eraser``
      * ``button[title="Camera"]``   → icon ``videocam``

    Do NOT use fuzzy ``:has-text`` — the Camera button's textContent is
    "videocam\\nCamera" and matched ``:has-text('videocam')`` in B26,
    causing a silent URL revert from /edit/ to /project/.
    """
    # Pass 1 — exact title match (VI labels are unique, stable)
    for text in button_texts:
        try:
            btn = page.locator(f"button[title='{text}']").first
            if await btn.is_visible(timeout=1500):
                # B28 (2026-04-19): on extend-output /edit/{new_media} the
                # Insert/Remove/Camera buttons render but Flow sets them
                # disabled ("extend-child lockout"). Pre-B28 the click would
                # time out with a misleading "Failed to find button" error.
                # Raise early with the B22-inheritance diagnostic instead.
                if not await btn.is_enabled():
                    message = (
                        f"Mode button {text!r} disabled — extend-child lockout "
                        f"(FLOW_BUTTON_EXACT §5.1). Check B22 inheritance."
                    )
                    message = await message_with_failure_capture(
                        client,
                        "extend_child_lockout",
                        message,
                    )
                    raise RuntimeError(message)
                await btn.click(timeout=timeout_ms)
                logger.info("Clicked mode button via title=%r", text)
                await asyncio.sleep(0.5)
                return True
        except RuntimeError:
            raise
        except Exception:
            continue

    # Pass 2 — icon fallback (locale-independent).  Find the unique icon
    # for the requested mode, then click the ancestor <button>.
    for text in button_texts:
        icon = _MODE_ICON_BY_TITLE.get(text)
        if not icon:
            continue
        try:
            btn = page.locator(f"button:has(i:text-is('{icon}'))").first
            if await btn.is_visible(timeout=1500):
                if not await btn.is_enabled():
                    message = (
                        f"Mode button {text!r} disabled — extend-child lockout "
                        f"(FLOW_BUTTON_EXACT §5.1). Check B22 inheritance."
                    )
                    message = await message_with_failure_capture(
                        client,
                        "extend_child_lockout",
                        message,
                    )
                    raise RuntimeError(message)
                await btn.click(timeout=timeout_ms)
                logger.info("Clicked mode button via icon=%r (requested title=%r)", icon, text)
                await asyncio.sleep(0.5)
                return True
        except RuntimeError:
            raise
        except Exception:
            continue

    return False


async def _click_video_tile(page, media_id: str = "", timeout_sec: float = 10.0) -> bool:
    """Click a video tile in the project view to enter edit mode.

    When direct /edit/ URL navigation fails, the project view shows media
    tiles.  Clicking a tile navigates to /edit/{media_id}.

    Priority:
    1. If media_id given: JS click on tile whose link/data contains media_id
    2. First [data-tile-id] tile
    3. First video element
    """
    await asyncio.sleep(2)  # let project view render

    # Priority 1: click tile matching media_id via JS
    if media_id:
        try:
            clicked = await page.evaluate("""(targetId) => {
                // Look for links containing the media_id
                const links = document.querySelectorAll('a[href*="/edit/"]');
                for (const a of links) {
                    if (a.href.includes(targetId)) {
                        a.click();
                        return 'link:' + targetId.substring(0, 12);
                    }
                }
                // Look for tiles with data attributes matching media_id
                const tiles = document.querySelectorAll('[data-tile-id]');
                for (const tile of tiles) {
                    const tileId = tile.getAttribute('data-tile-id') || '';
                    if (tileId.includes(targetId) || targetId.includes(tileId)) {
                        tile.click();
                        return 'tile:' + tileId.substring(0, 12);
                    }
                }
                // Look for any element with media_id in attributes
                const all = document.querySelectorAll('[data-media-id], [data-id]');
                for (const el of all) {
                    const id = el.getAttribute('data-media-id') || el.getAttribute('data-id') || '';
                    if (id.includes(targetId)) {
                        el.click();
                        return 'data-id:' + id.substring(0, 12);
                    }
                }
                return null;
            }""", media_id)
            if clicked:
                logger.info("Clicked tile for media_id via JS: %s", clicked)
                await asyncio.sleep(3)
                if "/edit/" in page.url:
                    logger.info("Edit mode entered: %s", page.url[:100])
                    return True
        except Exception:
            pass

    # Priority 2: click first [data-tile-id] tile
    try:
        tile = page.locator("[data-tile-id]").first
        if await tile.is_visible(timeout=3000):
            await tile.click(timeout=3000)
            logger.info("Clicked first [data-tile-id] tile")
            await asyncio.sleep(3)
            if "/edit/" in page.url:
                logger.info("Edit mode entered: %s", page.url[:100])
                return True
    except Exception:
        pass

    # Priority 3: click first video element
    try:
        video = page.locator("video").first
        if await video.is_visible(timeout=3000):
            await video.click(timeout=3000)
            logger.info("Clicked video element")
            await asyncio.sleep(3)
            if "/edit/" in page.url:
                logger.info("Edit mode entered: %s", page.url[:100])
                return True
    except Exception:
        pass

    return False


async def draw_bbox_on_video(page, bbox: dict) -> bool:
    """Draw a bounding box on the Flow preview canvas via mouse drag.

    Shared between insert-object and remove-object ops. The caller must
    have already clicked Insert/Remove so the preview is in bbox-drawing
    mode.

    Target: the LARGEST visible `<canvas>` with `width ≥ 300`. On an L1
    project Flow's preview is a `<canvas width=598 height=336>` (CSS-sized
    ~479×269) centered on screen. The `<video>` tag exists on the page
    but is a 105×60 card-strip thumbnail — never target it (B2 regression).

    Verify: pointer-trust — no post-drag DOM check and no pixel sampling.
    Flow paints the bbox onto the canvas 2D bitmap (confirmed Tier1:
    `elementFromPoint` inside the visible bbox returns `<CANVAS>`), so the
    B2 union selector `svg rect, [class*="bbox" i], …` matches 0 elements
    regardless of drag success. Pixel sampling is also unreliable because
    the preview plays video frames continuously — `getImageData` deltas
    are noisy even without a drag. Pointer delivery onto the correct
    canvas rect is the load-bearing signal; if that happens, Flow accepts
    the region. See `docs/session-reports/2026-04-17_B11_bbox-canvas-fix.md`
    §7 for the Option A vs B decision rationale.

    Args:
        bbox: `{x, y, w, h}` normalized 0-1 relative to the canvas rect.
              Values outside [0, 1] → reject (return False). Overflow
              (`x+w>1` or `y+h>1`) is clamped to fit.

    Returns:
        True after the drag sequence completes on the target canvas.
        False on genuine pre-drag failures: no visible canvas ≥ 300×200,
        or any bbox key out of range. Caller logs a WARNING and continues
        (Flow falls back to its default region on unreliable bbox input).

    See `docs/FLOW_UI_REFERENCE.md` §Bbox Overlay UI for the live-DOM
    ground truth that drove this design.
    """
    # Step 1: Find the largest visible <canvas> (width ≥ 300). Flow's
    # preview canvas is the only one that size; card-strip canvases are
    # smaller thumbnails.
    canvas_rect = await page.evaluate("""() => {
        const canvases = Array.from(document.querySelectorAll('canvas'));
        let best = null;
        for (const c of canvases) {
            const r = c.getBoundingClientRect();
            if (r.width < 300 || r.height < 200) continue;
            const area = r.width * r.height;
            if (!best || area > best.area) {
                best = {left: r.left, top: r.top, width: r.width, height: r.height, area: area};
            }
        }
        return best;
    }""")

    if not canvas_rect:
        logger.error("Preview canvas not found (no visible <canvas> ≥ 300×200)")
        return False

    # Step 2: Validate bbox keys in [0, 1]
    for k in ("x", "y", "w", "h"):
        v = bbox.get(k, 0)
        if not (0 <= v <= 1):
            logger.error("bbox[%s]=%s out of range 0-1", k, v)
            return False

    x = bbox.get("x", 0.25)
    y = bbox.get("y", 0.25)
    w = bbox.get("w", 0.5)
    h = bbox.get("h", 0.5)

    # Step 3: Clamp overflow so bbox fits within canvas rect
    if x + w > 1:
        w = 1 - x
    if y + h > 1:
        h = 1 - y

    cl = canvas_rect["left"]
    ct = canvas_rect["top"]
    cw = canvas_rect["width"]
    ch = canvas_rect["height"]

    start_x = cl + x * cw
    start_y = ct + y * ch
    end_x = cl + (x + w) * cw
    end_y = ct + (y + h) * ch

    # Step 4: Mouse drag on the canvas — 5 interpolation steps (Flow needs a
    # real, gradual drag; a single move→down→up does not register).
    await page.mouse.move(start_x, start_y)
    await page.mouse.down()
    await asyncio.sleep(0.1)
    steps = 5
    for i in range(1, steps + 1):
        px = start_x + (end_x - start_x) * i / steps
        py = start_y + (end_y - start_y) * i / steps
        await page.mouse.move(px, py)
        await asyncio.sleep(0.05)
    await page.mouse.up()
    await asyncio.sleep(0.5)

    # Step 5: Pointer-trust. No post-drag verify (bbox is canvas-painted;
    # DOM selectors cannot detect it and pixel sampling is noisy — see
    # docstring + session report §7).
    logger.info(
        "Drew bbox on canvas: x=%.2f y=%.2f w=%.2f h=%.2f canvas=%dx%d",
        x, y, w, h, int(cw), int(ch),
    )
    return True


async def count_visible_cards(page) -> int:
    """Count visible media cards on page."""
    try:
        return await page.evaluate("""() => {
            const videos = document.querySelectorAll('video');
            const tiles = document.querySelectorAll('[data-tile-id]');
            return Math.max(videos.length, tiles.length);
        }""")
    except Exception:
        return 0


async def finalize_operation(
    client,
    job: dict,
    job_type: str,
    project_id: str,
    locale: str,
    download_prefix: str = "op",
) -> dict:
    """Common post-submit flow: wait -> download -> extract metadata -> return result.

    This is called after submit_with_confirmation() succeeds.
    """
    page = client.page

    # Wait for completion
    logger.info("Waiting for %s completion...", job_type)
    result = await wait_for_completion(client, job_type=job_type)

    if not result.get("done"):
        error = result.get("error", "unknown")
        message = f"{job_type} failed: {error}"
        message = await message_with_failure_capture(
            client,
            failure_kind_from_error(job_type, error),
            message,
        )
        raise RuntimeError(message)

    logger.info("%s complete!", job_type)

    current_url = page.url
    parent_media_id = job.get("media_id")
    download_media_ids = result.get("media_ids") or []
    media_id = await resolve_final_media_id(
        page,
        fallback=parent_media_id,
        parent_media_id=parent_media_id,
        download_media_ids=download_media_ids,
    )

    # Build edit_url
    edit_url_val = None
    if media_id and project_id:
        base = flow_url(locale)
        edit_url_val = f"{base}/project/{project_id}/edit/{media_id}"

    # Download
    logger.info("Downloading %s result...", job_type)
    output_files = await download_video(
        client,
        media_ids=download_media_ids or ([media_id] if media_id else []),
        prefix=download_prefix,
    )
    if not output_files:
        message = f"{job_type}: no output file captured"
        message = await message_with_failure_capture(
            client,
            f"{job_type.replace('-', '_')}_no_output_file",
            message,
        )
        raise RuntimeError(message)

    # Build project_url
    proj_url = job.get("project_url")
    if not proj_url and project_id:
        proj_url = f"{flow_url(locale)}/project/{project_id}"

    return {
        "project_url": proj_url or "",
        "media_id": media_id,
        "edit_url": edit_url_val or current_url,
        "output_files": output_files,
        "generation_id": client._gen_id,
        "profile": client.profile_name,
    }


async def _extract_settled_route_media_id(page, fallback: str | None = None) -> str | None:
    """Poll briefly for a settled /edit/{slug} route before falling back."""
    for _ in range(12):
        media_id = extract_media_id(page.url)
        if media_id:
            return media_id
        await asyncio.sleep(0.25)
    return fallback


async def resolve_final_media_id(
    page,
    *,
    fallback: str | None = None,
    parent_media_id: str | None = None,
    download_media_ids: list[str] | None = None,
) -> str | None:
    """Resolve the canonical media slug for a completed operation.

    Priority (live-verified 2026-04-23, B39):
    1. Network-captured generation mid — the first ``download_media_ids``
       slug that isn't ``parent_media_id``. These are the real ids minted
       by Flow's backend during submit and are the strongest signal.
    2. Latest history tile slug when it differs from the parent — the UI's
       own declaration of "most recent output". Tile strip beats URL,
       which often carries a clip-route slug neither parent nor new
       output.
    3. Settled ``/edit/{slug}`` route — last resort; may be stale on the
       extend-child path.
    4. ``fallback`` — used only when the URL never settles to an /edit/
       slug (typically the parent media, so callers can still build a
       usable edit_url).
    """
    download_media_ids = download_media_ids or []
    network_media_id = next(
        (mid for mid in download_media_ids if mid and mid != parent_media_id),
        None,
    )
    if network_media_id:
        logger.info(
            "media_id from network events: %s", network_media_id[:20]
        )
        return network_media_id

    tile_media_id = await find_latest_tile_slug(page)
    if tile_media_id and tile_media_id != parent_media_id:
        url_media_id = extract_media_id(page.url)
        logger.warning(
            "No new network mid; using latest tile slug: parent=%s url=%s tile=%s",
            (parent_media_id or "")[:20],
            (url_media_id or "")[:20],
            tile_media_id[:20],
        )
        return tile_media_id

    return await _extract_settled_route_media_id(page, fallback=fallback)
