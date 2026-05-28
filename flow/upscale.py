"""UI-driven upscale helpers for Flow download menus.

Video `/edit/` download menu facts are probed and stable enough to automate:
- `1080pUpscaled` is the only safe upscaled target for video.
- `4KUpscaled · 50 credits` exists on the same video menu and must never match.

Image `/edit/` download menu labels are live-unverified. The selector lists for
`2k` and `4k` therefore use ordered anchored→legacy→loose regexes, and log
every visible menuitem text before clicking so future probes have DOM evidence.

NEVER press Escape on /edit/ view - it closes the entire editor dialog
(see CLAUDE.md §7 Common Gotchas). Menus dismiss by clicking the trigger again
or outside the menu.
"""

import asyncio
import logging
import os
import re
import time
from pathlib import Path
from typing import Awaitable, Callable, Literal

from flow.landing import recover_from_flow_landing

logger = logging.getLogger(__name__)

_DONE_RE = re.compile(
    r"(đã tăng độ phân giải xong|tăng độ phân giải xong|"
    r"upscal\w* complete|upscal\w* done|upscale complete|"
    r"(1080p|2k|4k) ready)",
    re.IGNORECASE,
)
_BUSY_RE = re.compile(
    r"(đang tăng độ phân giải|tăng độ phân giải|"
    r"upscaling(?!\s+done\b)|\bpreparing\b|\brendering\b|\bgenerating\b|"
    r"\bin progress\b|\bplease wait\b|processing (1080|2k|4k))",
    re.IGNORECASE,
)
_FAIL_RE = re.compile(
    r"(unable to upscale|upscale failed|không thể tăng|tăng.*thất bại)",
    re.IGNORECASE,
)

MIN_FILE_SIZE = 100_000

UPSCALE_TIMEOUT_SEC = int(os.environ.get("FLOW_UPSCALE_TIMEOUT_SEC", "360"))
UPSCALE_POLL_SEC = 3
IMAGE_TARGET_QUALITY = Literal["2k", "4k"]
_IMAGE_MENU_PATTERNS: dict[IMAGE_TARGET_QUALITY, tuple[str, ...]] = {
    "2k": (r"^2K\s*Upscaled$", r"^2KUpscaled$", r"\b2K\b"),
    "4k": (r"^4K\s*Upscaled$", r"^4KUpscaled$", r"\b4K\b"),
}


async def _popup_state(page) -> str | None:
    """Scan toast/alert popups. Returns 'done' | 'busy' | 'failed' | None."""
    try:
        texts = await page.evaluate(
            """() => {
            const sels = ['[role="alert"]','[role="alertdialog"]','[role="status"]',
                          '[data-radix-portal]','[data-state="open"]',
                          '[class*="toast"]','[class*="snackbar"]','[class*="notification"]',
                          '[class*="banner"]','[class*="message"]','[class*="popup"]',
                          'aside','output','[aria-live]'];
            const out = [];
            const seen = new Set();
            for (const s of sels)
                for (const el of document.querySelectorAll(s)) {
                    const cs = getComputedStyle(el);
                    if (cs.display==='none'||cs.visibility==='hidden') continue;
                    const r = el.getBoundingClientRect();
                    if (r.width<10) continue;
                    const t = (el.innerText||'').trim();
                    if (t && !seen.has(t)) { seen.add(t); out.push(t); }
                }
            return out;
        }"""
        )
    except Exception:
        return None
    for text in texts or []:
        if _FAIL_RE.search(text):
            return "failed"
        if _DONE_RE.search(text):
            return "done"
        if _BUSY_RE.search(text):
            return "busy"
    return None


