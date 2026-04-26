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
import sys
from pathlib import Path

ROOT = Path(__file__).parent / ".claude/worktrees/phase-web-ui"
sys.path.insert(0, str(ROOT))

from scripts.warm_profile import _launch_real_chrome  # noqa: E402

OUT_DIR = Path(__file__).parent / "flow_design_refs"
OUT_DIR.mkdir(exist_ok=True)


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

    profile_dir = (Path(__file__).parent / "chrome-profiles" / "ngoctuandt20").resolve()
    cdp_port = 60043
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

                  // Topbar = first <header> or [role=banner] near top
                  const topbar = document.querySelector('header, [role=banner]') ||
                                 document.querySelector('body > div > div:first-child');

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

                  // New project CTA — look for "New project" / "+ New project" text
                  const newProj = findByText('button, a, div, span', 'New project') ||
                                  Array.from(document.querySelectorAll('*')).find((e) => /new project/i.test(e.innerText) && e.children.length < 5);

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
