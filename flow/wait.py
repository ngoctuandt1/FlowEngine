"""Wait for Flow generation to complete.

Polls three independent signal sources (reverse-API, network video
captures, DOM observer) to detect when a generation finishes or fails.
"""

import asyncio
import logging
import time

logger = logging.getLogger(__name__)

# Default timeouts per job type (seconds)
TIMEOUTS: dict[str, int] = {
    "text-to-video": 900,   # 15 min (LP can be slow)
    "extend-video": 600,    # 10 min
    "insert-object": 300,   # 5 min
    "remove-object": 300,
    "camera-move": 300,
}

# Abort if zero progress signals for this many seconds
NO_SIGNAL_TIMEOUT = 180


# ------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------

async def wait_for_completion(
    client,
    job_type: str = "text-to-video",
    timeout: int | None = None,
) -> dict:
    """Wait for generation to complete.

    Args:
        client:   FlowClient instance (has ``.page``, ``._calls``,
                  ``._video_urls``, ``._media_id_events``).
        job_type: One of the keys in ``TIMEOUTS``.
        timeout:  Override the per-type timeout (seconds).

    Returns dict::

        {
            "done": bool,
            "media_ids": list[str],
            "video_urls": list[str],
            "error": str | None,
        }

    Detection methods (checked every 500 ms):
      1. Reverse API -- scan ``client._calls`` for ``operations/``
         responses with ``done: true``.
      2. Network -- new video URLs in ``client._video_urls``.
      3. DOM observer -- injected JS tracking progress % and new
         ``<video>`` elements.
    """
    if timeout is None:
        timeout = TIMEOUTS.get(job_type, 300)

    page = client.page

    await _inject_observer(page)

    initial_video_count = len(getattr(client, "_video_urls", []))
    start = time.monotonic()
    last_progress = 0
    last_signal_time = start

    while True:
        elapsed = time.monotonic() - start

        # Hard timeout
        if elapsed > timeout:
            logger.error("Wait timeout after %.0fs", elapsed)
            return _result(False, error="timeout")

        # --- Method 1: reverse API inspection ---
        api = _check_api_signals(client)
        if api["done"]:
            logger.info("Completion via API signal after %.0fs", elapsed)
            return _result(True, media_ids=api["media_ids"])
        if api["error"]:
            logger.error("API error: %s", api["error"])
            return _result(False, error=api["error"])
        if api["progress"] > last_progress:
            last_progress = api["progress"]
            last_signal_time = time.monotonic()

        # --- Method 2: network video captures ---
        video_urls = getattr(client, "_video_urls", [])
        new_videos = video_urls[initial_video_count:]
        if new_videos:
            logger.info("New video URL captured after %.0fs", elapsed)
            last_signal_time = time.monotonic()
            # Let media-ID events settle
            await asyncio.sleep(3)
            return _result(
                True,
                video_urls=list(new_videos),
                media_ids=_collect_media_ids(client),
            )

        # --- Method 3: DOM observer ---
        dom = await _read_observer(page)
        if dom["progress"] > last_progress:
            last_progress = dom["progress"]
            last_signal_time = time.monotonic()
        if dom["error"]:
            logger.error("DOM error: %s", dom["error"])
            return _result(False, error=dom["error"])
        if dom["progress"] >= 100 and dom["new_video"]:
            logger.info(
                "Completion via DOM (100%% + new video) after %.0fs", elapsed
            )
            await asyncio.sleep(2)  # let media settle
            return _result(
                True,
                media_ids=_collect_media_ids(client),
                video_urls=video_urls[initial_video_count:],
            )

        # --- No-signal watchdog ---
        silence = time.monotonic() - last_signal_time
        if silence > NO_SIGNAL_TIMEOUT:
            logger.error("No signal for %ds, aborting", NO_SIGNAL_TIMEOUT)
            return _result(False, error="no_signal_timeout")

        # Periodic progress log (every ~30s)
        if int(elapsed) % 30 == 0 and elapsed > 0:
            logger.info("Waiting... %.0fs, progress=%d%%", elapsed, last_progress)

        await asyncio.sleep(0.5)