async def _wait_upscale(page, timeout_sec: int = UPSCALE_TIMEOUT_SEC) -> str:
    """Poll for toast done/failed. Returns 'done' | 'failed' | 'timeout'."""
    start = time.time()
    logged_busy = False
    while time.time() - start < timeout_sec:
        state = await _popup_state(page)
        elapsed = int(time.time() - start)
        if state == "done":
            logger.info("[UPSCALE] Complete (%ds)", elapsed)
            return "done"
        if state == "failed":
            logger.warning("[UPSCALE] Failed (%ds)", elapsed)
            return "failed"
        if state == "busy" and not logged_busy:
            logged_busy = True
            logger.info("[UPSCALE] Upscaling in progress...")
        if elapsed > 0 and elapsed % 30 == 0:
            logger.info("[UPSCALE] Waiting... (%ds)", elapsed)
        await asyncio.sleep(UPSCALE_POLL_SEC)
    logger.warning("[UPSCALE] Timeout after %ds", timeout_sec)
    return "timeout"


async def _close_toast(page) -> None:
    """Dismiss a toast by its Close button if present. Never use Escape on /edit/."""
    try:
        btn = page.locator("button").filter(
            has_text=re.compile(r"^(close|dismiss|đóng)$", re.IGNORECASE)
        )
        if await btn.count() > 0 and await btn.first.is_visible():
            await btn.first.click(timeout=1500)
            await asyncio.sleep(0.3)
    except Exception:
        pass


async def _click_edit_download_button(page, wait_sec: int = 10) -> bool:
    """Click the icon-only `<button><i>download</i></button>` on /edit/ view.

    Waits up to `wait_sec` for the button to appear (post-generation /edit/ view
    may still be hydrating). Anchored to buttons whose textContent is exactly
    'download' (the Material Icons ligature text) - avoids 'Download app' etc.
    """
    logger.info("[UPSCALE] Current URL: %s", page.url[:100])

    deadline = time.time() + wait_sec
    btn = page.locator("button").filter(
        has=page.locator("i").get_by_text("download", exact=True)
    )
    while time.time() < deadline:
        try:
            if await btn.count() > 0:
                break
        except Exception:
            pass
        await asyncio.sleep(0.5)

    try:
        count = await btn.count()
        logger.info("[UPSCALE] i-tag candidate buttons: %d", count)
        if count > 0:
            await btn.first.click(timeout=3000)
            logger.info("[UPSCALE] Clicked /edit/ download button (i-tag match)")
            return True
    except Exception as exc:
        logger.warning("[UPSCALE] i-tag match click failed: %s", exc)

    btn2 = page.locator("button").filter(
        has_text=re.compile(r"^\s*download\s*$", re.IGNORECASE)
    )
    try:
        count = await btn2.count()
        logger.info("[UPSCALE] text-match candidate buttons: %d", count)
        if count > 0:
            await btn2.first.click(timeout=3000)
            logger.info("[UPSCALE] Clicked /edit/ download button (text match)")
            return True
    except Exception as exc:
        logger.warning("[UPSCALE] text-match click failed: %s", exc)

    logger.warning("[UPSCALE] Download button not found")
    return False


async def _open_edit_download_menu(page, wait_sec: int = 10) -> bool:
    """Open the /edit/ download menu and verify menu items are visible."""
    if not await _click_edit_download_button(page, wait_sec=wait_sec):
        return False
    await asyncio.sleep(0.5)
    try:
        await page.wait_for_selector('[role="menuitem"]', timeout=5000)
        return True
    except Exception as exc:
        logger.warning("[UPSCALE] Download menu did not open: %s", exc)
        return False


async def _log_menuitem_texts(page, *, prefix: str) -> list[str]:
    """Return and log the current open menuitem texts for DOM diagnostics."""
    try:
        menuitems = page.locator('[role="menu"][data-state="open"] [role="menuitem"]')
        texts = [text.strip() for text in await menuitems.all_inner_texts() if text.strip()]
    except Exception as exc:
        logger.warning("%s menuitem text capture failed: %s", prefix, exc)
        return []
    logger.info("%s menu items: %s", prefix, texts)
    return texts


async def _wait_for_download_or_popup(page, downloads: list, rounds: int = 5) -> str | None:
    """Race a download event against popup state for roughly `rounds * 3` seconds."""
    state = None
    for _ in range(rounds):
        await asyncio.sleep(3)
        if downloads:
            return None
        state = await _popup_state(page)
        if state in ("busy", "done", "failed"):
            return state
    return state


