#!/usr/bin/env python3
"""Probe L2 edit UI submit: type command, capture network + screenshot.

Navigate to a known project, enter edit mode, type 'Extend this video',
then capture:
  1. Screenshot before submit
  2. All network requests for 10s after Enter AND after add_2 Create click
  3. Screenshot after attempt

Usage:
    cd /opt/flowengine
    DISPLAY=:99 CHROME_USER_DATA_DIR=/opt/flowengine/chrome-profiles \
    FLOW_REAL_CHROME=1 FLOW_USE_BASE_PROFILE=1 \
    python3 scripts/probe-l2-edit-submit.py [profile] [project_url]
"""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("probe-l2")

# Most recently generated project from R24 runs
DEFAULT_PROJECT = "https://labs.google/fx/tools/flow/project/00fa29a2-3cf4-4143-803d-5bdf8e068859"
DEFAULT_PROFILE = "ngoctuandt20"


async def run(profile: str, project_url: str) -> None:
    import os
    from flow.client import FlowClient
    from flow.agent import uninstall_agent_session_blocker

    os.environ.setdefault("CHROME_USER_DATA_DIR", "./chrome-profiles")
    os.environ.setdefault("FLOW_REAL_CHROME", "1")
    os.environ.setdefault("FLOW_USE_BASE_PROFILE", "1")

    async with FlowClient(profile) as client:
        page = client.page
        captured_requests = []

        def _on_request(req):
            url = req.url or ""
            method = req.method or ""
            if any(t in url.lower() for t in [
                "batchasync", "generatecontent", "streamchat", "aisandbox",
                "generate", "runagent", "flowcreation",
            ]):
                log.info("REQUEST: %s %s", method, url[:120])
                captured_requests.append(url)

        page.on("request", _on_request)

        # Navigate to project
        log.info("Navigating to project: %s", project_url)
        await uninstall_agent_session_blocker(page)
        await page.goto(project_url, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(3)

        log.info("URL after goto: %s", page.url)

        # Click first tile to enter edit mode
        tile = page.locator("[data-tile-id]").first
        if await tile.is_visible(timeout=5000):
            await tile.click()
            await asyncio.sleep(3)
            log.info("URL after tile click: %s", page.url)

        # Screenshot before
        await page.screenshot(path="/tmp/probe_before_submit.png")
        log.info("Screenshot saved: /tmp/probe_before_submit.png")

        # Find contenteditable
        editor = page.locator("[contenteditable='true']").first
        if await editor.is_visible(timeout=5000):
            log.info("Contenteditable found — typing command")
            await editor.click(timeout=3000)
            await asyncio.sleep(0.3)
            keyboard = page.keyboard
            await keyboard.press("Control+a")
            await asyncio.sleep(0.1)
            await editor.type("Extend this video: probe test", delay=30)
            await asyncio.sleep(0.5)

            log.info("--- Pressing Enter ---")
            await keyboard.press("Enter")
            await asyncio.sleep(5)  # wait for request
            log.info("Captured requests after Enter: %s", captured_requests)

            if not captured_requests:
                log.info("--- No request after Enter. Trying add_2 Create click ---")
                create_btn = page.locator("button:has(i:text-is('add_2'))").first
                if await create_btn.is_visible(timeout=2000):
                    await create_btn.click()
                    await asyncio.sleep(5)
                    log.info("Captured requests after Create click: %s", captured_requests)
                else:
                    log.warning("add_2 Create button not found")
        else:
            log.warning("Contenteditable NOT found")
            # Dump visible text
            text = await page.evaluate("() => document.body.innerText.slice(0, 500)")
            log.info("Page text: %s", text)

        # Screenshot after
        await page.screenshot(path="/tmp/probe_after_submit.png")
        log.info("Screenshot saved: /tmp/probe_after_submit.png")
        log.info("Total captured requests: %d", len(captured_requests))


def main():
    profile = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PROFILE
    project = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_PROJECT
    asyncio.run(run(profile, project))


if __name__ == "__main__":
    main()
