"""Shared base for Level-2 operations (extend, insert, remove, camera).

All Level-2 ops navigate to the video's edit URL, perform an action,
wait, download, and return metadata.
"""

import asyncio
import logging

from flow.navigation import flow_url, extract_project_id, extract_media_id, detect_locale
from flow.login import is_login_page, handle_login_redirect
from flow.submit import submit_with_confirmation
from flow.wait import wait_for_completion
from flow.download import download_video
from flow.media_id import media_id_from_url, normalize_media_id, looks_like_media_id

logger = logging.getLogger(__name__)


async def extract_final_media_id(client, job: dict | None = None) -> str | None:
    """Extract the media_id produced by an operation.

    Tries three sources, in order of reliability:
      1. Current page URL ``/edit/{media_uuid}``
      2. ``client._media_id_events`` captured during the wait phase
      3. DOM ``<video>`` element ``src`` query param ``?name={media_id}``
      4. The job's original ``media_id`` (for Level-2 ops that edit in place)

    Returns a normalized media_id string, or ``None`` if nothing was found.
    """
    page = getattr(client, "page", None)

    # 1. URL parse
    try:
        current_url = page.url if page else ""
    except Exception:
        current_url = ""
    mid = extract_media_id(current_url)
    if mid:
        return mid

    # 2. Network capture buffer
    events = getattr(client, "_media_id_events", []) or []
    # Walk newest first — most recent capture is most likely to be the new video
    for evt in reversed(events):
        n = normalize_media_id(evt.get("mid") or evt.get("media_id") or "")
        if n and looks_like_media_id(n):
            return n

    # 3. DOM: <video src="...?name={media_id}">
    if page is not None:
        try:
            video_src = await page.evaluate("""() => {
                const vids = document.querySelectorAll('video');
                for (const v of vids) {
                    const src = v.currentSrc || v.src || '';
                    if (src) return src;
                    const source = v.querySelector('source');
                    if (source && source.src) return source.src;
                }
                return '';
            }""")
        except Exception:
            video_src = ""
        if video_src:
            dom_mid = media_id_from_url(video_src)
            if dom_mid:
                n = normalize_media_id(dom_mid)
                if n and looks_like_media_id(n):
                    return n

    # 4. Fallback: job's original media_id (in-place edits preserve the id)
    if job is not None:
        original = job.get("media_id")
        if original:
            return original

    return None