async def _capture_download_from_menu(
    page,
    open_menu: Callable[[], Awaitable[bool]],
    click_item: Callable[[], Awaitable[bool]],
    timeout_ms: int = 60_000,
):
    """Open the menu, click an item, and return the Playwright download object."""
    async with page.expect_download(timeout=timeout_ms) as dl_info:
        if not await open_menu():
            return None
        if not await click_item():
            return None
    return await dl_info.value


async def _click_menu_video_1080p(page) -> bool:
    """Click the video menu's `1080pUpscaled` item.

    Video automation intentionally anchors on `1080pUpscaled` first because the
    same menu also contains `4KUpscaled · 50 credits`; the loose `1080p`
    fallback remains safe because 4K/720p/270p do not contain that token.
    """
    try:
        await page.wait_for_selector('[role="menuitem"]', timeout=3000)
    except Exception:
        pass

    q = page.locator('[role="menuitem"]').filter(
        has_text=re.compile(r"^1080pUpscaled$", re.IGNORECASE)
    )
    try:
        if await q.count() > 0:
            await q.first.click(timeout=3000)
            logger.info("[UPSCALE] Clicked 1080pUpscaled menu item")
            return True
    except Exception as exc:
        logger.warning("[UPSCALE] anchored 1080p click failed: %s", exc)

    q2 = page.locator('[role="menuitem"]').filter(
        has_text=re.compile(r"1080p", re.IGNORECASE)
    )
    try:
        if await q2.count() > 0:
            await q2.first.click(timeout=3000)
            logger.info("[UPSCALE] Clicked 1080p menu item (substring match)")
            return True
    except Exception as exc:
        logger.warning("[UPSCALE] substring 1080p click failed: %s", exc)

    logger.warning("[UPSCALE] 1080p menu item not found")
    return False


async def _save_download(download, prefix: str, quality: str, out_dir: Path) -> str | None:
    """Save a Playwright Download to disk. Returns path or None."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        ts = int(time.time())
        filename = f"{prefix}_{quality}_{ts}.mp4"
        path = out_dir / filename
        await download.save_as(str(path))
        size = path.stat().st_size if path.exists() else 0
        if size < MIN_FILE_SIZE:
            logger.warning("[UPSCALE] File too small: %d bytes", size)
            return None
        logger.info("[UPSCALE] Saved: %s (%d bytes)", path, size)
        return str(path)
    except Exception as exc:
        logger.warning("[UPSCALE] Save failed: %s", exc)
        return None


async def _save_image_download(
    download,
    prefix: str,
    quality: IMAGE_TARGET_QUALITY,
    out_dir: Path,
) -> str | None:
    """Save an image download using Flow's shared content-type->extension mapping."""
    out_dir.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        suggested_suffix = Path(download.suggested_filename or "").suffix.lower()
        from flow.download import _extension_for

        ts = int(time.time())
        temp_path = out_dir / f"{prefix}_{quality}_{ts}.download"
        await download.save_as(str(temp_path))
        if not temp_path.exists():
            logger.warning("[UPSCALE] Image download missing after save")
            return None

        body = temp_path.read_bytes()
        if len(body) <= 1_000:
            logger.warning("[UPSCALE] Image file too small: %d bytes", len(body))
            temp_path.unlink(missing_ok=True)
            return None

        extension = _extension_for(_content_type_from_bytes(body), "image")
        if extension == ".jpg" and suggested_suffix == ".jpeg":
            extension = suggested_suffix
        if extension == ".png" and suggested_suffix in {".png", ".webp", ".jpg", ".jpeg"}:
            extension = suggested_suffix
        final_path = out_dir / f"{prefix}_{quality}_{ts}{extension}"
        temp_path.replace(final_path)
        logger.info("[UPSCALE] Saved image: %s (%d bytes)", final_path, len(body))
        return str(final_path)
    except Exception as exc:
        logger.warning("[UPSCALE] Image save failed: %s", exc)
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
        return None


