"""Wait for Flow generation to complete.

Polls three independent signal sources (reverse-API, network video
captures, DOM observer) to detect when a generation finishes or fails.
"""

import asyncio
import logging
import time

from flow.recaptcha import detect_recaptcha, detect_recaptcha_in_network, RecaptchaError

logger = logging.getLogger(__name__)

# Default timeouts per job type (seconds)
TIMEOUTS: dict[str, int] = {
    "text-to-video": 900,   # 15 min (LP can be slow)
    "extend-video": 600,    # 10 min
    "insert-object": 300,   # 5 min
    "remove-object": 300,
    "camera-move": 300,
}

# Abort if zero progress signals for this many seconds (per job type)
NO_SIGNAL_TIMEOUTS: dict[str, int] = {
    "text-to-video": 300,   # 5 min — video gen can stall at certain %
    "extend-video": 300,    # 5 min
    "insert-object": 180,   # 3 min
    "remove-object": 180,
    "camera-move": 180,
}
NO_SIGNAL_TIMEOUT_DEFAULT = 180


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

    no_signal_timeout = NO_SIGNAL_TIMEOUTS.get(job_type, NO_SIGNAL_TIMEOUT_DEFAULT)
    page = client.page

    await _inject_observer(page)

    initial_video_count = len(getattr(client, "_video_urls", []))
    initial_media_count = len(getattr(client, "_media_id_events", []))
    initial_url = page.url
    start = time.monotonic()
    last_progress = 0
    last_signal_time = start
    last_recaptcha_check = 0.0  # epoch-relative; check immediately on first pass

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
            await _settle_after_done(
                page,
                client,
                initial_url=initial_url,
                initial_media_count=initial_media_count,
            )
            return _result(
                True,
                media_ids=_collect_media_ids(client, start_index=initial_media_count),
            )
        if api["error"]:
            logger.error("API error: %s", api["error"])
            return _result(False, error=api["error"])
        if api["progress"] > last_progress:
            last_progress = api["progress"]
            last_signal_time = time.monotonic()

        # --- reCAPTCHA check (throttled to every ~10s) ---
        now = time.monotonic()
        if now - last_recaptcha_check >= 10:
            last_recaptcha_check = now
            if await detect_recaptcha(page) or await detect_recaptcha_in_network(client):
                raise RecaptchaError("reCAPTCHA detected during generation wait")

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
            snippet = dom.get("snippet") or "(no snippet)"
            logger.error("DOM error: %s | match: %s", dom["error"], snippet[:240])
            # Dump a screenshot + HTML for post-mortem diagnosis.
            try:
                from pathlib import Path
                Path("logs").mkdir(exist_ok=True)
                ts = int(time.time())
                shot = Path("logs") / f"error_{dom['error']}_{ts}.png"
                html = Path("logs") / f"error_{dom['error']}_{ts}.html"
                await page.screenshot(path=str(shot), full_page=True)
                html.write_text(await page.content(), encoding="utf-8")
                logger.error("DOM error artifacts: %s + %s", shot, html)
            except Exception as dump_exc:  # pragma: no cover
                logger.warning("error-dump failed: %s", dump_exc)
            return _result(False, error=dom["error"])
        # New <video> element detected at any progress level = done
        if dom["new_video"]:
            logger.info(
                "Completion via DOM (new video at %d%%) after %.0fs",
                dom["progress"], elapsed,
            )
            await asyncio.sleep(2)  # let media settle
            return _result(
                True,
                media_ids=_collect_media_ids(client),
                video_urls=video_urls[initial_video_count:],
            )

        # Progress stalled for a while — check for new media cards as
        # fallback.  Video gen may complete without DOM % reaching 100
        # (e.g. progress stops at 69% then video renders directly).
        silence_so_far = time.monotonic() - last_signal_time
        if silence_so_far > 30 and last_progress > 0:
            new_cards = await _check_new_media_cards(page)
            if new_cards:
                logger.info(
                    "Completion via DOM (stalled at %d%% & new media card) after %.0fs",
                    last_progress, elapsed,
                )
                await asyncio.sleep(3)
                return _result(
                    True,
                    media_ids=_collect_media_ids(client),
                )

        # --- No-signal watchdog ---
        silence = time.monotonic() - last_signal_time
        if silence > no_signal_timeout:
            # Debug: log what signals were captured before aborting
            n_calls = len(getattr(client, "_calls", []))
            n_videos = len(getattr(client, "_video_urls", []))
            n_media = len(getattr(client, "_media_id_events", []))
            logger.error(
                "No signal for %ds, aborting  "
                "(api_calls=%d, video_urls=%d, media_ids=%d, dom_progress=%d%%)",
                no_signal_timeout, n_calls, n_videos, n_media, last_progress,
            )
            return _result(False, error="no_signal_timeout")
        # Debug: log signal counts periodically when stalled
        if silence > 60 and int(elapsed) % 60 == 0 and elapsed > 0:
            n_calls = len(getattr(client, "_calls", []))
            n_videos = len(getattr(client, "_video_urls", []))
            logger.info(
                "Stalled debug: %.0fs elapsed, %ds silent, "
                "api_calls=%d, video_urls=%d, dom=%s",
                elapsed, int(silence), n_calls, n_videos, dom,
            )

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