async def navigate_to_edit(client, job: dict) -> tuple[str, str, str]:
    """Navigate to the video edit page.

    Targeting strategy:
      * When ``job.media_id`` is known, navigate directly to the
        ``/edit/{media_id}`` URL.  This binds the targeted video by id and
        never scans the project grid — so inserting/deleting other videos
        on the project cannot shift which video is edited.
      * When there is no ``media_id`` (legacy jobs that predate media_id
        storage), fall back to the project URL and click a visible tile.

    Uses ``edit_url`` if provided, otherwise builds it from
    ``project_url`` + ``media_id``.

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
        raise RuntimeError(
            f"Cannot navigate: no edit_url, project_url={project_url_val}, media_id={media_id}"
        )

    # Strategy:
    # - When media_id is known, navigate DIRECTLY to /edit/{media_id}.  This
    #   binds the targeted video by id (see spec §10 live test — 100% reliable)
    #   and avoids the fragile grid-card-index path, which shifts any time a
    #   new generation arrives or a video is deleted.
    # - Only legacy jobs that predate media_id storage fall back to the
    #   project URL + tile-click path.
    use_direct_edit = bool(media_id and edit_url_val)
    target_url = edit_url_val if use_direct_edit else (project_url_val or edit_url_val)
    logger.info(
        "Navigating to: %s (strategy=%s)",
        target_url[:100],
        "edit-url" if use_direct_edit else "project-grid",
    )
    await page.goto(target_url, wait_until="domcontentloaded", timeout=30000)
    await asyncio.sleep(3)

    # Handle login redirect if needed
    current = page.url
    if is_login_page(current):
        logger.warning("Login redirect on edit navigation — resolving")
        profile_name = getattr(client, "profile_name", "") or ""
        login_ok = await handle_login_redirect(
            page, timeout=60, profile_name=profile_name,
        )
        if not login_ok:
            raise RuntimeError("Google login required — session expired")
        await page.goto(target_url, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(3)

    # Detect homepage redirect — means project doesn't belong to this account
    current = page.url
    if "/project/" not in current and "/edit/" not in current:
        # Landed on Flow homepage instead of project page
        logger.error(
            "Project not accessible — redirected to homepage. "
            "URL: %s  profile: %s  target: %s",
            current[:100], getattr(client, "profile_name", "?"), target_url[:100],
        )
        raise RuntimeError(
            f"Project not accessible for profile {getattr(client, 'profile_name', '?')} "
            f"— wrong account or project deleted"
        )

    # If we're on the project page (not edit), enter edit mode.
    # For media_id-bound jobs we re-try the direct /edit/ URL — we must NOT
    # fall back to clicking a tile because the grid order is fragile and can
    # target the wrong video.  Legacy jobs (no media_id) use tile click.
    if "/edit/" not in page.url:
        if use_direct_edit:
            logger.info(
                "Not on /edit/ after direct nav — retrying edit URL: %s",
                edit_url_val[:80],
            )
            await page.goto(edit_url_val, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(5)
        else:
            logger.info("Legacy job (no media_id) — clicking video tile to enter edit mode")
            entered = await _click_video_tile(page, "")
            if not entered and edit_url_val:
                logger.info("Tile click failed — trying direct edit URL: %s", edit_url_val[:80])
                await page.goto(edit_url_val, wait_until="domcontentloaded", timeout=30000)
                await asyncio.sleep(5)

    locale = detect_locale(page.url)
    project_id = extract_project_id(page.url) or ""

    return edit_url_val, project_id, locale


async def wait_for_video_loaded(page, timeout_sec: float = 15.0):
    """Wait until a video element is visible on the edit page."""
    try:
        video = page.locator("video").first
        await video.wait_for(state="visible", timeout=timeout_sec * 1000)
        logger.info("Video element loaded")
    except Exception:
        logger.warning("Video element not found after %.0fs — proceeding anyway", timeout_sec)


async def click_action_button(page, button_texts: list[str], timeout_ms: int = 5000) -> bool:
    """Click an action button (Extend/Insert/Remove/Camera).

    Tries each text variant in order. Falls back to icon-based selectors.
    """
    # Try text-based selectors
    for text in button_texts:
        try:
            btn = page.locator(f"button:has-text('{text}')").first
            if await btn.is_visible(timeout=2000):
                await btn.click(timeout=timeout_ms)
                logger.info("Clicked action button: %s", text)
                await asyncio.sleep(0.5)
                return True
        except Exception:
            continue

    return False


async def _click_video_tile(page, media_id: str = "", timeout_sec: float = 10.0) -> bool:
    """Click a video tile in the project view to enter edit mode.

    When direct /edit/ URL navigation fails, the project view shows media
    tiles.  Clicking a video tile enters the edit view with action buttons
    (Extend, Insert, Remove, Camera).

    Tries:
    1. Click video element directly
    2. Click tile container with matching data-tile-id
    3. Click first visible video card / thumbnail
    """
    await asyncio.sleep(2)  # let project view render

    # Try clicking a <video> element (most reliable for video projects)
    try:
        video = page.locator("video").first
        if await video.is_visible(timeout=3000):
            await video.click(timeout=3000)
            logger.info("Clicked video element to enter edit mode")
            await asyncio.sleep(3)
            if "/edit/" in page.url:
                logger.info("Edit mode entered: %s", page.url[:100])
                return True
    except Exception:
        pass

    # Try clicking tile container
    TILE_SELECTORS = [
        "[data-tile-id]",
        "[class*='tile']",
        "[class*='card'] video",
        "[class*='thumbnail']",
        "img[src*='googleusercontent']",
    ]
    for sel in TILE_SELECTORS:
        try:
            tile = page.locator(sel).first
            if await tile.is_visible(timeout=2000):
                await tile.click(timeout=3000)
                logger.info("Clicked tile via: %s", sel)
                await asyncio.sleep(3)
                if "/edit/" in page.url:
                    logger.info("Edit mode entered: %s", page.url[:100])
                    return True
        except Exception:
            continue

    # JS fallback: click first clickable media element in main area
    try:
        clicked = await page.evaluate("""() => {
            // Find video elements or large images
            const videos = document.querySelectorAll('video');
            for (const v of videos) {
                const r = v.getBoundingClientRect();
                if (r.width > 50 && r.height > 50) {
                    v.click();
                    return 'video';
                }
                // Try clicking parent
                if (v.parentElement) {
                    v.parentElement.click();
                    return 'video-parent';
                }
            }
            // Try large image thumbnails
            const imgs = document.querySelectorAll('img[src*="googleusercontent"], img[src*="ggpht"]');
            for (const img of imgs) {
                const r = img.getBoundingClientRect();
                if (r.width > 80 && r.height > 80) {
                    img.click();
                    return 'img';
                }
            }
            return null;
        }""")
        if clicked:
            logger.info("Clicked media via JS: %s", clicked)
            await asyncio.sleep(3)
            if "/edit/" in page.url:
                logger.info("Edit mode entered: %s", page.url[:100])
                return True
    except Exception:
        pass

    return False


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
        raise RuntimeError(f"{job_type} failed: {error}")

    logger.info("%s complete!", job_type)

    # Extract metadata: URL → captured media_id events → DOM <video> src → original
    current_url = page.url
    media_id = await extract_final_media_id(client, job)
    if not media_id and result.get("media_ids"):
        media_id = result["media_ids"][0]

    if not media_id:
        logger.error(
            "%s: failed to extract media_id after completion (url=%s)",
            job_type, current_url[:120],
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
        media_ids=result.get("media_ids", [media_id] if media_id else []),
        prefix=download_prefix,
    )

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