def _content_type_from_bytes(body: bytes) -> str:
    """Infer a minimal image content type for extension mapping."""
    if body.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if body.startswith(b"RIFF") and body[8:12] == b"WEBP":
        return "image/webp"
    if body.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    return ""


async def _ensure_edit_view(page, media_id: str | None = None) -> None:
    """Ensure page is on `/edit/{routing_slug}`. After L1 generation the page
    sits on `/project/{pid}` root - but the icon-only Download button only
    exists on `/project/{pid}/edit/{routing_slug}`.

    Two facts from Run 17d/17e live evidence:
      1. The captured API `media_id` is NOT the /edit/ routing slug
         (`page.goto(/edit/{media_id})` -> SPA bounces back to project root).
      2. `data-tile-id="fe_id_{X}"` on project tiles - `X` is ALSO NOT the
         routing slug (Run 17e: tile id 54ce98c9... -> SPA landed on /edit/
         4ed94c32..., different UUID). Probe §5.5's claim that `fe_id_` prefix
         equals the routing slug is stale / situational.

    Reliable path: click the tile. Flow's SPA router owns the slug resolution;
    clicking triggers its `pushState` + state setup and settles on the correct
    `/edit/{routing_slug}` URL. The `media_id` arg is kept for future reference
    (e.g. multi-tile disambiguation) but is unused here - after fresh L1 t2v
    there is exactly one tile on the project view.
    """
    if await recover_from_flow_landing(page, logger, page.url):
        await asyncio.sleep(1)
    if "/edit/" in page.url:
        # Verify we're on the correct tile when a target media_id is specified.
        # After extend/insert/remove generation the page stays on the SOURCE
        # tile (/edit/{old_mid}) while the download target is the NEW output
        # tile (/edit/{new_mid}).  The download button is data-disabled="" on
        # the wrong tile, so we must switch before attempting the click.
        if media_id and media_id not in page.url:
            from flow.operations._base import _activate_clip_tile

            logger.info(
                "[UPSCALE] Wrong /edit/ tile active (need=%s url=%s) — activating target",
                media_id[:20], page.url[:80],
            )
            activated = await _activate_clip_tile(page, media_id)
            if activated:
                await asyncio.sleep(1)
                return
            # Sidebar tile-switch failed (routing-slug/UUID mismatch).  After
            # extend the source tile's download button is disabled while Flow
            # is still settling the result tile.  Navigate to the project root
            # so the tile-click path below re-enters on the most-recent tile
            # (the extend result whose download button is enabled).
            logger.warning(
                "[UPSCALE] Could not activate tile %s — backing up to project root for tile re-entry",
                media_id[:20],
            )
            project_url = page.url.split("/edit/")[0]
            try:
                await page.goto(project_url, wait_until="domcontentloaded", timeout=10_000)
                await asyncio.sleep(1.0)
            except Exception as exc:
                logger.warning("[UPSCALE] Project root nav failed (%s); giving up", exc)
                return
            # Fall through to tile-click logic below.
        else:
            return
    if "/project/" not in page.url:
        recovered = await recover_from_flow_landing(page, logger, page.url)
        if "/edit/" in page.url:
            return
        if not recovered and "/project/" not in page.url:
            logger.warning("[UPSCALE] Not on a Flow project URL: %s", page.url[:100])
            return

    tile = page.locator('[data-tile-id^="fe_id_"]').first
    try:
        await tile.wait_for(state="attached", timeout=5000)
    except Exception:
        logger.warning("[UPSCALE] No tile with data-tile-id^=fe_id_ on project view")
        return

    logger.info("[UPSCALE] Clicking tile for SPA nav to /edit/ view")
    try:
        await tile.click(timeout=5000)
    except Exception as exc:
        logger.warning("[UPSCALE] Tile click failed: %s", exc)
        return

    deadline = time.time() + 10
    while time.time() < deadline:
        if "/edit/" in page.url:
            logger.info("[UPSCALE] SPA landed on /edit/: %s", page.url[:120])
            await asyncio.sleep(1.5)
            return
        await asyncio.sleep(0.2)
    logger.warning(
        "[UPSCALE] Tile click didn't reach /edit/ after 10s; URL=%s",
        page.url[:100],
    )


