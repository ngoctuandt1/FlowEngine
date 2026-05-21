"""Probe Flow L1 Frames/Ingredients upload DOM without submitting.

Usage:
    python scripts/probe_l1_composer_uploads.py frames <profile> <image_path>
    python scripts/probe_l1_composer_uploads.py ingredients <profile> <image_path>

The probe creates a fresh project, switches the composer to the requested L1
sub-mode, clicks the relevant upload affordance, sets the native file chooser if
one opens, and writes DOM/screenshot artifacts under docs/livetest-2026-05-21.
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

from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from flow.model_selector import DEFAULT_MODEL, select_model
from flow.navigation import flow_url
from flow.operations.frames_to_video import _click_new_project, _close_composer_menu
from flow.operations.generate import (
    _dismiss_overlays,
    _ensure_video_composer_mode,
    _set_aspect_ratio,
    _set_output_count,
    _select_video_composer_subtab,
    _wait_for_composer,
)


CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
ARTIFACT_DIR = Path("docs/livetest-2026-05-21")


def _port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


async def _wait_for_cdp(playwright: Any, port: int):
    for _ in range(40):
        try:
            return await playwright.chromium.connect_over_cdp(
                f"http://127.0.0.1:{port}"
            )
        except Exception:
            await asyncio.sleep(0.5)
    raise RuntimeError("CDP connection failed")


async def _prepare_project(page: Any, profile: str, mode: str) -> dict[str, Any]:
    await page.goto(flow_url(""), wait_until="domcontentloaded", timeout=30_000)
    await asyncio.sleep(2)
    await _dismiss_overlays(page)
    await _click_new_project(page)
    try:
        await page.wait_for_url("**/project/**", timeout=20_000)
    except Exception:
        await asyncio.sleep(1)
    await _wait_for_composer(page)
    await _ensure_video_composer_mode(page)
    await _set_output_count(page, 1)
    await select_model(page, model=DEFAULT_MODEL, free_mode=True, profile=profile)
    await _ensure_video_composer_mode(page)
    await _set_aspect_ratio(page, "16:9")
    await _set_output_count(page, 1)
    await _select_video_composer_subtab(page, "Frames" if mode == "frames" else "Ingredients")
    await _close_composer_menu(page)
    await asyncio.sleep(1)
    return {"url": page.url, "title": await page.title()}


async def _snapshot_dom(page: Any, note: str) -> dict[str, Any]:
    return await page.evaluate(
        r"""(note) => {
            const visible = (el) => {
                if (!el) return false;
                const style = getComputedStyle(el);
                const rect = el.getBoundingClientRect();
                return style.display !== 'none'
                    && style.visibility !== 'hidden'
                    && Number(style.opacity || 1) !== 0
                    && rect.width > 0
                    && rect.height > 0;
            };
            const rectOf = (el) => {
                const rect = el.getBoundingClientRect();
                return {
                    x: Math.round(rect.x),
                    y: Math.round(rect.y),
                    w: Math.round(rect.width),
                    h: Math.round(rect.height),
                };
            };
            const textOf = (el, max = 160) =>
                (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim().slice(0, max);
            const describe = (el) => ({
                tag: el.tagName,
                role: el.getAttribute('role') || '',
                ariaLabel: el.getAttribute('aria-label') || '',
                title: el.getAttribute('title') || '',
                type: el.getAttribute('type') || '',
                accept: el.getAttribute('accept') || '',
                multiple: el.hasAttribute('multiple'),
                dataState: el.getAttribute('data-state') || '',
                className: String(el.className || '').slice(0, 160),
                text: textOf(el),
                visible: visible(el),
                rect: rectOf(el),
            });

            const interesting = Array.from(document.querySelectorAll('button, [role="button"], input[type="file"], [role="dialog"], [aria-modal="true"], [data-slate-editor="true"]'))
                .filter((el) => visible(el) || el.matches('input[type="file"]'))
                .map(describe)
                .slice(0, 180);

            const textNodes = Array.from(document.querySelectorAll('body *'))
                .filter((el) => visible(el))
                .map((el) => ({tag: el.tagName, text: textOf(el, 80), rect: rectOf(el)}))
                .filter((item) => /start|end|ingredient|upload|add|image|frame|media|hình|ảnh/i.test(item.text))
                .slice(0, 160);

            return {
                note,
                url: location.href,
                bodyText: (document.body.innerText || '').slice(0, 3000),
                inputs: Array.from(document.querySelectorAll('input[type="file"]')).map(describe),
                interesting,
                textNodes,
            };
        }""",
        note,
    )


async def _find_frames_start_click(page: Any) -> dict[str, Any] | None:
    return await page.evaluate(
        r"""() => {
            const visible = (el) => {
                if (!el) return false;
                const style = getComputedStyle(el);
                const rect = el.getBoundingClientRect();
                return style.display !== 'none'
                    && style.visibility !== 'hidden'
                    && Number(style.opacity || 1) !== 0
                    && rect.width > 0
                    && rect.height > 0;
            };
            const center = (el) => {
                const rect = el.getBoundingClientRect();
                return {
                    x: Math.round(rect.left + rect.width / 2),
                    y: Math.round(rect.top + rect.height / 2),
                    w: Math.round(rect.width),
                    h: Math.round(rect.height),
                    text: (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 160),
                    tag: el.tagName,
                    role: el.getAttribute('role') || '',
                };
            };
            const labels = Array.from(document.querySelectorAll('body *')).filter((el) => {
                if (!visible(el)) return false;
                const text = (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim();
                return text === 'Start' || /^Start\b/i.test(text);
            });
            for (const label of labels) {
                let cur = label;
                for (let depth = 0; cur && depth < 8; depth += 1, cur = cur.parentElement) {
                    const clickable = Array.from(cur.querySelectorAll('button, [role="button"], label, input[type="file"]'))
                        .filter(visible)
                        .sort((a, b) => {
                            const ar = a.getBoundingClientRect();
                            const br = b.getBoundingClientRect();
                            return (ar.width * ar.height) - (br.width * br.height);
                        })[0];
                    if (clickable) return {...center(clickable), source: 'descendant-clickable'};
                    if (visible(cur)) {
                        const rect = cur.getBoundingClientRect();
                        if (rect.width >= 50 && rect.height >= 40) return {...center(cur), source: 'ancestor-slot'};
                    }
                }
            }
            const addButtons = Array.from(document.querySelectorAll('button, [role="button"]')).filter((el) => {
                if (!visible(el)) return false;
                const text = (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim().toLowerCase();
                return text === 'add' || text === '+' || text.includes('add_photo') || text.includes('upload');
            });
            if (addButtons.length) return {...center(addButtons[0]), source: 'first-add-like-button'};
            return null;
        }"""
    )


async def _find_ingredients_add_click(page: Any) -> dict[str, Any] | None:
    return await page.evaluate(
        r"""() => {
            const visible = (el) => {
                if (!el) return false;
                const style = getComputedStyle(el);
                const rect = el.getBoundingClientRect();
                return style.display !== 'none'
                    && style.visibility !== 'hidden'
                    && Number(style.opacity || 1) !== 0
                    && rect.width > 0
                    && rect.height > 0;
            };
            const candidates = Array.from(document.querySelectorAll('button, [role="button"]')).filter((el) => {
                if (!visible(el)) return false;
                const text = (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim().toLowerCase();
                const title = (el.getAttribute('title') || el.getAttribute('aria-label') || '').toLowerCase();
                if (title.includes('add media')) return false;
                return text === '+' || text === 'add' || text.includes('add') || title.includes('ingredient') || title.includes('upload');
            });
            const ranked = candidates.map((el) => {
                const rect = el.getBoundingClientRect();
                const text = (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim();
                const score = (text === '+' ? 100 : 0)
                    + (text.toLowerCase() === 'add' ? 80 : 0)
                    + (rect.top > window.innerHeight * 0.4 ? 20 : 0)
                    - Math.abs(rect.left - window.innerWidth / 2) / 100;
                return {el, score};
            }).sort((a, b) => b.score - a.score);
            if (!ranked.length) return null;
            const rect = ranked[0].el.getBoundingClientRect();
            return {
                source: 'ranked-add-button',
                x: Math.round(rect.left + rect.width / 2),
                y: Math.round(rect.top + rect.height / 2),
                w: Math.round(rect.width),
                h: Math.round(rect.height),
                text: (ranked[0].el.innerText || ranked[0].el.textContent || '').replace(/\s+/g, ' ').trim(),
                tag: ranked[0].el.tagName,
                role: ranked[0].el.getAttribute('role') || '',
                score: ranked[0].score,
            };
        }"""
    )


async def _click_and_maybe_upload(page: Any, target: dict[str, Any], image_path: str) -> dict[str, Any]:
    try:
        async with page.expect_file_chooser(timeout=4_000) as chooser_info:
            await page.mouse.click(target["x"], target["y"])
        chooser = await chooser_info.value
        await chooser.set_files(image_path)
        await asyncio.sleep(3)
        return {"fileChooser": True, "target": target}
    except PlaywrightTimeoutError:
        await asyncio.sleep(2)
        upload_button = page.locator(
            "button:has(i:text-is('upload')), button:has-text('Upload media'), [role='button']:has-text('Upload media')"
        ).last
        try:
            async with page.expect_file_chooser(timeout=5_000) as chooser_info:
                await upload_button.click(timeout=3_000)
            chooser = await chooser_info.value
            await chooser.set_files(image_path)
            await asyncio.sleep(5)
            return {"fileChooser": True, "target": target, "via": "picker-upload-media"}
        except PlaywrightTimeoutError:
            await asyncio.sleep(2)
            return {"fileChooser": False, "target": target, "via": "none"}


async def main(mode: str, profile: str, image_path: str) -> int:
    if mode not in {"frames", "ingredients"}:
        raise SystemExit("mode must be frames or ingredients")
    image = Path(image_path).resolve(strict=True)
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
            "about:blank",
        ]
    )
    try:
        async with async_playwright() as playwright:
            browser = await _wait_for_cdp(playwright, port)
            context = browser.contexts[0]
            page = context.pages[0] if context.pages else await context.new_page()
            nav = await _prepare_project(page, profile, mode)
            before = await _snapshot_dom(page, "before-click")
            await page.screenshot(
                path=str(ARTIFACT_DIR / f"l1_{mode}_upload_before.png"),
                full_page=True,
                timeout=15_000,
            )

            target = await (_find_frames_start_click(page) if mode == "frames" else _find_ingredients_add_click(page))
            click_result = None
            if target:
                click_result = await _click_and_maybe_upload(page, target, str(image))
            after = await _snapshot_dom(page, "after-click")
            await page.screenshot(
                path=str(ARTIFACT_DIR / f"l1_{mode}_upload_after.png"),
                full_page=True,
                timeout=15_000,
            )
            result = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "mode": mode,
                "profile": profile,
                "image_path": str(image),
                "nav": nav,
                "target": target,
                "click_result": click_result,
                "before": before,
                "after": after,
            }
            out = ARTIFACT_DIR / f"l1_{mode}_upload_probe.json"
            out.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
            print(f"OUT {out}")
            print(json.dumps({"nav": nav, "target": target, "click_result": click_result}, indent=2))
            await browser.close()
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=10)
        except Exception:
            proc.kill()
    return 0 if target else 1


if __name__ == "__main__":
    if len(sys.argv) != 4:
        print(
            "usage: probe_l1_composer_uploads.py <frames|ingredients> <profile> <image_path>",
            file=sys.stderr,
        )
        sys.exit(64)
    sys.exit(asyncio.run(main(sys.argv[1], sys.argv[2], sys.argv[3])))
