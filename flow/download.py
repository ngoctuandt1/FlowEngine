"""Download pipeline -- API-driven (primary) + UI-driven (fallback)."""

import asyncio
import base64
import logging
import os
import re
import time
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)

DOWNLOAD_DIR = os.environ.get("FLOW_DOWNLOAD_DIR", "./downloads")
UPSCALE_MAX_WAIT = int(os.environ.get("FLOW_UPSCALE_MAX_WAIT_SEC", "180"))
# B34 (2026-04-19): bumped from 10s → 15s + retries 3 → 12 (total
# ~180s) to cover Flow's real upscale latency. Pre-B34 every live Tier
# 2 run (Run 10 + Run 12) fell through to 720p because `_upsampled`
# returned 202/404 at 30s cumulative poll (3 × 10s) — too short.
# Evidence: `downloads/` folder had zero `_1080p_` files across all
# runs until this bump. Env overrides preserved for ops tuning.
# B34b (2026-04-19, Run 15 residual P3): bumped retries 12 → 24 (total
# ~360s) because Run 15's 3 t2v jobs all still fell through to 720p
# at 180s. Flow's upscale on 9:16 / 16:9 fast-LP clips is longer than
# initially estimated. 720p fallback is stable (B37 fix makes the
# harvest deterministic), so this is purely a quality upgrade — no
# correctness / credit impact.
UPSCALE_POLL_INTERVAL = int(os.environ.get("FLOW_UPSCALE_POLL_INTERVAL_SEC", "15"))
UPSCALE_MAX_RETRIES = int(os.environ.get("FLOW_UPSCALE_MAX_RETRIES", "24"))
MIN_FILE_SIZE = 100_000  # 100KB minimum for valid video
IMAGE_MIN_FILE_SIZE = 1_000
IMAGE_QUALITY_ENV = "FLOW_IMAGE_QUALITY"
IMAGE_UPSCALE_QUALITIES = {"2k", "4k"}
ImageQuality = Literal["original", "2k", "4k"]


async def download_video(
    client,
    media_ids: list[str] | None = None,
    prefix: str = "vid",
    quality: str = "1080p",
    media_kind: str = "video",
) -> list[str]:
    """Download generated video(s).

    Fallback chain:
    1. UI-driven 1080p upscale (flow/upscale.py) — primary when quality=="1080p"
       since the `_upsampled` API endpoint returns 404 permanently (see
       session-reports/2026-04-19_download-probe.md §5.4).
    2. API-driven: ?name={id} (720p)
    3. UI-driven: right-click card -> Download -> 1080p (legacy fallback)
    4. Blob URL: fetch video blob in browser

    Returns list of downloaded file paths.
    """
    page = client.page
    output_dir = Path(DOWNLOAD_DIR)
    output_dir.mkdir(parents=True, exist_ok=True)
    requested_image_quality = (
        _requested_image_quality(quality) if media_kind == "image" else "original"
    )

    # B38: UI-driven 1080p upscale is the primary path for 1080p. The API
    # `_upsampled` endpoint has been 404 for all probes (see probe report
    # §5.4). Only the UI-triggered POST uploadImage + toast-poll flow works.
    # Fall through to 720p API only if the UI path fails.
    if media_kind == "video" and quality == "1080p":
        # Determine target media_id for /edit/ deep-link (upscale module
        # navigates if page is still on project root after L1 generate).
        first_mid = None
        if media_ids:
            first_mid = media_ids[0]
        else:
            evts = getattr(client, "_media_id_events", [])
            for evt in evts:
                if evt.get("mid"):
                    first_mid = evt["mid"]
                    break

        try:
            from flow.upscale import upscale_and_download_1080p

            ui_path = await upscale_and_download_1080p(
                client,
                prefix=prefix,
                output_dir=DOWNLOAD_DIR,
                media_id=first_mid,
            )
            if ui_path:
                return [ui_path]
            logger.info("UI 1080p path returned None; falling back to 720p API")
        except Exception as e:
            logger.warning("UI 1080p upscale raised: %s; falling back to API", e)

    if media_kind == "image" and requested_image_quality in IMAGE_UPSCALE_QUALITIES:
        target_mids = list(media_ids) if media_ids else []
        if not target_mids:
            evts = getattr(client, "_media_id_events", [])
            for evt in evts:
                if evt.get("mid"):
                    target_mids.append(evt["mid"])
                    break

        upscaled_paths = []
        mid = None
        try:
            from flow.upscale import upscale_and_download_image

            for mid in target_mids:
                ui_path = await upscale_and_download_image(
                    client,
                    prefix=prefix,
                    output_dir=DOWNLOAD_DIR,
                    media_id=mid,
                    target_quality=requested_image_quality,
                )
                if ui_path:
                    upscaled_paths.append(ui_path)
                    continue
                logger.info(
                    "Image UI %s path returned None for mid=%s; will try API fallback",
                    requested_image_quality,
                    mid,
                )
        except Exception as e:
            logger.warning(
                "Image UI %s upscale raised for mid=%s: %s",
                requested_image_quality,
                mid,
                e,
            )

        if upscaled_paths:
            return upscaled_paths

    # Collect media IDs if not provided
    if not media_ids:
        media_ids = [
            evt["mid"]
            for evt in getattr(client, "_media_id_events", [])
            if evt.get("mid")
        ]

    if not media_ids:
        # Try extracting from video URLs
        try:
            from flow.media_id import media_id_from_url
        except ImportError:
            media_id_from_url = None

        if media_id_from_url:
            for entry in getattr(client, "_video_urls", [])[-5:]:
                url = entry["url"] if isinstance(entry, dict) else entry
                mid = media_id_from_url(url)
                if mid:
                    media_ids.append(mid)

    if not media_ids:
        logger.warning("No media IDs found for download")
        # Fallback: try UI download
        result = await _download_via_ui(client, prefix, output_dir, media_kind)
        return [result] if result else []

    # B38: When the caller asked for 1080p and we're past the UI path above,
    # skip the stale `_upsampled` API poll — go straight to 720p. Saves ~6min.
    if media_kind == "video" and quality == "1080p":
        api_quality = "720p"
    elif media_kind == "image":
        api_quality = "original"
    else:
        api_quality = quality

    downloaded = []
    for mid in media_ids:
        path = await _download_via_api(client, mid, prefix, api_quality, output_dir, media_kind)
        if path:
            downloaded.append(path)
            continue

        # Fallback: UI download
        path = await _download_via_ui(client, prefix, output_dir, media_kind)
        if path:
            downloaded.append(path)

    return downloaded