# ------------------------------------------------------------------
# Result builder
# ------------------------------------------------------------------

def _result(
    done: bool,
    *,
    media_ids: list[str] | None = None,
    video_urls: list[str] | None = None,
    error: str | None = None,
) -> dict:
    return {
        "done": done,
        "media_ids": media_ids or [],
        "video_urls": video_urls or [],
        "error": error,
    }


# ------------------------------------------------------------------
# Signal readers
# ------------------------------------------------------------------

def _check_api_signals(client) -> dict:
    """Scan ``client._calls`` for operation status."""
    result: dict = {
        "progress": 0,
        "done": False,
        "error": None,
        "media_ids": [],
    }

    calls = getattr(client, "_calls", [])
    # Walk newest first, cap scan at last 100 entries
    for call in reversed(calls[-100:]):
        url = call.get("url", "")
        body = call.get("body", {})
        status = call.get("status", 0)

        # reCAPTCHA / quota block
        if status in (403, 429):
            result["error"] = f"blocked_{status}"
            return result

        if "operations/" not in url or not isinstance(body, dict):
            continue

        progress = body.get("progressPercentage", 0)
        if progress > result["progress"]:
            result["progress"] = progress

        if body.get("done"):
            result["done"] = True
            return result

        err = body.get("error")
        if err:
            result["error"] = str(err)
            return result

    return result


def _collect_media_ids(client) -> list[str]:
    """Collect all known media IDs from client state."""
    ids: set[str] = set()
    for evt in getattr(client, "_media_id_events", []):
        mid = evt.get("media_id")
        if mid:
            ids.add(mid)
    return list(ids)


# ------------------------------------------------------------------
# DOM observer (injected JS)
# ------------------------------------------------------------------

_OBSERVER_JS = """() => {
    if (window.__flowObserverActive) return;
    window.__flowObserverActive = true;
    window.__flowProgress   = 0;
    window.__flowError      = '';
    window.__flowNewVideo   = false;
    window.__flowVideoCount = document.querySelectorAll('video').length;

    setInterval(() => {
        try {
            const body = document.body.innerText || '';

            // Extract highest progress percentage
            const matches = body.match(/(\\d{1,3})%/g);
            if (matches) {
                for (const m of matches) {
                    const pct = parseInt(m);
                    if (pct > window.__flowProgress && pct <= 100) {
                        window.__flowProgress = pct;
                    }
                }
            }

            // Detect error states
            if (/all.*failed|generation.*failed/i.test(body)) {
                window.__flowError = 'ALL_FAILED';
            }
            if (/no.*credits/i.test(body) && /insufficient/i.test(body)) {
                window.__flowError = 'NO_CREDITS';
            }
            if (/policy|violated/i.test(body)) {
                window.__flowError = 'POLICY';
            }

            // Detect new <video> elements
            const currentCount = document.querySelectorAll('video').length;
            if (currentCount > window.__flowVideoCount) {
                window.__flowNewVideo = true;
            }
        } catch(e) {}
    }, 500);
}"""

_READ_OBSERVER_JS = """() => ({
    progress:  window.__flowProgress  || 0,
    error:     window.__flowError     || '',
    new_video: window.__flowNewVideo  || false,
})"""


async def _inject_observer(page) -> None:
    """Inject a JS observer that tracks progress and new elements."""
    try:
        await page.evaluate(_OBSERVER_JS)
    except Exception as exc:
        logger.warning("Failed to inject observer: %s", exc)


async def _read_observer(page) -> dict:
    """Read the injected observer state."""
    try:
        return await page.evaluate(_READ_OBSERVER_JS)
    except Exception:
        return {"progress": 0, "error": "", "new_video": False}
