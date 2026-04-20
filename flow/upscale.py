"""1080p upscale + download via UI (cherry-picked from AI-Engine3-Project
modules/upscale_unified.py; async port + /edit/ view adaptation).

B38 (2026-04-19): The `_upsampled` endpoint that `flow/download.py` polls
returns HTTP 404 permanently (see session-reports/2026-04-19_download-probe.md
§5.4) — so B34/B34b poll-window bumps could never succeed. Modern UI triggers
upscale via `POST aisandbox-pa.googleapis.com/v1/flow/uploadImage` (probe §5.3).
This module replaces the broken API poll with a UI-driven flow:

On `/edit/{media_id}` view (primary entry after generate / extend / camera):
1. Click icon-only Download button (top-right). DOM: `<button><i>download</i></button>`.
2. Menu opens with 4 items: 270p · 720p · 1080pUpscaled · 4KUpscaled · 50 credits.
3. Click `1080pUpscaled` → triggers upscale (if not cached) OR downloads immediately.
4. On upscale: wait for "Upscaling complete!" toast (EN) / "Đã tăng độ phân giải xong!" (VI).
5. Re-click Download button → menu → `1080pUpscaled` → real mp4 download.

Safety: `4KUpscaled · 50 credits` is 50 LP credits per click — NEVER matched
by our selector (anchored regex `^1080pUpscaled$` on the menuitem textContent).

NEVER press Escape on /edit/ view — it closes the entire editor dialog
(see CLAUDE.md §7 Common Gotchas). Menus dismiss by clicking the trigger again
or outside the menu.
"""

import asyncio
import logging
import os
import re
import time
from pathlib import Path

from flow.landing import recover_from_flow_landing

logger = logging.getLogger(__name__)

_DONE_RE = re.compile(
    r"(đã tăng độ phân giải xong|tăng độ phân giải xong|"
    r"upscal\w* complete|upscal\w* done|1080p ready)",
    re.IGNORECASE,
)
_BUSY_RE = re.compile(
    r"(đang tăng độ phân giải|tăng độ phân giải|upscaling|processing 1080)",
    re.IGNORECASE,
)
_FAIL_RE = re.compile(
    r"(unable to upscale|upscale failed|không thể tăng|tăng.*thất bại)",
    re.IGNORECASE,
)

MIN_FILE_SIZE = 100_000

UPSCALE_TIMEOUT_SEC = int(os.environ.get("FLOW_UPSCALE_TIMEOUT_SEC", "360"))
UPSCALE_POLL_SEC = 3