async def upscale_and_download_1080p(
    client,
    prefix: str = "vid",
    output_dir: str = "./downloads",
    upscale_timeout_sec: int = UPSCALE_TIMEOUT_SEC,
    max_retries: int = 2,
    media_id: str | None = None,
) -> str | None:
    """UI-driven 1080p video upscale + download on `/edit/` view.

    Flow:
      0. Ensure page is on /edit/{media_id} (deep-link if on project root).
      1. Click Download button -> click 1080pUpscaled.
      2. Poll ~15 s: if a download fires immediately (cached), save and return.
         If a 'busy' toast appears -> wait up to upscale_timeout_sec for 'done'.
         If 'done' or 'failed' -> act accordingly.
      3. After upscale 'done': re-click Download + 1080pUpscaled inside
         `expect_download` to capture the real mp4.

    Returns: path to a `*_1080p_*.mp4` file, or None so the caller can fall
    back to the stable original-size API path.
    """
    page = client.page
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    await _ensure_edit_view(page, media_id)

    downloads: list = []

    def _on_download(download) -> None:
        downloads.append(download)

    page.on("download", _on_download)

    try:
        for attempt in range(1, max_retries + 1):
            logger.info("[UPSCALE] Attempt %d/%d", attempt, max_retries)

            if not await _open_edit_download_menu(page):
                await asyncio.sleep(1.5)
                continue
            if not await _click_menu_video_1080p(page):
                await asyncio.sleep(1)
                continue

            state = await _wait_for_download_or_popup(page, downloads)
            if downloads:
                path = await _save_download(downloads[0], prefix, "1080p", out_dir)
                if path:
                    return path
                downloads.clear()

            if state == "done":
                await _close_toast(page)
                path = await _redownload_1080p(page, prefix, out_dir)
                if path:
                    return path
                continue

            if state == "busy":
                wait_result = await _wait_upscale(page, upscale_timeout_sec)
                await _close_toast(page)
                if downloads:
                    path = await _save_download(downloads[0], prefix, "1080p", out_dir)
                    if path:
                        return path
                    downloads.clear()
                if wait_result == "done":
                    path = await _redownload_1080p(page, prefix, out_dir)
                    if path:
                        return path
                continue

            if state == "failed":
                logger.warning("[UPSCALE] Failed toast on attempt %d", attempt)
                await _close_toast(page)
                continue

            logger.warning(
                "[UPSCALE] No download/toast after 15s on attempt %d",
                attempt,
            )

        logger.warning(
            "[UPSCALE] All %d attempts failed - caller falls back to 720p",
            max_retries,
        )
        return None
    finally:
        try:
            page.remove_listener("download", _on_download)
        except Exception:
            pass


async def _redownload_1080p(page, prefix: str, out_dir: Path) -> str | None:
    """After the ready toast, re-open the menu and click `1080pUpscaled` again."""
    logger.info("[UPSCALE] Re-triggering 1080p download...")
    await asyncio.sleep(0.5)
    try:
        download = await _capture_download_from_menu(
            page,
            lambda: _open_edit_download_menu(page),
            lambda: _click_menu_video_1080p(page),
        )
        if download is None:
            return None
        return await _save_download(download, prefix, "1080p", out_dir)
    except Exception as exc:
        logger.warning("[UPSCALE] Re-download failed: %s", exc)
        return None


