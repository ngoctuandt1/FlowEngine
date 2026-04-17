"""
Integration test — issue #8: LP model selector in extend mode must not fire
generation (credit-consuming) API requests during open / cycle / close.

Acceptance criteria (from GitHub issue #8):
  "opening, browsing, and closing the model selector in extend mode produces
   zero credit delta; only the explicit submit action consumes credits."

How the test works
------------------
1. A headless Playwright browser loads a mock HTML page that simulates the
   Flow extend UI.  The mock page fires a POST to
   ``/api/operations/generate`` when an LP model option is clicked — this
   reproduces the credit-leak bug that existed before the fix.

2. ``select_model()`` is called (open chip → switch Video tab → open dropdown
   → click LP option → close panel) WITHOUT any explicit submit.

3. The ``_generation_guard`` route handler inside ``select_model()`` (the
   bug-#8 fix) aborts any ``operations/`` request before it reaches the
   server, so no response is ever received.

4. We assert that ``page.on("response")`` captured zero responses whose URL
   matches the generation pattern.  A response can only be captured if the
   request was NOT aborted by the guard — so an empty list proves the fix
   works.

Run with:
    python -m pytest tests/test_lp_model_no_generate.py -v
or standalone:
    python tests/test_lp_model_no_generate.py
"""

from __future__ import annotations

import asyncio
import sys
import re

# ---------------------------------------------------------------------------
# Mock HTML: simulates the Flow extend panel UI.
#
# The page intentionally fires POST /api/operations/generate when an LP model
# option is clicked — this is the bug-reproduction step.  With the fix in
# place the route guard aborts that request before it reaches the network.
# ---------------------------------------------------------------------------

_ORIGIN = "https://mock-flow-test.internal"

MOCK_EXTEND_HTML = f"""
<!DOCTYPE html>
<html>
<body style="margin:0;padding:20px;background:#111;color:#eee;font-family:sans-serif;">

  <!-- Simulated video from a loaded project -->
  <video id="video" src="" width="320" height="180"
         style="background:#222;display:block;margin-bottom:12px;"></video>

  <!-- Model chip (bottom bar) — clicking opens the model panel -->
  <div style="position:fixed;bottom:20px;left:20px;">
    <button id="chip" style="padding:6px 14px;background:#333;color:#fff;border:1px solid #555;border-radius:20px;">
      Veo 3.1 - Fast <span>x1</span>
    </button>
  </div>

  <!-- Model selection panel (initially hidden) -->
  <div id="panel" style="display:none;position:fixed;bottom:70px;left:20px;
       background:#222;border:1px solid #555;border-radius:8px;padding:16px;width:280px;">

    <!-- Two tabs: Image and Video -->
    <div style="display:flex;gap:8px;margin-bottom:12px;">
      <button role="tab" style="padding:4px 10px;background:#444;color:#eee;border:1px solid #555;">
        Image
      </button>
      <button role="tab" style="padding:4px 10px;background:#444;color:#fff;border:1px solid #aaa;">
        Video
      </button>
    </div>

    <!-- Model dropdown trigger (Veo button with dropdown arrow) -->
    <button id="veo-dropdown" style="width:100%;text-align:left;padding:6px 10px;
            background:#333;color:#fff;border:1px solid #555;border-radius:4px;">
      Veo 3.1 - Fast <i>arrow_drop_down</i>
    </button>

    <!-- Model options list (initially hidden) -->
    <div id="model-list" style="display:none;margin-top:4px;background:#2a2a2a;
         border:1px solid #555;border-radius:4px;padding:4px 0;">
      <menuitem id="lp-fast" style="display:block;padding:8px 12px;cursor:pointer;">
        Veo 3.1 - Fast [Lower Priority]
      </menuitem>
      <menuitem id="lp-lite" style="display:block;padding:8px 12px;cursor:pointer;">
        Veo 3.1 - Lite [Lower Priority]
      </menuitem>
    </div>

    <!-- Credit indicator (shown after model selection) -->
    <div id="credit-info" style="margin-top:10px;font-size:12px;color:#aaa;">
      will use 0 credits &mdash; Lower Priority
    </div>

    <!-- Extend prompt editor (Slate-compatible element) -->
    <div data-slate-editor="true" contenteditable="true"
         style="margin-top:10px;min-height:32px;padding:6px;background:#1a1a1a;
                border:1px solid #444;border-radius:4px;color:#ccc;font-size:13px;">
      What happens next?
    </div>
  </div>

  <!-- Submit button (explicit action — SHOULD fire generation) -->
  <button id="submit-btn" style="position:fixed;bottom:20px;right:20px;
          padding:8px 16px;background:#1a73e8;color:#fff;border:none;border-radius:4px;">
    <i>arrow_forward</i> Extend
  </button>

  <script>
    // Chip click: open the model panel
    document.getElementById('chip').onclick = function() {{
      document.getElementById('panel').style.display = 'block';
    }};

    // Video tab: no-op (panel already shows video models)
    document.querySelectorAll('[role="tab"]').forEach(function(tab) {{
      tab.onclick = function() {{}};
    }});

    // Veo dropdown click: show model list
    document.getElementById('veo-dropdown').onclick = function() {{
      document.getElementById('model-list').style.display = 'block';
    }};

    // -----------------------------------------------------------------------
    // THE BUG (pre-fix behaviour reproduced here):
    // Clicking an LP model option fires a hidden generation request before the
    // user has explicitly submitted.  This consumes credits unintentionally.
    // The fix in select_model() registers a route guard that aborts this
    // request so it never reaches Google's servers.
    // -----------------------------------------------------------------------
    function fireLPClick(modelId) {{
      fetch('{_ORIGIN}/api/operations/generate', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{model: modelId, type: 'extend'}})
      }}).catch(function() {{}});  // network errors are expected in mock
    }}

    document.getElementById('lp-fast').onclick = function() {{
      fireLPClick('lp-fast');
    }};
    document.getElementById('lp-lite').onclick = function() {{
      fireLPClick('lp-lite');
    }};

    // Submit button fires the LEGITIMATE generation call
    document.getElementById('submit-btn').onclick = function() {{
      fetch('{_ORIGIN}/api/operations/generate', {{
        method: 'POST',
        headers: {{'Content-Type': 'application/json'}},
        body: JSON.stringify({{submit: true}})
      }}).catch(function() {{}});
    }};
  </script>
</body>
</html>
"""

