"""Attach to a profile + edit URL, dump every button candidate for Extend.

Usage:
    python scripts/inspect_extend_buttons.py <profile> <edit_url>
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.async_api import async_playwright


CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"


def _port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def main(profile: str, edit_url: str) -> int:
    base = Path(os.environ.get("CHROME_USER_DATA_DIR", "chrome-profiles")).resolve()
    port = _port()
    proc = subprocess.Popen([
        CHROME,
        f"--user-data-dir={base / profile}",
        f"--remote-debugging-port={port}",
        "--no-first-run",
        "--no-default-browser-check",
        edit_url,
    ])
    try:
        async with async_playwright() as p:
            browser = None
            for _ in range(20):
                try:
                    browser = await p.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
                    break
                except Exception:
                    await asyncio.sleep(0.5)
            if browser is None:
                print("ERR: CDP fail", file=sys.stderr)
                return 2
            ctx = browser.contexts[0]
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()
            # Wait for either project or edit URL to settle
            for _ in range(40):
                if "/project/" in page.url or "/edit/" in page.url:
                    break
                await asyncio.sleep(0.5)
            print(f"URL_after_goto={page.url}")
            await asyncio.sleep(6)

            # If we bounced to project root, find the first media tile and
            # click it — Flow's SPA router only enters edit view via tile
            # click (memory: feedback_flow_edit_nav_click.md).
            if "/edit/" not in page.url:
                print("bounced to project root — clicking first tile")
                clicked = await page.evaluate(
                    """() => {
                        const tiles = document.querySelectorAll('a[href*="/edit/"], button[aria-label*="play" i], video');
                        for (const t of tiles) {
                            const r = t.getBoundingClientRect();
                            if (r.width >= 100 && r.height >= 60) {
                                t.click();
                                return {tag: t.tagName, w: Math.round(r.width)};
                            }
                        }
                        // Fallback: click parent of first <video>
                        const v = document.querySelector('video');
                        if (v) {
                            let p = v;
                            for (let i = 0; i < 5 && p; i++) {
                                p = p.parentElement;
                                if (p && p.tagName === 'BUTTON') { p.click(); return {tag: 'button-via-video'}; }
                            }
                            v.click();
                            return {tag: 'video-direct'};
                        }
                        return null;
                    }"""
                )
                print(f"tile_click={clicked}")
                for _ in range(30):
                    if "/edit/" in page.url:
                        break
                    await asyncio.sleep(0.5)
                print(f"URL_after_click={page.url}")
                await asyncio.sleep(6)

            shot = Path("docs/livetest-2026-05-21/extend_inspect.png")
            shot.parent.mkdir(parents=True, exist_ok=True)
            await page.screenshot(path=str(shot), full_page=True, timeout=15000)
            print(f"shot={shot}")

            # Dump ALL buttons with full attribute fingerprint
            buttons = await page.evaluate("""() => {
                const out = [];
                const els = document.querySelectorAll('button, [role="button"]');
                els.forEach(el => {
                    const r = el.getBoundingClientRect();
                    if (r.width <= 0 || r.height <= 0) return;
                    const style = getComputedStyle(el);
                    if (style.display === 'none' || style.visibility === 'hidden') return;
                    const icons = [];
                    el.querySelectorAll('i, span').forEach(child => {
                        const t = (child.innerText || '').trim();
                        if (t && t.length < 30 && !/\\s/.test(t)) icons.push({tag: child.tagName, text: t, cls: child.className.toString().slice(0,80)});
                    });
                    out.push({
                        text: (el.innerText || '').trim().slice(0, 80),
                        aria: el.getAttribute('aria-label') || '',
                        title: el.getAttribute('title') || '',
                        dataAttrs: Array.from(el.attributes).filter(a => a.name.startsWith('data-')).map(a => a.name + '=' + a.value.slice(0,40)),
                        cls: (el.className || '').toString().slice(0,120),
                        tag: el.tagName,
                        rect: {x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height)},
                        icons,
                    });
                });
                return out;
            }""")
            # Probe BEFORE click: editor placeholders
            pre_editors = await page.evaluate("""() => {
                return Array.from(document.querySelectorAll('[data-slate-editor="true"]')).map((ed, i) => ({
                    i, placeholder: ed.getAttribute('data-placeholder') || '',
                    inner: (ed.innerText || '').slice(0, 100),
                    parentText: (ed.parentElement?.innerText || '').slice(0, 200),
                }));
            }""")
            print(f"editors_BEFORE_extend_click={json.dumps(pre_editors, ensure_ascii=False)}")

            print(f"button_count={len(buttons)}")
            # Filter to small/icon buttons + ones with 'add' or 'extend' or 'create'
            interesting = []
            for b in buttons:
                txt = (b['text'] + ' ' + b['aria'] + ' ' + b['title']).lower()
                if (any(k in txt for k in ['extend', 'add_', 'create', 'keyboard_double']) or
                    any(k in (i['text'] or '').lower() for i in b['icons'] for k in ['extend', 'add', 'keyboard_double'])):
                    interesting.append(b)
            print(f"interesting={len(interesting)}")
            for b in interesting:
                print(json.dumps(b, ensure_ascii=False))

            # Now click the add_2 candidate (the Extend trigger), wait,
            # dump editor + new visible buttons to confirm the panel
            # signal we need to verify after click.
            clicked = await page.evaluate("""() => {
                for (const el of document.querySelectorAll('button, [role="button"]')) {
                    const text = (el.innerText || '').toLowerCase();
                    if (text.includes('arrow_forward')) continue;
                    if (text.includes('add_2')) {
                        const r = el.getBoundingClientRect();
                        el.click();
                        return {ok: true, rect: {x: Math.round(r.x), y: Math.round(r.y)}};
                    }
                }
                return {ok: false};
            }""")
            print(f"add_2_click={clicked}")
            await asyncio.sleep(3)
            post = await page.evaluate("""() => {
                const eds = Array.from(document.querySelectorAll('[data-slate-editor="true"]')).map((ed, i) => ({
                    i, placeholder: ed.getAttribute('data-placeholder') || '',
                    inner: (ed.innerText || '').slice(0, 200),
                    parentText: (ed.parentElement?.innerText || '').slice(0, 400),
                }));
                // also dump any role=dialog / aria-modal
                const dialogs = Array.from(document.querySelectorAll('[role="dialog"], [aria-modal="true"], [data-radix-popper-content-wrapper]')).map(d => ({
                    role: d.getAttribute('role') || '',
                    text: (d.innerText || '').slice(0, 200),
                }));
                return {editors: eds, dialogs};
            }""")
            print(f"editors_AFTER_add_2={json.dumps(post['editors'], ensure_ascii=False)}")
            print(f"dialogs_AFTER_add_2={json.dumps(post['dialogs'], ensure_ascii=False)}")
            shot2 = Path("docs/livetest-2026-05-21/extend_inspect_after_click.png")
            await page.screenshot(path=str(shot2), full_page=True, timeout=15000)
            print(f"shot_after={shot2}")

            await browser.close()
    finally:
        try:
            proc.terminate(); proc.wait(timeout=10)
        except Exception:
            proc.kill()
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("usage: inspect_extend_buttons.py <profile> <edit_url>", file=sys.stderr)
        sys.exit(64)
    sys.exit(asyncio.run(main(sys.argv[1], sys.argv[2])))
