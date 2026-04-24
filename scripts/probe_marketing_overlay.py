"""One-shot DOM probe to identify what intercepts pointer events on the
Flow marketing landing. Runs headed, dumps the topmost element at the
CTA's location + any modal/consent-banner-ish nodes, then exits.
"""

from __future__ import annotations

import asyncio
import json

from flow.client import FlowClient


async def main() -> None:
    client = FlowClient(profile_name="ngoctuandt20")
    await client.start()
    try:
        page = client.page
        await page.goto(
            "https://labs.google/fx/tools/flow",
            wait_until="domcontentloaded",
            timeout=30000,
        )
        await asyncio.sleep(3)

        info = await page.evaluate(
            """() => {
                const out = {url: location.href, title: document.title};
                const btns = [...document.querySelectorAll(
                    'button, [role="button"], a'
                )].filter(el => /create with flow/i.test(el.textContent || ''));
                out.cta_count = btns.length;
                out.ctas = btns.slice(0, 6).map(el => {
                    const r = el.getBoundingClientRect();
                    return {
                        tag: el.tagName,
                        href: el.getAttribute('href'),
                        text: (el.textContent || '').trim().slice(0, 80),
                        visible: r.width > 0 && r.height > 0,
                        inMain: !!el.closest('main'),
                        rect: {x: r.x, y: r.y, w: r.width, h: r.height},
                    };
                });
                // topmost element at the hero CTA's centre
                const hero = btns.find(el => {
                    const r = el.getBoundingClientRect();
                    return r.width > 80 && r.height > 30 && r.top < innerHeight;
                });
                if (hero) {
                    const r = hero.getBoundingClientRect();
                    const cx = r.x + r.width/2, cy = r.y + r.height/2;
                    const top = document.elementFromPoint(cx, cy);
                    out.topmost_at_hero = top ? {
                        tag: top.tagName,
                        id: top.id,
                        className: (top.className || '').toString().slice(0,120),
                        isHero: top === hero || hero.contains(top),
                    } : null;
                }
                // scan for suspicious overlays (fixed/absolute full-page)
                const overlays = [...document.querySelectorAll('div, section, aside')]
                    .filter(el => {
                        const cs = getComputedStyle(el);
                        if (cs.position !== 'fixed' && cs.position !== 'absolute') return false;
                        const r = el.getBoundingClientRect();
                        return r.width >= innerWidth * 0.8 && r.height >= innerHeight * 0.4;
                    })
                    .slice(0, 8)
                    .map(el => ({
                        tag: el.tagName,
                        id: el.id,
                        className: (el.className || '').toString().slice(0,120),
                        role: el.getAttribute('role'),
                        ariaLabel: el.getAttribute('aria-label'),
                        zIndex: getComputedStyle(el).zIndex,
                        position: getComputedStyle(el).position,
                        pointerEvents: getComputedStyle(el).pointerEvents,
                        textHead: (el.textContent || '').trim().slice(0, 100),
                    }));
                out.overlays = overlays;
                // Scan for ALL clickable elements with CTA-ish text — not just
                // "Create with Flow". We need the button that enters the app.
                const allCtas = [...document.querySelectorAll(
                    'button, a, [role="button"]'
                )]
                    .filter(el => {
                        const t = (el.textContent || '').trim();
                        if (!t || t.length > 40) return false;
                        return /flow|sign in|log in|launch|start|try|get started|go to|enter|app|dashboard/i.test(t);
                    })
                    .slice(0, 20)
                    .map(el => {
                        const r = el.getBoundingClientRect();
                        return {
                            tag: el.tagName,
                            text: (el.textContent || '').trim().slice(0, 50),
                            href: el.getAttribute('href'),
                            onclick: el.getAttribute('onclick') ? 'yes' : null,
                            visible: r.width > 0 && r.height > 0,
                            yPercent: Math.round((r.y / innerHeight) * 100),
                        };
                    });
                out.all_ctas = allCtas;
                return out;
            }"""
        )
        print(json.dumps(info, indent=2))

        png_path = "debug_screens/marketing_probe.png"
        await page.screenshot(path=png_path, full_page=False)
        print(f"\nScreenshot saved: {png_path}")
    finally:
        await client.stop()


if __name__ == "__main__":
    asyncio.run(main())