async def _popup_state(page):
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
    for t in (texts or []):
        if _FAIL_RE.search(t):
            return "failed"
        if _DONE_RE.search(t):
            return "done"
        if _BUSY_RE.search(t):
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
    'download' (the Material Icons ligature text) — avoids 'Download app' etc.
    """
    logger.info("[UPSCALE] Current URL: %s", page.url[:100])

    # Wait for any candidate button to show up (post-gen /edit/ view hydration)
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
    except Exception as e:
        logger.warning("[UPSCALE] i-tag match click failed: %s", e)

    # Fallback: button whose total text is exactly 'download' (case-insensitive)
    btn2 = page.locator("button").filter(
        has_text=re.compile(r"^\s*download\s*$", re.IGNORECASE)
    )
    try:
        c2 = await btn2.count()
        logger.info("[UPSCALE] text-match candidate buttons: %d", c2)
        if c2 > 0:
            await btn2.first.click(timeout=3000)
            logger.info("[UPSCALE] Clicked /edit/ download button (text match)")
            return True
    except Exception as e:
        logger.warning("[UPSCALE] text-match click failed: %s", e)

    logger.warning("[UPSCALE] Download button not found")
    return False


async def _click_menu_1080p(page) -> bool:
    """In an open Radix menu, click the '1080pUpscaled' item.

    Uses anchored regex to exclude the '4KUpscaled · 50 credits' sibling (50 LP cost).
    """
    try:
        await page.wait_for_selector('[role="menuitem"]', timeout=3000)
    except Exception:
        pass

    # Anchored: textContent must be EXACTLY '1080pUpscaled' (probe §5.2)
    q = page.locator('[role="menuitem"]').filter(
        has_text=re.compile(r"^1080pUpscaled$", re.IGNORECASE)
    )
    try:
        if await q.count() > 0:
            await q.first.click(timeout=3000)
            logger.info("[UPSCALE] Clicked 1080pUpscaled menu item")
            return True
    except Exception as e:
        logger.warning("[UPSCALE] anchored 1080p click failed: %s", e)

    # Fallback: any menuitem containing '1080p' (still excludes 4K — '4KUpscaled' has no '1080p')
    q2 = page.locator('[role="menuitem"]').filter(
        has_text=re.compile(r"1080p", re.IGNORECASE)
    )
    try:
        if await q2.count() > 0:
            await q2.first.click(timeout=3000)
            logger.info("[UPSCALE] Clicked 1080p menu item (substring match)")
            return True
    except Exception as e:
        logger.warning("[UPSCALE] substring 1080p click failed: %s", e)

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
    except Exception as e:
        logger.warning("[UPSCALE] Save failed: %s", e)
        return None


async def _ensure_edit_view(page, media_id: str | None = None) -> None:
    """Ensure page is on `/edit/{routing_slug}`. After L1 generation the page
    sits on `/project/{pid}` root — but the icon-only Download button only
    exists on `/project/{pid}/edit/{routing_slug}`.

    Two facts from Run 17d/17e live evidence:
      1. The captured API `media_id` is NOT the /edit/ routing slug
         (`page.goto(/edit/{media_id})` → SPA bounces back to project root).
      2. `data-tile-id="fe_id_{X}"` on project tiles — `X` is ALSO NOT the
         routing slug (Run 17e: tile id 54ce98c9… → SPA landed on /edit/
         4ed94c32…, different UUID). Probe §5.5's claim that `fe_id_` prefix
         equals the routing slug is stale / situational.

    Reliable path: **click the tile**. Flow's SPA router owns the slug
    resolution; clicking triggers its `pushState` + state setup and settles
    on the correct `/edit/{routing_slug}` URL. The `media_id` arg is kept
    for future reference (e.g. multi-tile disambiguation) but is unused here
    — after fresh L1 t2v there is exactly one tile on the project view.
    """
    if await recover_from_flow_landing(page, logger, page.url):
        await asyncio.sleep(1)
    if "/edit/" in page.url:
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
    except Exception as e:
        logger.warning("[UPSCALE] Tile click failed: %s", e)
        return

    deadline = time.time() + 10
    while time.time() < deadline:
        if "/edit/" in page.url:
            logger.info("[UPSCALE] SPA landed on /edit/: %s", page.url[:120])
            await asyncio.sleep(1.5)  # let view hydrate (button mount, icons)
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
    """UI-driven 1080p upscale + download on /edit/ view.

    Flow:
      0. Ensure page is on /edit/{media_id} (deep-link if on project root).
      1. Click Download button → click 1080pUpscaled.
      2. Poll ~15 s: if a download fires immediately (cached), save and return.
         If a 'busy' toast appears → wait up to upscale_timeout_sec for 'done'.
         If 'done' or 'failed' → act accordingly.
      3. After upscale 'done': re-click Download + 1080pUpscaled inside
         `expect_download` to capture the real mp4.

    Returns: path to 1080p mp4, or None (caller should fall back to 720p API).
    """
    page = client.page
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    await _ensure_edit_view(page, media_id)

    downloads: list = []

    def _on_download(d):
        downloads.append(d)

    page.on("download", _on_download)

    try:
        for attempt in range(1, max_retries + 1):
            logger.info("[UPSCALE] Attempt %d/%d", attempt, max_retries)

            if not await _click_edit_download_button(page):
                await asyncio.sleep(1.5)
                continue

            if not await _click_menu_1080p(page):
                await asyncio.sleep(1)
                continue

            # Race: download arrives fast (cached) OR busy/done/failed toast shows.
            state = None
            for _ in range(5):
                await asyncio.sleep(3)
                if downloads:
                    break
                state = await _popup_state(page)
                if state in ("busy", "done", "failed"):
                    break

            if downloads:
                path = await _save_download(downloads[0], prefix, "1080p", out_dir)
                if path:
                    return path
                downloads.clear()

            if state == "done":
                # Already upscaled (rare race). Re-click to pull file.
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
                "[UPSCALE] No download/toast after 15s on attempt %d", attempt
            )

        logger.warning(
            "[UPSCALE] All %d attempts failed — caller falls back to 720p",
            max_retries,
        )
        return None
    finally:
        try:
            page.remove_listener("download", _on_download)
        except Exception:
            pass


async def _redownload_1080p(page, prefix: str, out_dir: Path) -> str | None:
    """After 'done' toast: re-click Download + 1080pUpscaled inside expect_download."""
    logger.info("[UPSCALE] Re-triggering 1080p download...")
    await asyncio.sleep(0.5)
    try:
        async with page.expect_download(timeout=60_000) as dl_info:
            if not await _click_edit_download_button(page):
                return None
            if not await _click_menu_1080p(page):
                return None
        download = await dl_info.value
        return await _save_download(download, prefix, "1080p", out_dir)
    except Exception as e:
        logger.warning("[UPSCALE] Re-download failed: %s", e)
        return None
