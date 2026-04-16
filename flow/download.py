"""Download pipeline -- API-driven (primary) + UI-driven (fallback)."""

import asyncio
import base64
import logging
import os
import re
import time
from pathlib import Path

logger = logging.getLogger(__name__)

DOWNLOAD_DIR = os.environ.get("FLOW_DOWNLOAD_DIR", "./downloads")
UPSCALE_MAX_WAIT = int(os.environ.get("FLOW_UPSCALE_MAX_WAIT_SEC", "180"))
UPSCALE_POLL_INTERVAL = int(os.environ.get("FLOW_UPSCALE_POLL_INTERVAL_SEC", "10"))
MIN_FILE_SIZE = 100_000  # 100KB minimum for valid video


async def download_video(
    client,
    media_ids: list[str] | None = None,
    prefix: str = "vid",
    quality: str = "1080p",
) -> list[str]:
    """Download generated video(s).

    Fallback chain:
    1. API-driven: media.getMediaUrlRedirect?name={id}_upsampled (1080p)
    2. API-driven: ?name={id} (720p)
    3. UI-driven: right-click card -> Download -> 1080p
    4. Blob URL: fetch video blob in browser

    Returns list of downloaded file paths.
    """
    page = client.page
    output_dir = Path(DOWNLOAD_DIR)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Collect media IDs if not provided
    if not media_ids:
        media_ids = [
            evt["media_id"]
            for evt in getattr(client, "_media_id_events", [])
            if evt.get("media_id")
        ]

    if not media_ids:
        # Try extracting from video URLs
        try:
            from flow.media_id import media_id_from_url
        except ImportError:
            media_id_from_url = None

        if media_id_from_url:
            for url in getattr(client, "_video_urls", [])[-5:]:
                mid = media_id_from_url(url)
                if mid:
                    media_ids.append(mid)

    if not media_ids:
        logger.warning("No media IDs found for download")
        # Fallback: try UI download
        result = await _download_via_ui(client, prefix, output_dir)
        return [result] if result else []

    downloaded = []
    for mid in media_ids:
        # Try API download (1080p first, then 720p)
        path = await _download_via_api(client, mid, prefix, quality, output_dir)
        if path:
            downloaded.append(path)
            continue

        # Fallback: UI download
        path = await _download_via_ui(client, prefix, output_dir)
        if path:
            downloaded.append(path)

    return downloaded


async def _download_via_api(
    client, media_id: str, prefix: str, quality: str, output_dir: Path
) -> str | None:
    """Download via API redirect URL."""
    page = client.page

    # Try 1080p first (upsampled)
    if quality == "1080p":
        url_1080 = (
            "https://labs.google/fx/api/trpc/"
            f"media.getMediaUrlRedirect?name={media_id}_upsampled"
        )
        path = await _api_download_with_retry(
            page, url_1080, prefix, "1080p", output_dir
        )
        if path:
            return path

    # Fallback to 720p
    url_720 = (
        "https://labs.google/fx/api/trpc/"
        f"media.getMediaUrlRedirect?name={media_id}"
    )
    return await _api_download_with_retry(page, url_720, prefix, "720p", output_dir)


async def _api_download_with_retry(
    page,
    url: str,
    prefix: str,
    quality: str,
    output_dir: Path,
    max_retries: int = 3,
) -> str | None:
    """Download from API URL with upscale polling."""
    for attempt in range(max_retries):
        try:
            # Use page.request to get the URL (carries cookies)
            response = await page.request.get(url, timeout=30000)

            if response.status == 200:
                content_type = response.headers.get("content-type", "")
                if "video" in content_type or "octet-stream" in content_type:
                    body = await response.body()
                    if len(body) > MIN_FILE_SIZE:
                        ts = int(time.time())
                        filename = f"{prefix}_{quality}_{ts}.mp4"
                        filepath = output_dir / filename
                        filepath.write_bytes(body)
                        logger.info(
                            "Downloaded %s: %s (%d bytes)",
                            quality,
                            filepath,
                            len(body),
                        )
                        return str(filepath)

                # 200 but redirected -- may need to follow
                redirect_url = response.headers.get("location")
                if redirect_url:
                    return await _fetch_and_save(
                        page, redirect_url, prefix, quality, output_dir
                    )

            elif response.status in (202, 404) and "upsampled" in url:
                # Upscale not ready yet, wait and retry
                logger.info(
                    "Upscale in progress, attempt %d/%d", attempt + 1, max_retries
                )
                await asyncio.sleep(UPSCALE_POLL_INTERVAL)
                continue
            else:
                logger.warning(
                    "API download status %d: %s", response.status, url[:80]
                )

        except Exception as e:
            logger.warning("API download error: %s", e)

    return None