async def _download_via_api(
    client, media_id: str, prefix: str, quality: str, output_dir: Path, media_kind: str
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
            page, url_1080, prefix, "1080p", output_dir, media_kind
        )
        if path:
            return path

    fallback_quality = quality if media_kind == "image" else "720p"
    url_fallback = (
        "https://labs.google/fx/api/trpc/"
        f"media.getMediaUrlRedirect?name={media_id}"
    )
    return await _api_download_with_retry(
        page,
        url_fallback,
        prefix,
        fallback_quality,
        output_dir,
        media_kind,
    )


async def _api_download_with_retry(
    page,
    url: str,
    prefix: str,
    quality: str,
    output_dir: Path,
    media_kind: str,
    max_retries: int | None = None,
) -> str | None:
    # B34: default retries = UPSCALE_MAX_RETRIES (env-configurable 12) instead
    # of the pre-B34 hardcoded 3. At UPSCALE_POLL_INTERVAL=15s, total wait is
    # ~180s — matches observed Flow upscale latency envelope on 9:16/8s clips.
    # Caller may override for 720p path (retries matter less there; no polling
    # for upsample-ready, just network transient recovery).
    if max_retries is None:
        max_retries = UPSCALE_MAX_RETRIES
    """Download from API URL with upscale polling."""
    for attempt in range(max_retries):
        try:
            # Use page.request to get the URL (carries cookies)
            response = await page.request.get(url, timeout=30000)

            if response.status == 200:
                content_type = response.headers.get("content-type", "")
                if _response_matches_media(content_type, media_kind):
                    body = await response.body()
                    if len(body) > _minimum_size_for(media_kind):
                        ts = int(time.time())
                        filename = f"{prefix}_{quality}_{ts}{_extension_for(content_type, media_kind)}"
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
                        page, redirect_url, prefix, quality, output_dir, media_kind
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
    page, url: str, prefix: str, quality: str, output_dir: Path, media_kind: str
) -> str | None:
    """Fetch a direct URL via browser and save."""
    try:
        response = await page.request.get(url, timeout=60000)
        if response.status == 200:
            body = await response.body()
            if len(body) > _minimum_size_for(media_kind):
                ts = int(time.time())
                content_type = response.headers.get("content-type", "")
                filename = f"{prefix}_{quality}_{ts}{_extension_for(content_type, media_kind)}"
                filepath = output_dir / filename
                filepath.write_bytes(body)
                logger.info(
                    "Fetched %s: %s (%d bytes)", quality, filepath, len(body)
                )
                return str(filepath)
    except Exception as e:
        logger.warning("Fetch error: %s", e)
    return None


async def _download_via_ui(client, prefix: str, output_dir: Path, media_kind: str) -> str | None:
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
            suggested = download.suggested_filename or ""
            suffix = Path(suggested).suffix or _extension_for("", media_kind)
            filename = f"{prefix}_ui_{ts}{suffix}"
            filepath = output_dir / filename
            await download.save_as(str(filepath))

            if filepath.stat().st_size > _minimum_size_for(media_kind):
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


def _response_matches_media(content_type: str, media_kind: str) -> bool:
    if media_kind == "image":
        return "image/" in content_type or "octet-stream" in content_type
    return "video" in content_type or "octet-stream" in content_type


def _extension_for(content_type: str, media_kind: str) -> str:
    content_type = (content_type or "").lower()
    if media_kind == "image":
        if "png" in content_type:
            return ".png"
        if "webp" in content_type:
            return ".webp"
        if "jpeg" in content_type or "jpg" in content_type:
            return ".jpg"
        return ".png"
    return ".mp4"


def _minimum_size_for(media_kind: str) -> int:
    return IMAGE_MIN_FILE_SIZE if media_kind == "image" else MIN_FILE_SIZE


def _requested_image_quality(explicit_quality: str) -> ImageQuality:
    """Resolve image quality with env override while defaulting to original.

    `download_video(..., quality="original", media_kind="image")` remains the
    normal call site. Setting `FLOW_IMAGE_QUALITY=2k|4k` opts into the new UI
    path without forcing signature churn through the operation layer.
    """
    quality = (explicit_quality or "").strip().lower()
    env_quality = os.environ.get(IMAGE_QUALITY_ENV, "original").strip().lower()
    if quality in IMAGE_UPSCALE_QUALITIES:
        return quality
    if quality not in {"", "original"}:
        return "original"
    if env_quality in IMAGE_UPSCALE_QUALITIES:
        return env_quality
    return "original"