async def _click_menu_image_target(page, target_quality: IMAGE_TARGET_QUALITY) -> bool:
    """Click an image upscale target using anchored->legacy->loose regexes.

    The image menu labels are not yet probe-confirmed, so each quality keeps an
    ordered regex list:
    1. modern spaced label, e.g. `2K Upscaled`
    2. legacy collapsed label, e.g. `2KUpscaled`
    3. loose `2K`/`4K` token fallback for diagnosis-oriented resilience
    """
    try:
        await page.wait_for_selector('[role="menuitem"]', timeout=3000)
    except Exception:
        pass
    await _log_menuitem_texts(page, prefix=f"[UPSCALE][IMAGE][{target_quality}]")

    for pattern in _IMAGE_MENU_PATTERNS[target_quality]:
        locator = page.locator('[role="menuitem"]').filter(
            has_text=re.compile(pattern, re.IGNORECASE)
        )
        try:
            if await locator.count() > 0:
                await locator.first.click(timeout=3000)
                logger.info(
                    "[UPSCALE][IMAGE] Clicked %s item with regex %s",
                    target_quality,
                    pattern,
                )
                return True
        except Exception as exc:
            logger.warning(
                "[UPSCALE][IMAGE] %s click failed for regex %s: %s",
                target_quality,
                pattern,
                exc,
            )

    logger.warning("[UPSCALE][IMAGE] %s menu item not found", target_quality)
    return False


async def upscale_and_download_image(
    client,
    *,
    prefix: str,
    output_dir: str,
    media_id: str | None,
    target_quality: IMAGE_TARGET_QUALITY = "2k",
) -> str | None:
    """Attempt a UI-driven image upscale download for `2k` or `4k`.

    Returns None on any failure so `flow/download.py` can fall back to the
    existing original-quality API path with no behavior change when the menu
    differs from assumptions.
    """
    page = client.page
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    downloads: list = []

    def _on_download(download) -> None:
        downloads.append(download)

    try:
        await _ensure_edit_view(page, media_id)
        page.on("download", _on_download)

        for attempt in range(1, 3):
            logger.info(
                "[UPSCALE][IMAGE] Attempt %d/2 for %s",
                attempt,
                target_quality,
            )
            downloads.clear()

            if not await _open_edit_download_menu(page):
                await asyncio.sleep(1.5)
                continue
            if not await _click_menu_image_target(page, target_quality):
                await asyncio.sleep(1)
                continue

            state = await _wait_for_download_or_popup(page, downloads)
            if downloads:
                path = await _save_image_download(
                    downloads[0],
                    prefix,
                    target_quality,
                    out_dir,
                )
                if path:
                    return path
                downloads.clear()

            if state == "busy":
                wait_result = await _wait_upscale(page, UPSCALE_TIMEOUT_SEC)
                await _close_toast(page)
                if downloads:
                    path = await _save_image_download(
                        downloads[0],
                        prefix,
                        target_quality,
                        out_dir,
                    )
                    if path:
                        return path
                    downloads.clear()
                if wait_result == "done":
                    download = await _capture_download_from_menu(
                        page,
                        lambda: _open_edit_download_menu(page),
                        lambda: _click_menu_image_target(page, target_quality),
                    )
                    if download is None:
                        continue
                    path = await _save_image_download(
                        download,
                        prefix,
                        target_quality,
                        out_dir,
                    )
                    if path:
                        return path
                continue

            if state == "done":
                await _close_toast(page)
                download = await _capture_download_from_menu(
                    page,
                    lambda: _open_edit_download_menu(page),
                    lambda: _click_menu_image_target(page, target_quality),
                )
                if download is None:
                    continue
                path = await _save_image_download(
                    download,
                    prefix,
                    target_quality,
                    out_dir,
                )
                if path:
                    return path
                continue

            if state == "failed":
                logger.warning(
                    "[UPSCALE][IMAGE] Failed toast on attempt %d for %s",
                    attempt,
                    target_quality,
                )
                await _close_toast(page)
                continue

            logger.warning(
                "[UPSCALE][IMAGE] No download/toast after 15s for %s on attempt %d",
                target_quality,
                attempt,
            )
            return None

        return None
    except Exception as exc:
        logger.warning("[UPSCALE][IMAGE] %s download failed: %s", target_quality, exc)
        return None
    finally:
        try:
            page.remove_listener("download", _on_download)
        except Exception:
            pass
