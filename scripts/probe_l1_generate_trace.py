"""Trace what network fires when we submit an L1 prompt in the new agent UI.

Sets Confirm=Never, types a prompt, clicks arrow_forward Create, then logs ALL
non-static network requests for 60s + whether a <video> appears. Tells us:
- does generation actually fire? (1 credit if yes)
- what endpoint/URL is the generate request?
- does Confirm=Never make it auto-generate?

Usage:
    python scripts/probe_l1_generate_trace.py <profile> [project_url]
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from flow.client import FlowClient
from flow.navigation import flow_url
from flow.agent_settings import ensure_agent_settings
from flow.operations.generate import _dismiss_overlays

PROMPT = "A calm river flowing through a misty forest at dawn, cinematic"

_STATIC = (".svg", ".png", ".jpg", ".css", ".woff", ".js", "/fonts/", "gstatic",
           "googletagmanager", "recaptcha", "flower-placeholder")


async def main():
    profile = sys.argv[1]
    url = sys.argv[2] if len(sys.argv) > 2 else None

    seen: list[tuple[str, str]] = []

    async with FlowClient(profile) as c:
        p = c.page

        def on_req(req):
            u = req.url
            if any(s in u for s in _STATIC):
                return
            seen.append((req.method, u))

        p.on("request", on_req)

        if url:
            await p.goto(url, wait_until="domcontentloaded", timeout=40_000)
        else:
            await p.goto(flow_url(""), wait_until="domcontentloaded", timeout=40_000)
        await asyncio.sleep(5)
        await _dismiss_overlays(p)

        # 1. Confirm=Never (validated working)
        ok = await ensure_agent_settings(p, confirm_never=True, count=1)
        print("ensure_agent_settings ->", ok)
        await asyncio.sleep(1)

        # 2. type prompt into the composer
        ed = p.locator("[contenteditable='true']").first
        await ed.click(timeout=5000)
        await ed.type(PROMPT, delay=20)
        await asyncio.sleep(0.5)

        # 3. mark + real-click arrow_forward Create
        marked = await p.evaluate(
            """()=>{
              const vis=(e)=>{const r=e.getBoundingClientRect();return r.width>0&&r.height>0&&e.offsetParent!==null;};
              for (const b of document.querySelectorAll('button')){
                if(!vis(b))continue;
                const i=b.querySelector('i,span'); const ic=i?(i.textContent||'').trim():'';
                if(ic==='arrow_forward'){b.setAttribute('data-go','1');return (b.textContent||'').trim().slice(0,30);}
              }
              return null;
            }"""
        )
        print("arrow_forward marked ->", marked)
        n_before = len(seen)
        if marked:
            await p.locator("[data-go='1']").click(timeout=4000)
        else:
            await p.keyboard.press("Enter")
            print("fallback: pressed Enter")

        # 4. trace network for 60s
        print("--- tracing network 60s after submit ---")
        for _ in range(12):
            await asyncio.sleep(5)
            has_video = await p.evaluate("()=>document.querySelectorAll('video').length")
            print(f"  t+{(_+1)*5}s: reqs={len(seen)-n_before} videos={has_video}")

        print("=== NEW requests after submit ===")
        for m, u in seen[n_before:]:
            print(f"  {m} {u[:130]}")

    print("DONE")


if __name__ == "__main__":
    asyncio.run(main())
