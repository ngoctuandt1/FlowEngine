"""Scrape Flow's homepage DOM + computed styles for 1:1 design clone.

Connects to a real Chrome with the warmed ngoctuandt20 profile, navigates
to https://labs.google/fx/tools/flow, then dumps:

- HTML tree (top-bar + grid + tile sample)
- computed styles for the grid, a tile, the new-project pill, the
  top-bar, the page background — everything we need to copy exact
  numbers (radius, gap, colors, font-sizes, paddings) instead of
  guessing.

Output: flow_design_refs/flow_dom_scrape.json + flow_dom.html.
"""
from __future__ import annotations

import asyncio
import json
import os
import socket
import sys
from pathlib import Path


def _pick_free_port() -> int:
    """Bind to port 0 to get a kernel-assigned free port. Avoids the
    hard-coded 60042 collision risk Codex flagged."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]

# Resolve the repo root (scripts/..) and add it to sys.path so the
# `scripts.warm_profile` import works regardless of where this script
# is invoked from.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.warm_profile import _launch_real_chrome  # noqa: E402

# warm_profile builds a Chrome profile path relative to CWD via
# `Path("chrome-profiles")`. Worktrees often have an empty auto-mkdir'd
# `chrome-profiles/{NAME}/Default/` from earlier runs; the *master*
# tree has the real warmed cookies. Walk ancestors and pick the first
# that actually contains a non-zero Cookies file.
PROFILE_NAME = os.environ.get("FLOW_PROFILE_NAME", "ngoctuandt20")
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

_master_candidate = ROOT
for _candidate in (ROOT, *ROOT.parents):
    if _has_warm_cookies(_candidate):
        _master_candidate = _candidate
        break
os.chdir(_master_candidate)

OUT_DIR = ROOT / "docs" / "design_refs"
OUT_DIR.mkdir(parents=True, exist_ok=True)
CDP_PORT = int(os.environ.get("FLOW_SCRAPE_CDP_PORT", "0")) or _pick_free_port()


# CSS properties we care about for the clone.
PROPS = [
    "background-color", "background-image", "color",
    "font-family", "font-size", "font-weight", "letter-spacing",
    "line-height",
    "padding", "padding-top", "padding-right", "padding-bottom", "padding-left",
    "margin", "margin-top", "margin-right", "margin-bottom", "margin-left",
    "border", "border-radius", "border-color", "border-width", "border-style",
    "width", "height", "max-width", "min-width",
    "display", "grid-template-columns", "grid-template-rows", "gap",
    "flex-direction", "justify-content", "align-items",
    "box-shadow", "backdrop-filter", "opacity",
    "position", "top", "right", "bottom", "left",
    "z-index", "overflow",
]


async def main() -> None:
    from playwright.async_api import async_playwright

    # CWD was set above to the tree that owns the warmed profile, so
    # warm_profile's relative `Path("chrome-profiles") / NAME` resolves
    # to the real cookies dir. Use a free port (parametrised) so two
    # scrape runs don't fight.
    profile_dir = (Path.cwd() / "chrome-profiles" / PROFILE_NAME).resolve()
    cdp_port = CDP_PORT
    chrome = _launch_real_chrome(profile_dir, cdp_port)

    try:
        async with async_playwright() as pw:
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
            await page.set_viewport_size({"width": 1440, "height": 900})

            await page.goto("https://labs.google/fx/tools/flow",
                            wait_until="networkidle", timeout=60000)
            await asyncio.sleep(4)

            # Save full HTML for offline grep.
            html = await page.content()
            (OUT_DIR / "flow_dom.html").write_text(html, encoding="utf-8")
            print(f"saved DOM: {OUT_DIR / 'flow_dom.html'} ({len(html)} bytes)")

            # Run a single eval that walks key landmarks and pulls
            # computed styles for each. Uses heuristic selectors since
            # Flow's class names are scrambled.
            scrape = await page.evaluate(f"""
                () => {{
                  const props = {json.dumps(PROPS)};
                  const grab = (el) => {{
                    if (!el) return null;
                    const s = getComputedStyle(el);
                    const out = {{ tag: el.tagName, classes: el.className, id: el.id, text: (el.innerText || '').slice(0, 80) }};
                    for (const p of props) out[p] = s.getPropertyValue(p);
                    out.bbox = el.getBoundingClientRect();
                    return out;
                  }};

                  // Heuristics — tweak if Flow shifts class shapes.
                  const findByText = (sel, text) => Array.from(document.querySelectorAll(sel))
                    .find((e) => (e.innerText || '').trim() === text);

                  // Topbar — Flow's signed-in homepage doesn't use
                  // <header>/[role=banner], so fall back to the
                  // brand-text element ("Flow"), then walk up to the
                  // first ancestor that's display:flex justify
                  // space-between (the actual app bar).
                  let topbar = null;
                  const brand = findByText('span, h1, div, a', 'Flow');
                  for (let el = brand; el; el = el.parentElement) {{
                    const cs = getComputedStyle(el);
                    if (cs.display === 'flex' && cs.justifyContent === 'space-between' && el.getBoundingClientRect().width > 800) {{
                      topbar = el;
                      break;
                    }}
                  }}
                  if (!topbar) topbar = brand;

                  // Real Flow tile structure (verified):
                  //   <div class="sc-c371c8f2-2 jqtfJg">  <- row wrapper
                  //     <div class="sc-7153f67b-0 ..."> <- tile (3 of these per row)
                  //       <a href="/project/{id}"><img class="sc-7153f67b-2 ..."></a>
                  //       <div class="sc-7153f67b-3 ...">  <- meta overlay (date + edit + delete)
                  //         <span class="sc-7153f67b-5 ..."><text/></span>
                  //         <button>delete</button>
                  //       </div>
                  //     </div>
                  //   </div>
                  const virtList = document.querySelector('[data-testid=\"virtuoso-item-list\"]');
                  const row = document.querySelector('.sc-c371c8f2-2');
                  const tileWrap = document.querySelector('.sc-7153f67b-0');
                  const tileLink = document.querySelector('.sc-7153f67b-0 a[href*=\"/project/\"]');
                  const tileImg = document.querySelector('.sc-7153f67b-2');
                  const tileMeta = document.querySelector('.sc-7153f67b-3');
                  const tileDate = document.querySelector('.sc-7153f67b-5');
                  const tilesAll = Array.from(document.querySelectorAll('.sc-7153f67b-0'));
                  const grid = row;
                  const tile = tileWrap;
                  const tileVideo = tileImg; // re-purposed slot for the IMG element
                  const tileOverlay = tileMeta;
                  const tileChildren = tilesAll.slice(0, 12).map((t) => ({{
                    rect: t.getBoundingClientRect(),
                  }}));

                  // New project CTA — Flow's signed-in homepage shows
                  // a "+ New project" pill that often only appears on
                  // hover or in an empty-state slot. Restrict to leaf
                  // nodes (no descendants matching) so we don't pick up
                  // the whole document.
                  const newProj = Array.from(document.querySelectorAll('button, a, span, div'))
                    .find((e) => {{
                      const t = (e.innerText || '').trim().toLowerCase();
                      if (!t.startsWith('new project') && t !== '+ new project') return false;
                      // Skip if this element has descendants that also match.
                      return !Array.from(e.querySelectorAll('button,a,span,div'))
                        .some((c) => /^(\\+\\s*)?new project/i.test((c.innerText||'').trim()));
                    }}) || null;

                  // ULTRA badge
                  const ultra = findByText('span, div, button', 'ULTRA') ||
                                findByText('span, div, button', 'Ultra');

                  return {{
                    body: grab(document.body),
                    topbar: grab(topbar),
                    virtList: grab(virtList),
                    row: grab(row),
                    grid: grab(grid),
                    tile: grab(tile),
                    tileImg: grab(tileImg),
                    tileMeta: grab(tileMeta),
                    tileDate: grab(tileDate),
                    tileLink: grab(tileLink),
                    tilesPerRow: tilesAll.length,
                    tileRects: tileChildren,
                    newProj: grab(newProj),
                    ultra: grab(ultra),
                    viewport: {{ w: innerWidth, h: innerHeight }},
                  }};
                }}
            """)

            (OUT_DIR / "flow_dom_scrape.json").write_text(
                json.dumps(scrape, indent=2, default=str), encoding="utf-8")
            print(f"saved scrape: {OUT_DIR / 'flow_dom_scrape.json'}")

            # also a focused HD screenshot for visual reference
            await page.set_viewport_size({"width": 1440, "height": 900})
            await page.screenshot(path=str(OUT_DIR / "flow_homepage_hd.png"),
                                  full_page=False)
            print(f"saved HD screenshot")

            await browser.close()
    finally:
        try:
            chrome.terminate()
        except Exception:
            pass


if __name__ == "__main__":
    asyncio.run(main())
