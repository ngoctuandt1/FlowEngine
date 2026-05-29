#!/usr/bin/env python3
"""Live smoke test for flow.agent_settings.ensure_agent_settings.

Opens a FlowClient, creates a fresh project, opens the Agent settings panel,
applies confirm=Never + count=1, and screenshots before/after. NO generation
or submit happens — this is a zero-credit UI probe.

Usage::

    python scripts/test_agent_settings_live.py <profile>

Requires a real Chrome + logged-in Google session (debian worker host). On a
headless/Windows dev box it will likely fail at login; that is expected.
"""

import asyncio
import logging
import os
import sys

# Allow running from repo root without installing the package.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flow.client import FlowClient
from flow.agent_settings import ensure_agent_settings
from flow.operations.frames_to_video import _click_new_project
from flow.operations.generate import _dismiss_overlays, _wait_for_composer
from flow.navigation import flow_url

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("test_agent_settings_live")

SHOT_DIR = "/tmp/flow-ui-map"


async def _shot(page, name: str) -> None:
    try:
        os.makedirs(SHOT_DIR, exist_ok=True)
        path = os.path.join(SHOT_DIR, f"agentsettings_{name}.png")
        await page.screenshot(path=path, full_page=True)
        logger.info("screenshot: %s", path)
    except Exception as exc:
        logger.warning("screenshot %s failed: %s", name, exc)


async def _never_radio_checked(page) -> bool:
    try:
        return await page.evaluate(
            """() => {
                const radios = Array.from(document.querySelectorAll('button[role="radio"]'));
                for (const r of radios) {
                    const t = (r.textContent || '').toLowerCase();
                    if (t.includes('never') || t.includes('automatically')) {
                        return r.getAttribute('data-state') === 'checked';
                    }
                }
                return false;
            }"""
        )
    except Exception as exc:
        logger.warning("never-radio probe failed: %s", exc)
        return False


async def main(profile: str) -> int:
    async with FlowClient(profile) as client:
        page = client.page
        await page.goto(flow_url(""), wait_until="domcontentloaded", timeout=30000)
        await _dismiss_overlays(page)

        logger.info("creating new project...")
        await _click_new_project(page)
        await page.wait_for_url("**/project/**", timeout=20000)
        await _wait_for_composer(page)
        await _shot(page, "before")

        ok = await ensure_agent_settings(page, confirm_never=True, count=1)
        logger.info("ensure_agent_settings returned: %s", ok)
        await _shot(page, "after")

        checked = await _never_radio_checked(page)
        logger.info("Never radio checked after Save: %s", checked)
        print(f"RESULT ensure_agent_settings={ok} never_checked={checked}")
        return 0 if ok else 1


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: python scripts/test_agent_settings_live.py <profile>", file=sys.stderr)
        sys.exit(2)
    sys.exit(asyncio.run(main(sys.argv[1])))