async def _fetch_and_save(
    page, url: str, prefix: str, quality: str, output_dir: Path
) -> str | None:
    """Fetch a direct URL via browser and save."""
    try:
        response = await page.request.get(url, timeout=60000)
        if response.status == 200:
            body = await response.body()
            if len(body) > MIN_FILE_SIZE:
                ts = int(time.time())
                filename = f"{prefix}_{quality}_{ts}.mp4"
                filepath = output_dir / filename
                filepath.write_bytes(body)
                logger.info(
                    "Fetched %s: %s (%d bytes)", quality, filepath, len(body)
                )
                return str(filepath)
    except Exception as e:
        logger.warning("Fetch error: %s", e)
    return None


async def _download_via_ui(client, prefix: str, output_dir: Path) -> str | None:
    """Download via UI right-click menu on video card."""
    page = client.page

    try:
        # Find a video element or download button
        # First try the Download button in edit view
        dl_btn = (
            page.locator("button, [role='button']")
            .filter(has_text=re.compile(r"download|tải xuống", re.IGNORECASE))
            .first
        )

        if await dl_btn.is_visible(timeout=2000):
            # Set up download handler
            async with page.expect_download(timeout=60000) as dl_info:
                await dl_btn.click()
                # If a quality submenu appears, click 1080p
                try:
                    quality_btn = (
                        page.locator("[role='menuitem']")
                        .filter(has_text=re.compile(r"1080p", re.IGNORECASE))
                        .first
                    )
                    if await quality_btn.is_visible(timeout=2000):
                        await quality_btn.click()
                except Exception:
                    pass

            download = await dl_info.value
            ts = int(time.time())
            filename = f"{prefix}_ui_{ts}.mp4"
            filepath = output_dir / filename
            await download.save_as(str(filepath))

            if filepath.stat().st_size > MIN_FILE_SIZE:
                logger.info("UI download: %s", filepath)
                return str(filepath)
    except Exception as e:
        logger.warning("UI download failed: %s", e)

    # Last resort: extract blob URL from video element
    return await _download_blob(page, prefix, output_dir)


async def _download_blob(page, prefix: str, output_dir: Path) -> str | None:
    """Extract and download video from blob: URL in browser."""
    try:
        blob_data = await page.evaluate(
            """async () => {
            const video = document.querySelector('video');
            if (!video || !video.src) return null;

            const src = video.currentSrc || video.src;
            if (!src) return null;

            try {
                const resp = await fetch(src);
                const blob = await resp.blob();
                const reader = new FileReader();
                return new Promise((resolve) => {
                    reader.onloadend = () => resolve(reader.result);
                    reader.readAsDataURL(blob);
                });
            } catch(e) { return null; }
        }"""
        )

        if blob_data and blob_data.startswith("data:"):
            # data:video/mp4;base64,....
            _, encoded = blob_data.split(",", 1)
            raw = base64.b64decode(encoded)
            if len(raw) > MIN_FILE_SIZE:
                ts = int(time.time())
                filename = f"{prefix}_blob_{ts}.mp4"
                filepath = output_dir / filename
                filepath.write_bytes(raw)
                logger.info("Blob download: %s (%d bytes)", filepath, len(raw))
                return str(filepath)
    except Exception as e:
        logger.warning("Blob download failed: %s", e)

    return None
