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

logger = logging.getLogger(__name__)


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
        raise RuntimeError(
            f"Cannot navigate: no edit_url, project_url={project_url_val}, media_id={media_id}"
        )

    logger.info("Navigating to edit page: %s", edit_url_val[:100])
    await page.goto(edit_url_val, wait_until="domcontentloaded", timeout=30000)
    await asyncio.sleep(2)  # Let video load

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
        # Re-navigate to edit URL after login
        await page.goto(edit_url_val, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(2)
        current = page.url

    # Verify we're on the right page
    if "/edit/" not in current:
        logger.warning("Not on edit page after navigation: %s", current[:100])

    locale = detect_locale(current)
    project_id = extract_project_id(current) or ""

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

    # Extract metadata from current URL
    current_url = page.url
    media_id = extract_media_id(current_url)
    if not media_id and result.get("media_ids"):
        media_id = result["media_ids"][0]
    # Fallback to job's original media_id (operations update in-place)
    if not media_id:
        media_id = job.get("media_id")

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
