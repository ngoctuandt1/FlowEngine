"""One-shot: attach to Chrome with profile, dump Flow project media state.

Usage:
    python scripts/inspect_media_state.py <profile> <project_id> <media_id>

Navigates to the project URL, waits for the SPA to mount, takes a
screenshot, and dumps DOM info for every visible media tile so we can
tell whether the video finished rendering even when the engine missed
the completion signal.
"""

from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.async_api import async_playwright


CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def main(profile: str, project_id: str, media_id: str) -> int:
    base = Path(os.environ.get("CHROME_USER_DATA_DIR", "chrome-profiles")).resolve()
    user_data = base / profile
    port = _free_port()
    proc = subprocess.Popen([
        CHROME,
        f"--user-data-dir={user_data}",
        f"--remote-debugging-port={port}",
        "--no-first-run",
        "--no-default-browser-check",
        f"https://labs.google/fx/tools/flow/project/{project_id}",
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
                print("ERR: could not attach CDP", file=sys.stderr)
                return 2

            ctx = browser.contexts[0]
            # Pick a real tab — not chrome://newtab and not omnibox popup
            page = None
            for pg in ctx.pages:
                u = pg.url
                if u.startswith("https://labs.google") or u == "about:blank":
                    page = pg
                    break
            page = page or (ctx.pages[0] if ctx.pages else await ctx.new_page())

            for _ in range(40):
                if "/project/" in page.url or "/edit/" in page.url:
                    break
                await asyncio.sleep(0.5)
            print(f"URL={page.url}")

            # Wait for SPA mount
            await asyncio.sleep(8)

            shot_dir = Path("docs/livetest-2026-05-21")
            shot_dir.mkdir(parents=True, exist_ok=True)
            shot = shot_dir / f"project_{project_id[:8]}_after_T5.png"
            try:
                await page.screenshot(path=str(shot), full_page=True, timeout=15000)
                print(f"screenshot={shot}")
            except Exception as exc:
                print(f"screenshot_error={exc}")

            # Dump media tiles. Flow renders each generated clip as a
            # card/tile under a list region. We try a few generic shapes
            # — `[data-media-id]`, `<video>`, and any element whose innerText
            # contains the media_id prefix.
            info = await page.evaluate(
                """({prefix}) => {
                    const out = {
                        title: document.title,
                        url: location.href,
                        videos: [],
                        images: [],
                        tile_count_data_media_id: 0,
                        any_text_match: false,
                        page_text_snippet: (document.body?.innerText || "").slice(0, 600),
                    };
                    document.querySelectorAll("video").forEach(v => {
                        out.videos.push({
                            src: v.currentSrc || v.src || "",
                            duration: v.duration,
                            readyState: v.readyState,
                            paused: v.paused,
                            poster: v.poster || "",
                        });
                    });
                    document.querySelectorAll("img").forEach(img => {
                        if (img.src && img.src.includes("googleusercontent")) {
                            out.images.push({src: img.src.slice(0, 200), alt: img.alt || ""});
                        }
                    });
                    out.tile_count_data_media_id = document.querySelectorAll("[data-media-id]").length;
                    if (prefix && document.body) {
                        out.any_text_match = (document.body.innerText || "").includes(prefix);
                    }
                    return out;
                }""",
                {"prefix": media_id[:12]},
            )
            print("---DOM_DUMP---")
            print(f"title={info.get('title')}")
            print(f"tile_count_data_media_id={info.get('tile_count_data_media_id')}")
            print(f"text_contains_media_id_prefix={info.get('any_text_match')}")
            print(f"video_count={len(info.get('videos', []))}")
            for i, v in enumerate(info.get("videos", [])):
                print(f"  video[{i}] dur={v.get('duration')} ready={v.get('readyState')} src={(v.get('src') or '')[:80]} poster={(v.get('poster') or '')[:60]}")
            print(f"image_count={len(info.get('images', []))}")
            for i, im in enumerate(info.get("images", [])[:5]):
                print(f"  image[{i}] alt={im.get('alt')!r:.60} src={im.get('src')}")
            text = info.get("page_text_snippet") or ""
            print(f"page_text[0:600]={text!r}")

            await browser.close()
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=10)
        except Exception:
            proc.kill()
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 4:
        print("usage: inspect_media_state.py <profile> <project_id> <media_id>", file=sys.stderr)
        sys.exit(64)
    sys.exit(asyncio.run(main(sys.argv[1], sys.argv[2], sys.argv[3])))