def _collect_media_ids(client, start_index: int = 0) -> list[str]:
    """Collect all known media IDs from client state."""
    ids: set[str] = set()
    for evt in getattr(client, "_media_id_events", [])[start_index:]:
        mid = evt.get("mid") or evt.get("media_id")
        if mid:
            ids.add(mid)
    return list(ids)


async def _settle_after_done(page, client, initial_url: str, initial_media_count: int) -> None:
    """Allow the editor route or media captures to settle after API-done."""
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        if page.url != initial_url:
            return
        if len(getattr(client, "_media_id_events", [])) > initial_media_count:
            return
        await asyncio.sleep(0.25)


# ------------------------------------------------------------------
# DOM observer (injected JS)
# ------------------------------------------------------------------

async def _check_new_media_cards(page) -> bool:
    """Check if new media cards appeared (video or image tiles)."""
    try:
        return await page.evaluate("""() => {
            const videos = document.querySelectorAll('video');
            const tiles = document.querySelectorAll('[data-tile-id]');
            const imgs = document.querySelectorAll(
                'img[src*="googleusercontent"], img[src*="ggpht"]'
            );
            // Observer baseline was set when injection ran
            const baseV = window.__flowVideoCount || 0;
            return videos.length > baseV || tiles.length > 0 || imgs.length > baseV;
        }""")
    except Exception:
        return False


_OBSERVER_JS = """() => {
    if (window.__flowObserverActive) return;
    window.__flowObserverActive = true;
    window.__flowProgress       = 0;
    window.__flowError          = '';
    window.__flowErrorSnippet   = '';
    window.__flowNewVideo       = false;
    window.__flowVideoCount     = document.querySelectorAll('video').length;

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

            // Detect error states. Capture ±80 chars around the match
            // so the Python side can log what actually triggered.
            const captureSnippet = (label, re) => {
                const m = body.match(re);
                if (m) {
                    window.__flowError = label;
                    const idx = m.index ?? body.search(re);
                    const start = Math.max(0, idx - 80);
                    const end = Math.min(body.length, idx + m[0].length + 80);
                    window.__flowErrorSnippet = body.slice(start, end).replace(/\\s+/g, ' ').trim();
                }
            };
            if (/all.*failed|generation.*failed/i.test(body)) {
                captureSnippet('ALL_FAILED', /.{0,80}(all.*failed|generation.*failed).{0,80}/i);
            }
            if (/no.*credits/i.test(body) && /insufficient/i.test(body)) {
                captureSnippet('NO_CREDITS', /.{0,80}(no.*credits|insufficient).{0,80}/i);
            }
            // Tightened: match only real content-policy error phrasings, not
            // footer links like "Privacy Policy". Requires "content" or
            // "violat" near "policy" — eliminates false positives on Flow's
            // persistent legal footer.
            if (/content\\s+policy|policy\\s+(violation|violated)|violat(es|ed|ing)\\s+.{0,30}\\s*polic/i.test(body)) {
                captureSnippet('POLICY', /.{0,80}(content\\s+policy|policy\\s+(violation|violated)|violat(es|ed|ing)\\s+.{0,30}\\s*polic).{0,80}/i);
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
    progress:  window.__flowProgress    || 0,
    error:     window.__flowError       || '',
    snippet:   window.__flowErrorSnippet|| '',
    new_video: window.__flowNewVideo    || false,
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
