"""Stress-test the marketing-landing bypass.

Reuses one warm FlowClient (pool-style), and runs N iterations of:
    goto(homepage) -> dismiss landing if needed -> click "+ New project"
    -> wait for /project/ URL -> go back to homepage.

Records per-iteration outcome + whether the dismiss helper had to run,
whether a reload was triggered, and total elapsed time.

Env:
    STRESS_PROFILE  (default: ngoctuandt20)
    STRESS_N        (default: 100)
    CHROME_USER_DATA_DIR (inherits from .env)
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from contextlib import redirect_stdout

from dotenv import load_dotenv

load_dotenv()

# Make repo-root imports work when run as `python scripts/stress_landing_bypass.py`
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flow.client import FlowClient  # noqa: E402
from flow.landing import dismiss_flow_marketing_landing  # noqa: E402

PROFILE = os.environ.get("STRESS_PROFILE", "ngoctuandt20")
ITERATIONS = int(os.environ.get("STRESS_N", "100"))
HOMEPAGE = "https://labs.google/fx/tools/flow"
TILE_SELECTOR = "text=New project, text=Dự án mới, text=Tạo dự án"
ROLE_NAMES = ("New project", "Dự án mới", "Tạo dự án")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("stress")
# Silence flow internals — keep the output readable.
logging.getLogger("flow.client").setLevel(logging.WARNING)
logging.getLogger("flow.landing").setLevel(logging.WARNING)
logging.getLogger("flow.operations.generate").setLevel(logging.WARNING)


class Counter:
    def __init__(self) -> None:
        self.ok = 0
        self.fail = 0
        self.direct = 0           # tile present within 1s (no dismiss needed)
        self.dismiss_ok = 0       # dismiss helper succeeded
        self.dismiss_reload = 0   # dismiss went through reload path
        self.cta_clicked = 0      # a real Create-with-Flow CTA was clicked
        self.total_s = 0.0


async def _tile_attached(page, timeout_ms: int) -> bool:
    """True if the "+ New project" tile is attached.

    Tries the text-based selector first (faster), then falls back to
    the role-based match — some Flow variants render the tile as an
    icon-only button whose label only exposes via aria-label.
    """
    try:
        await page.wait_for_selector(TILE_SELECTOR, state="attached", timeout=timeout_ms)
        return True
    except Exception:
        pass
    # Role-based fallback — same matcher the production click uses.
    per_name_timeout = max(250, timeout_ms // len(ROLE_NAMES))
    for name in ROLE_NAMES:
        try:
            btn = page.get_by_role("button", name=name).filter(visible=True).first
            if await btn.is_visible(timeout=per_name_timeout):
                return True
        except Exception:
            continue
    return False


async def run_one(page, stats: Counter, n: int) -> None:
    t0 = time.monotonic()
    reload_before = 0
    cta_before = 0

    try:
        await page.goto(HOMEPAGE, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(2)  # Mirror generate.py — page settle before tile check.
    except Exception as exc:
        stats.fail += 1
        stats.total_s += time.monotonic() - t0
        logger.error("[%d] goto FAIL: %s", n, exc)
        return

    direct = await _tile_attached(page, 1000)
    local_counts = {"reload": 0, "cta": 0}
    if direct:
        stats.direct += 1
        route = "direct"
    else:
        class _CountingLogger:
            def info(self, fmt, *args):
                msg = fmt % args if args else fmt
                if "reload retry" in msg:
                    local_counts["reload"] += 1
                if "marketing landing detected" in msg:
                    local_counts["cta"] += 1

            def warning(self, *a, **k):
                pass

            def exception(self, *a, **k):
                pass

            def error(self, *a, **k):
                pass

        async def _ready():
            return await _tile_attached(page, 2000)

        await dismiss_flow_marketing_landing(
            page, _CountingLogger(), _ready,
            per_click_timeout_sec=8.0, max_reloads=2,
        )
        if local_counts["reload"]:
            stats.dismiss_reload += 1
        if local_counts["cta"]:
            stats.cta_clicked += 1

        # Mirror generate.py: always give the tile a final long wait,
        # regardless of dismiss return value.
        if await _tile_attached(page, 10000):
            stats.dismiss_ok += 1
            route = f"dismiss(reload={local_counts['reload']}, cta={local_counts['cta']})"
        else:
            stats.fail += 1
            stats.total_s += time.monotonic() - t0
            # Dump page diagnostics — what did we actually land on?
            try:
                title = await page.title()
                url = page.url
                body_text = await page.evaluate(
                    "() => (document.body && document.body.innerText || '').slice(0, 300)"
                )
            except Exception:
                title, url, body_text = "?", "?", "?"
            logger.error(
                "[%d] TILE MISS (reload=%d, cta=%d)\n  url=%s\n  title=%s\n  body[:300]=%r",
                n, local_counts["reload"], local_counts["cta"], url, title, body_text,
            )
            return

    # Click "+ New project".
    try:
        btn = page.get_by_role("button", name="New project").filter(visible=True).first
        if not await btn.is_visible(timeout=3000):
            raise RuntimeError("New project button not visible after dismiss")
        await btn.click(timeout=10000)
        await page.wait_for_url("**/project/**", timeout=15000)
    except Exception as exc:
        stats.fail += 1
        stats.total_s += time.monotonic() - t0
        logger.error("[%d] click/url FAIL (%s): %s", n, route, exc)
        return

    elapsed = time.monotonic() - t0
    stats.ok += 1
    stats.total_s += elapsed
    logger.info("[%d] OK %s  %.1fs", n, route, elapsed)


async def main() -> int:
    profile_base = os.environ.get("CHROME_USER_DATA_DIR", "./chrome-profiles")
    download_dir = os.environ.get("FLOW_DOWNLOAD_DIR", "./downloads")

    client = FlowClient(
        profile_name=PROFILE,
        profile_base_dir=profile_base,
        download_dir=download_dir,
    )

    logger.info("=== Stress landing-bypass: profile=%s N=%d ===", PROFILE, ITERATIONS)
    await client.start()
    stats = Counter()

    try:
        for i in range(1, ITERATIONS + 1):
            await run_one(client.page, stats, i)
            # Light breathing room so we don't hammer Google.
            await asyncio.sleep(0.5)
    finally:
        await client.stop()

    logger.info("=" * 60)
    logger.info("TOTAL:        %d", ITERATIONS)
    logger.info("OK:           %d   (%.1f%%)", stats.ok, stats.ok * 100 / max(1, ITERATIONS))
    logger.info("FAIL:         %d", stats.fail)
    logger.info("direct hit:   %d   (no dismiss needed)", stats.direct)
    logger.info("dismiss ok:   %d", stats.dismiss_ok)
    logger.info("  - reload fired:  %d", stats.dismiss_reload)
    logger.info("  - CTA clicked:   %d", stats.cta_clicked)
    logger.info("avg duration: %.1fs", stats.total_s / max(1, ITERATIONS))
    return 0 if stats.fail == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
