"""Probe v2: test direct page.goto(edit_url) on EN-configured profile — better diagnostics.

v1 FAIL verdict was false positive (text match on '[...catchAll]' too lax;
waited only 6s for SPA render).

v2 checks real editor DOM signals:
- canvas >= 300 wide (preview canvas)
- button with submit semantics (arrow_forward icon)
- textarea count
- Veo chip visibility
- URL preservation
"""
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from flow.client import FlowClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

PROFILE = "ngoctuandt20"
PROFILE_BASE = os.environ.get("CHROME_USER_DATA_DIR", "D:/AI/chrome-profiles")
EDIT_URL = (
    "https://labs.google/fx/tools/flow/project/"
    "dbb990c0-7d75-41f4-b7c9-21870bf3b190/edit/"
    "e219fc6c-ee61-4a42-a1b7-731e9f95ae53"
)
HOLD_SECS = 12


async def main() -> int:
    async with FlowClient(PROFILE, profile_base_dir=PROFILE_BASE, debug_port=19402) as client:
        page = client.page
        print(f"[probe] direct goto -> {EDIT_URL}")
        await page.goto(EDIT_URL, wait_until="domcontentloaded", timeout=30000)
        # Wait longer for SPA to render the editor
        for sec in (3, 6, 10):
            await asyncio.sleep(sec - (sec - 3 if sec == 3 else (sec - 6 if sec == 6 else (sec - 10))))
        # Simpler: just wait 12s total with progress logs
        await asyncio.sleep(12)

        final_url = page.url
        print(f"[probe] landed URL: {final_url}")
        is_vi = "/fx/vi/" in final_url
        still_has_edit = "/edit/" in final_url
        print(f"[probe] VI-locale redirect: {is_vi}")
        print(f"[probe] /edit/ preserved:  {still_has_edit}")

        # Editor-presence signals via JS (not text-match on full HTML)
        signals = await page.evaluate(
            """() => {
                const canvases = [...document.querySelectorAll('canvas')];
                const big = canvases.filter(c => {
                    const r = c.getBoundingClientRect();
                    return r.width >= 300 && r.height >= 200;
                });
                const submitBtn = document.querySelector("button:has(i)") ?
                    [...document.querySelectorAll('button i')].some(i => i.textContent.trim() === 'arrow_forward') : false;
                const textareas = document.querySelectorAll('textarea').length;
                const veoChip = [...document.querySelectorAll('button')].some(b =>
                    /Veo/.test(b.innerText || ''));
                const addIconBtn = [...document.querySelectorAll('button i')].some(i =>
                    i.textContent.trim() === 'add_2');
                // Next.js catch-all actually renders a specific h1; distinguish from string-in-bundle
                const h1Text = document.querySelector('h1')?.innerText?.trim() || '';
                return {
                    canvas_big_count: big.length,
                    canvas_sizes: canvases.map(c => {
                        const r = c.getBoundingClientRect();
                        return { w: Math.round(r.width), h: Math.round(r.height) };
                    }),
                    submit_arrow_forward: submitBtn,
                    textareas,
                    veo_chip_visible: veoChip,
                    homepage_add_icon: addIconBtn,
                    h1: h1Text,
                };
            }"""
        )
        print(f"[probe] editor signals: {signals}")

        on_editor = (
            still_has_edit
            and not is_vi
            and signals.get("canvas_big_count", 0) >= 1
            and signals.get("submit_arrow_forward", False)
        )
        on_homepage = signals.get("homepage_add_icon", False)

        if on_editor:
            verdict = "PASS: direct goto lands on editor (canvas + submit chip both present)"
        elif on_homepage:
            verdict = "FAIL: direct goto bounced to homepage"
        else:
            verdict = "AMBIGUOUS: /edit/ URL preserved but no editor DOM signals"
        print(f"[probe] verdict: {verdict}")
        await asyncio.sleep(HOLD_SECS)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
