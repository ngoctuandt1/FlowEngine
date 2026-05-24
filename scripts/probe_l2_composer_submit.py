"""Probe Flow unified L2 composer submit network request.

Usage:
    python scripts/probe_l2_composer_submit.py <profile> <edit_url> [prompt]

The probe enters edit view via tile click when Flow bounces /edit/<media_id> to
/project/<id>, types into the Slate composer, clicks arrow_forward Create, and
captures matching video POST bodies. Matching requests are aborted so this probe
does not intentionally spend credits.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from playwright.async_api import Route, async_playwright


CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
ARTIFACT_DIR = Path("docs/livetest-2026-05-21")


def _port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def _wait_for_cdp(playwright: Any, port: int):
    for _ in range(30):
        try:
            return await playwright.chromium.connect_over_cdp(
                f"http://127.0.0.1:{port}"
            )
        except Exception:
            await asyncio.sleep(0.5)
    raise RuntimeError("CDP connection failed")


async def _enter_edit_view(page: Any) -> dict[str, Any]:
    for _ in range(40):
        if "/project/" in page.url or "/edit/" in page.url:
            break
        await asyncio.sleep(0.5)
    start_url = page.url
    await asyncio.sleep(6)

    tile_click = None
    if "/edit/" not in page.url:
        tile_click = await page.evaluate(
            """() => {
                const tiles = document.querySelectorAll('a[href*="/edit/"], button[aria-label*="play" i], video');
                for (const tile of tiles) {
                    const rect = tile.getBoundingClientRect();
                    if (rect.width >= 100 && rect.height >= 60) {
                        tile.click();
                        return {tag: tile.tagName, w: Math.round(rect.width), h: Math.round(rect.height)};
                    }
                }
                const video = document.querySelector('video');
                if (video) {
                    let parent = video;
                    for (let depth = 0; depth < 5 && parent; depth++) {
                        parent = parent.parentElement;
                        if (parent && parent.tagName === 'BUTTON') {
                            parent.click();
                            return {tag: 'button-via-video'};
                        }
                    }
                    video.click();
                    return {tag: 'video-direct'};
                }
                return null;
            }"""
        )
        for _ in range(40):
            if "/edit/" in page.url:
                break
            await asyncio.sleep(0.5)
        await asyncio.sleep(6)

    return {"start_url": start_url, "tile_click": tile_click, "edit_url": page.url}


async def _type_prompt(page: Any, prompt: str) -> dict[str, Any]:
    editor = page.locator('[data-slate-editor="true"]').first
    await editor.wait_for(state="visible", timeout=30_000)
    before = await editor.inner_text(timeout=10_000)
    await editor.click(timeout=10_000)
    await page.keyboard.press("Control+A")
    await page.keyboard.type(prompt, delay=10)
    await asyncio.sleep(0.5)
    after = await editor.inner_text(timeout=10_000)
    return {"before": before, "after": after}


async def _click_submit(page: Any) -> dict[str, Any]:
    result = await page.evaluate(
        """() => {
            const visible = (el) => {
                const rect = el.getBoundingClientRect();
                const style = getComputedStyle(el);
                return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
            };
            for (const button of document.querySelectorAll('button, [role="button"]')) {
                if (!visible(button)) continue;
                const text = (button.innerText || '').trim().toLowerCase();
                const hasArrow = Array.from(button.querySelectorAll('i, span'))
                    .some((child) => (child.innerText || '').trim() === 'arrow_forward');
                if (hasArrow || text.includes('arrow_forward')) {
                    const rect = button.getBoundingClientRect();
                    button.click();
                    return {ok: true, text, rect: {x: Math.round(rect.x), y: Math.round(rect.y), w: Math.round(rect.width), h: Math.round(rect.height)}};
                }
            }
            return {ok: false};
        }"""
    )
    return result


async def main(profile: str, edit_url: str, prompt: str) -> int:
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    base = Path(os.environ.get("CHROME_USER_DATA_DIR", "chrome-profiles")).resolve()
    port = _port()
    proc = subprocess.Popen(
        [
            CHROME,
            f"--user-data-dir={base / profile}",
            f"--remote-debugging-port={port}",
            "--no-first-run",
            "--no-default-browser-check",
            edit_url,
        ]
    )
    captures: list[dict[str, Any]] = []
    all_google_posts: list[dict[str, Any]] = []
    try:
        async with async_playwright() as playwright:
            browser = await _wait_for_cdp(playwright, port)
            context = browser.contexts[0]
            page = context.pages[0] if context.pages else await context.new_page()

            async def intercept_video(route: Route) -> None:
                request = route.request
                item = {
                    "url": request.url,
                    "method": request.method,
                    "post_data": request.post_data or "",
                    "headers_subset": {
                        key: value
                        for key, value in request.headers.items()
                        if key.lower()
                        in {"content-type", "authorization", "x-goog-api-key"}
                    },
                }
                captures.append(item)
                print("CAPTURE_VIDEO_POST", json.dumps(item, ensure_ascii=True)[:4000])
                await route.abort()

            await context.route("**/v1/video:**", intercept_video)

            page.on(
                "request",
                lambda request: all_google_posts.append(
                    {
                        "url": request.url,
                        "method": request.method,
                        "post_data": request.post_data or "",
                    }
                )
                if request.method == "POST" and "googleapis.com" in request.url
                else None,
            )

            nav = await _enter_edit_view(page)
            print("NAV", json.dumps(nav, ensure_ascii=True))

            await page.screenshot(
                path=str(ARTIFACT_DIR / "l2_composer_probe_before.png"),
                full_page=True,
                timeout=15_000,
            )
            typed = await _type_prompt(page, prompt)
            print("TYPED", json.dumps(typed, ensure_ascii=True))
            submit = await _click_submit(page)
            print("SUBMIT", json.dumps(submit, ensure_ascii=True))
            await asyncio.sleep(8)
            await page.screenshot(
                path=str(ARTIFACT_DIR / "l2_composer_probe_after.png"),
                full_page=True,
                timeout=15_000,
            )

            page_state = await page.evaluate(
                """() => ({
                    url: location.href,
                    bodyText: document.body.innerText.slice(0, 2000),
                    editors: Array.from(document.querySelectorAll('[data-slate-editor="true"]')).map((editor) => ({
                        text: (editor.innerText || '').slice(0, 200),
                        placeholder: editor.getAttribute('data-placeholder') || '',
                    })),
                })"""
            )
            result = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "profile": profile,
                "input_edit_url": edit_url,
                "prompt": prompt,
                "nav": nav,
                "typed": typed,
                "submit": submit,
                "captures": captures,
                "all_google_posts": all_google_posts[:20],
                "page_state": page_state,
            }
            out = ARTIFACT_DIR / "l2_composer_probe_network.json"
            out.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
            print(f"OUT {out}")
            print(f"CAPTURE_COUNT {len(captures)}")
            await browser.close()
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=10)
        except Exception:
            proc.kill()
    return 0 if captures else 1


if __name__ == "__main__":
    if len(sys.argv) not in {3, 4}:
        print(
            "usage: probe_l2_composer_submit.py <profile> <edit_url> [prompt]",
            file=sys.stderr,
        )
        sys.exit(64)
    test_prompt = sys.argv[3] if len(sys.argv) == 4 else "extend the scene by 2 seconds"
    sys.exit(asyncio.run(main(sys.argv[1], sys.argv[2], test_prompt)))