_GENERATION_RE = re.compile(r"operations/|/generate\b", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _setup_mock_page(playwright):
    """Launch a headless browser, serve the mock extend page, return page."""
    browser = await playwright.chromium.launch(headless=True)
    page = await browser.new_page()

    # Route: serve our mock HTML for the mock origin root
    async def _serve(route):
        url = route.request.url
        if url.rstrip("/") == _ORIGIN or url == f"{_ORIGIN}/":
            await route.fulfill(
                status=200,
                content_type="text/html",
                body=MOCK_EXTEND_HTML,
            )
        else:
            # Other paths (including /api/operations/generate):
            # If the generation guard hasn't aborted this request first it will
            # reach here.  Return a plausible JSON response so the fetch()
            # inside the mock page doesn't throw an unhandled exception.
            await route.fulfill(
                status=200,
                content_type="application/json",
                body='{"name": "operations/mock-gen-id-should-not-happen"}',
            )

    await page.route(f"{_ORIGIN}/**", _serve)
    await page.goto(_ORIGIN, wait_until="domcontentloaded")
    return browser, page


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

async def _test_no_generate_on_model_cycle():
    """
    Core assertion: open extend panel → cycle LP models → close → zero
    operations/ responses captured.
    """
    from playwright.async_api import async_playwright
    from flow.model_selector import select_model

    generation_responses: list[str] = []

    async with async_playwright() as pw:
        browser, page = await _setup_mock_page(pw)

        # Capture every response — the guard aborts generation URLs so they
        # should never produce a response.
        page.on(
            "response",
            lambda r: generation_responses.append(r.url)
            if _GENERATION_RE.search(r.url)
            else None,
        )

        # Run: open model panel, select LP model, close — WITHOUT submitting
        await select_model(page, model="veo-3.1-fast-lp", free_mode=True)

        # Give any in-flight async fetches a moment to settle
        await asyncio.sleep(0.3)

        await browser.close()

    assert generation_responses == [], (
        f"Credit leak detected: {len(generation_responses)} generation "
        f"response(s) received during model selection (without submit):\n"
        + "\n".join(f"  {u}" for u in generation_responses)
    )


async def _test_explicit_submit_still_generates():
    """
    Sanity check: after model selection completes (guard removed), an explicit
    click on the submit button DOES fire a generation request.  This ensures
    the guard does not permanently block legitimate calls.
    """
    from playwright.async_api import async_playwright
    from flow.model_selector import select_model

    submit_responses: list[str] = []

    async with async_playwright() as pw:
        browser, page = await _setup_mock_page(pw)

        # Run model selection first (guard active then removed)
        await select_model(page, model="veo-3.1-fast-lp", free_mode=True)

        # Now track generation responses — guard is gone
        page.on(
            "response",
            lambda r: submit_responses.append(r.url)
            if _GENERATION_RE.search(r.url)
            else None,
        )

        # Click the explicit submit button
        submit_btn = page.locator("#submit-btn")
        if await submit_btn.is_visible(timeout=2000):
            await submit_btn.click()
            # Wait for the fetch to complete
            await asyncio.sleep(0.5)

        await browser.close()

    # The submit button's fetch should have received a response
    assert submit_responses, (
        "Sanity check failed: explicit submit did not produce any "
        "generation response — the guard may be leaking beyond model selection"
    )


# ---------------------------------------------------------------------------
# pytest entry-points
# ---------------------------------------------------------------------------

def test_lp_model_selector_no_generate_on_extend_cycle():
    """
    LP model selector in extend mode must not fire /operations/ API calls
    while browsing/cycling models before an explicit submit (issue #8).
    """
    asyncio.run(_test_no_generate_on_model_cycle())


def test_explicit_submit_still_fires_generation_after_model_selection():
    """
    After model selection the route guard must be removed so that a real
    submit can still trigger the generation API (issue #8 sanity check).
    """
    asyncio.run(_test_explicit_submit_still_generates())


# ---------------------------------------------------------------------------
# Standalone runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys

    print("Running: test_lp_model_selector_no_generate_on_extend_cycle")
    try:
        asyncio.run(_test_no_generate_on_model_cycle())
        print("  PASS")
    except AssertionError as exc:
        print(f"  FAIL: {exc}")
        sys.exit(1)
    except Exception as exc:
        print(f"  ERROR: {exc}")
        sys.exit(1)

    print("Running: test_explicit_submit_still_fires_generation_after_model_selection")
    try:
        asyncio.run(_test_explicit_submit_still_generates())
        print("  PASS")
    except AssertionError as exc:
        print(f"  FAIL: {exc}")
        sys.exit(1)
    except Exception as exc:
        print(f"  ERROR: {exc}")
        sys.exit(1)

    print("\nAll tests passed.")
