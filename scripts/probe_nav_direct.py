"""Ad-hoc probe: open ngoctuandt20 profile, check locale, keep Chrome open.

1. Opens Flow homepage in the ngoctuandt20 profile.
2. Prints whether the landed URL contains /vi/ (still Vietnamese) or not.
3. Also navigates a second tab to myaccount.google.com/language so user can
   flip the preferred language to English.
4. Keeps Chrome open for 3 minutes so the user can interact.
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

FLOW_HOME = "https://labs.google/fx/tools/flow"
LANG_URL = "https://myaccount.google.com/language"
PROFILE = "ngoctuandt20"
PROFILE_BASE = os.environ.get("CHROME_USER_DATA_DIR", "D:/AI/chrome-profiles")
HOLD_SECS = 180


async def main() -> int:
    async with FlowClient(PROFILE, profile_base_dir=PROFILE_BASE, debug_port=19400) as client:
        page = client.page
        print(f"[probe] goto Flow home -> {FLOW_HOME}")
        await page.goto(FLOW_HOME, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(4)
        final_url = page.url
        print(f"[probe] landed URL: {final_url}")
        is_vi = "/fx/vi/" in final_url
        print(f"[probe] VI locale detected: {is_vi}")
        if is_vi:
            print("[probe] profile STILL on Vietnamese locale")
        else:
            print("[probe] profile ALREADY on English locale")
        # Open language settings in a second tab (regardless) so user can confirm/change
        lang_page = await page.context.new_page()
        print(f"[probe] opening language settings tab -> {LANG_URL}")
        await lang_page.goto(LANG_URL, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(3)
        print(f"[probe] language tab URL: {lang_page.url}")
        try:
            print(f"[probe] language tab title: {await lang_page.title()}")
        except Exception:
            pass
        print(f"[probe] keeping Chrome open for {HOLD_SECS}s - adjust language if needed")
        await asyncio.sleep(HOLD_SECS)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
