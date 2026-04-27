"""Quick one-off: capture Flow homepage + composer state for design reference.

Reuses warm_profile.py's CDP launcher (real Chrome with the warmed
ngoctuandt20 profile) so we land on Flow signed-in, not the OAuth wall.
"""
from __future__ import annotations

import asyncio
import os
import socket
import sys
from pathlib import Path


def _pick_free_port() -> int:
    """Bind to port 0 → kernel picks a free one. Avoids 60042 collision."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# Repo root = scripts/.. — add to sys.path so `from scripts.warm_profile`
# resolves regardless of caller CWD. Earlier the path was
# `Path(__file__).parent / ".claude/worktrees/phase-web-ui"` which only
# worked when the script lived at the repo root.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PROFILE_NAME = os.environ.get("FLOW_PROFILE_NAME", "ngoctuandt20")
# Chdir to the ancestor whose chrome-profiles dir actually has warmed
# cookies (worktrees often have empty auto-mkdir'd profile shells that
# would launch Chrome onto an OAuth wall).
def _has_warm_cookies(base: Path) -> bool:
    for cand in (
        base / "chrome-profiles" / PROFILE_NAME / "Default" / "Network" / "Cookies",
        base / "chrome-profiles" / PROFILE_NAME / "Default" / "Cookies",
    ):
        try:
            if cand.is_file() and cand.stat().st_size > 0:
                return True
        except OSError:
            pass
    return False

for _candidate in (ROOT, *ROOT.parents):
    if _has_warm_cookies(_candidate):
        os.chdir(_candidate)
        break

from scripts.warm_profile import _launch_real_chrome  # noqa: E402

OUT_DIR = ROOT / "docs" / "design_refs"
OUT_DIR.mkdir(parents=True, exist_ok=True)
CDP_PORT = int(os.environ.get("FLOW_SCREENSHOT_CDP_PORT", "0")) or _pick_free_port()


async def main() -> None:
    from playwright.async_api import async_playwright

    profile_dir = (Path.cwd() / "chrome-profiles" / PROFILE_NAME).resolve()
    cdp_port = CDP_PORT
    chrome = _launch_real_chrome(profile_dir, cdp_port)

    try:
        async with async_playwright() as pw:
            # Wait for CDP up
            for _ in range(30):
                try:
                    browser = await pw.chromium.connect_over_cdp(f"http://localhost:{cdp_port}")
                    break
                except Exception:
                    await asyncio.sleep(0.5)
            else:
                raise RuntimeError("CDP never came up")

            ctx = browser.contexts[0]
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()

            await page.goto("https://labs.google/fx/tools/flow",
                            wait_until="networkidle", timeout=60000)
            await asyncio.sleep(3)
            await page.screenshot(path=str(OUT_DIR / "flow_homepage.png"),
                                  full_page=False)
            print(f"saved: {OUT_DIR / 'flow_homepage.png'}")

            await page.screenshot(path=str(OUT_DIR / "flow_homepage_full.png"),
                                  full_page=True)
            print(f"saved: {OUT_DIR / 'flow_homepage_full.png'}")

            # also dump the composer area
            try:
                await page.evaluate("window.scrollTo(0, 0)")
                composer = page.locator(
                    "[data-slate-editor='true'], textarea, "
                    "[contenteditable='true']"
                ).first
                bb = await composer.bounding_box()
                if bb:
                    await page.screenshot(
                        path=str(OUT_DIR / "flow_composer.png"),
                        clip={
                            "x": max(0, bb["x"] - 120),
                            "y": max(0, bb["y"] - 60),
                            "width": min(1280, bb["width"] + 240),
                            "height": min(360, bb["height"] + 120),
                        },
                    )
                    print(f"saved composer crop")
            except Exception as e:
                print(f"composer crop skipped: {e}")

            await browser.close()
    finally:
        try:
            chrome.terminate()
        except Exception:
            pass


if __name__ == "__main__":
    asyncio.run(main())
