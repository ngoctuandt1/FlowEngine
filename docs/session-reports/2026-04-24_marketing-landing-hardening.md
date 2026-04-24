# Marketing-landing dismiss — hardening to green (#48 + #51)

**Date:** 2026-04-24
**Branch:** `claude/bug-51-marketing-landing-reload-retry`
**PR:** [#52](https://github.com/ngoctuandt1/FlowEngine/pull/52) (supersedes #50)
**Closes:** [#48](https://github.com/ngoctuandt1/FlowEngine/issues/48), [#51](https://github.com/ngoctuandt1/FlowEngine/issues/51)
**Status:** ✅ Live-verified on `ngoctuandt20`

---

## TL;DR

L1 `text-to-video` kept failing at Google Flow's marketing landing. Fixed across
4 live-verify iterations — each run exposed a deeper root cause. Final winning
recipe:

1. **`force=True`** on the CTA click — bypass Playwright actionability so the
   sticky `<header>` nav subtree can't intercept pointer events.
2. **Reload-retry wrapper** — Google A/Bs the hero CTA's React handler per
   request; `page.reload()` gives the A/B another roll.
3. **Tag-agnostic "+ New project" fallback** — `page.get_by_text(..., exact=True)`
   + `text=` in the ready selector so the tile is found whether it renders as
   `<button>`, `<div role="button">`, or otherwise.

Live job `baceb286-4d09-4909-816c-c362ee5a34c0`:
`completed` with `project_url`, `media_id`, and 1080p download (12 MB) in ~3 min.

---

## Symptoms

L1 `text-to-video` jobs on fresh `ngoctuandt20` sessions all hit:

```
ERROR: Failed to find '+ New project' button on Flow homepage
```

despite the account being logged in and the dashboard being visible when you
open the URL manually. Screenshots showed the Flow **marketing landing**
(`labs.google/fx/tools/flow`) rendered instead of the app — Google A/Bs
this variant even for signed-in sessions.

## Iteration log

### Run 1 — `<html> intercepts pointer events`

**Evidence:**
```
<html> intercepts pointer events
waiting for element to be visible, enabled and stable
... (retry × 3) ...
Locator.click: Timeout 5000ms exceeded
```

**Hypothesis:** A full-page overlay with `pointer-events: auto` was stealing
clicks. A DOM probe (`scripts/probe_marketing_overlay.py`) confirmed the
overlay existed but had `pointer-events: none`.

**Fix attempt (commit `ef3774e`):** JS `.click()` fallback after Playwright
click fails.

### Run 2 — JS click works but "+ New project" misses

JS click entered the app. URL cleaned to `/tools/flow`, project list visible.
But the subsequent `button:has-text('New project')` CSS selector timed out
because the tile renders as a `<div>` wrapper with nested children, not a
plain `<button>`.

**Fix:** Widened the ready selector to `button, a, [role='button']`.

### Run 3 — A/B variance (issue #51)

Same selector, same account, same minute → sometimes the CTA mounted the app,
sometimes it scroll-navigated to `#capabilities` with no further effect.
Google A/Bs the onClick handler per request — the DOM is identical; the React
binding differs.

**Fix (commit `4bcb26e`):** `dismiss_flow_marketing_landing(..., max_reloads=2)`
wraps the candidate loop in a reload-retry. Each `page.reload()` re-rolls
the A/B.

### Run 4 — Header nav subtree intercepts

Even with reload-retry, the scroll-linked sticky header kept blocking clicks:

```
<a href="#partners" class="sc-be124e6e-4 loiDfC">Partners</a>
    from <header class="sc-be124e6e-0 curipu"> subtree intercepts pointer events
```

Playwright's actionability retry kept re-scrolling, and the scroll listener
kept mutating the URL hash, racing the click.

**Fix (commit `0f09551`):** Replace the try-click / JS-fallback pattern with
a single `cta.click(timeout=5000, force=True)`. `force=True` bypasses the
actionability check entirely and dispatches at the element centre.

Also added a `page.get_by_text("New project", exact=True)` fallback in
`generate.py` so the tile is found regardless of tag.

### Run 5 — A/B sticky across reloads (post-merge-eve regression)

After the fix above merged-ready, a follow-up batch of 10 jobs hit a
fifth failure mode. Two jobs completed, then 8 in a row failed with the
same error as before: `Failed to find '+ New project' button`.

Log pattern on every failing run:

```
Flow marketing landing detected — clicking 'button:has-text('Create with Flow')'
CTA did not mount app within 8s (url=https://labs.google/fx/tools/flow#partners) — trying next
Marketing landing persisted — reload retry 1/2
Marketing landing persisted — reload retry 2/2
Page URL at failure: https://labs.google/fx/tools/flow#partners
```

Failure screenshot (`debug_screens/new_project_btn_missing_20260424_183039.png`)
showed only the marketing sticky header scrolled down to the Partners
section — the app never mounted, not even after two reloads. Every
"Create with Flow" candidate scroll-navigated to `#partners`, and plain
`page.reload()` served the same variant each time.

**Root cause:** the A/B assignment persists via `localStorage` /
`sessionStorage`. Cloning the profile preserves these, and `page.reload()`
doesn't touch them — so every retry rolls the same variant.

**Fix (commit `7f6ac29`):** last-ditch storage-reset bounce. When the
reload-retry loop exhausts without mounting:

```python
await page.evaluate(
    "() => { try { localStorage.clear(); sessionStorage.clear(); } catch (e) {} }"
)
await page.goto("https://labs.google/fx", wait_until="domcontentloaded", timeout=15000)
await asyncio.sleep(1.5)
await page.goto("https://labs.google/fx/tools/flow",
                wait_until="domcontentloaded", timeout=15000)
```

Cookies untouched → session auth intact. Fresh client storage + bouncing
through the parent `/fx` domain forces a fresh A/B roll, then the CTA
loop runs one last pass.

Gated on `max_reloads>0` so the pre-#51 `max_reloads=0` single-pass
contract is preserved.

**Live verification** — job `34c073be-982c-4dc1-bdfd-4a0f6572ea3a`:

```
19:23:21 Marketing landing persisted — reload retry 1/2
19:23:24 Marketing landing persisted — reload retry 2/2
19:23:27 Reload-retry exhausted — resetting client storage + bounce via /fx
19:23:49 Clicked new project via role=button name='New project'
19:26:17 text-to-video DONE
```

Result: `status=completed`, `project_url=.../project/db767c00...`,
`media_id=57812d68...`, 1080p download saved.

## Final architecture

`flow/landing.py`:

```python
_CREATE_WITH_FLOW_SELECTORS = (
    "main button:has-text('Create with Flow')",
    "main [role='button']:has-text('Create with Flow')",
    "main a:has-text('Create with Flow'):not([href^='#'])",
    "button:has-text('Create with Flow')",
    "[role='button']:has-text('Create with Flow')",
    "a:has-text('Create with Flow'):not([href^='#'])",
)

async def dismiss_flow_marketing_landing(page, logger, is_ready, *,
    per_click_timeout_sec=8.0, max_reloads=2, reload_settle_sec=2.0) -> bool:
    for attempt in range(max_reloads + 1):
        if attempt > 0:
            await page.reload(wait_until="domcontentloaded", timeout=15000)
            await asyncio.sleep(reload_settle_sec)
            if await is_ready():
                return True
        if await _dismiss_landing_once(page, logger, is_ready,
                                        per_click_timeout_sec):
            return True
    return False
```

Inside `_dismiss_landing_once`:

```python
# If a prior CTA candidate scrolled to an anchor, reset the URL so
# the hero is back at baseline before the next click.
if is_marketing_anchor_url(page.url):
    await page.evaluate(
        "() => history.replaceState(null, '', location.pathname)"
    )

# force=True skips actionability — the sticky header subtree can't
# intercept and Playwright won't re-scroll the scroll listener.
await cta.click(timeout=5000, force=True)

# Poll: /project/ in URL, OR is_ready() true, OR URL degraded to anchor
# (→ wrong CTA, try next).
```

`flow/operations/generate.py`:

```python
_NEW_PROJECT_APP_READY_SELECTOR = (
    "text=New project, text=Dự án mới, text=Tạo dự án"
)

# After CSS + role selectors fail, fall back to get_by_text and walk up
# to the first reasonably-sized ancestor to click.
if not new_project_clicked:
    for text in ("New project", "Dự án mới", "Tạo dự án"):
        tile = page.get_by_text(text, exact=True).first
        if await tile.is_visible(timeout=2000):
            await tile.click(timeout=5000)
            new_project_clicked = True
            break
```

## Live verification

**Job:** `baceb286-4d09-4909-816c-c362ee5a34c0`
**Prompt:** "a red fox in snow"
**Profile:** `ngoctuandt20`
**Model:** `veo-3.1-fast-lp`

| field | value |
|---|---|
| status | `completed` |
| project_url | `https://labs.google/fx/tools/flow/project/6fe81953-f6ca-4457-832f-d3ffbcaef3c1` |
| media_id | `f7b867b1-0895-44df-b091-eb6fb45d437b` |
| edit_url | `.../edit/f7b867b1-0895-44df-b091-eb6fb45d437b` |
| output_files | `downloads\t2v_1080p_1777029093.mp4` (12 198 517 bytes) |
| claimed_at → completed_at | 18:08:29 → 18:11:34 (3 m 5 s) |

Log excerpt showing the winning path:

```
18:08:35 Flow marketing landing detected — clicking 'button:has-text('Create with Flow')'
18:08:43 CTA 'button:has-text('Create with Flow')' did not mount app within 8s — trying next
18:08:43 Marketing landing persisted — reload retry 1/2
18:08:46 Marketing landing persisted — reload retry 2/2
18:09:05 New-project button did not attach within 15s — continuing
18:09:07 Clicked new project via role=button name='New project'   ← WIN
18:09:22 Submit clicked ...
18:10:22 Completion via DOM (new video at 58%) after 60s
18:11:33 [UPSCALE] Saved: downloads\t2v_1080p_1777029093.mp4 (12198517 bytes)
18:11:34 Job result sent -> completed
```

Unit tests: `pytest tests/test_landing_dismiss.py` — **15/15 green**.

## Lessons

1. **Read the Playwright error's `Call log:` section carefully.** The "X
   intercepts pointer events" line names the exact DOM subtree blocking the
   click. Fixing the symptom (retry, wait longer) instead of the cause
   (`force=True` or remove the interceptor) burned 3 live runs.
2. **Don't assume a repro means a deterministic A/B.** Three manual opens of
   the marketing URL on the same account/session yielded different React
   handlers. Always verify A/B assumptions by checking multiple reloads.
3. **Tag-agnostic selectors are cheap insurance.** `get_by_text(..., exact=True)`
   + a `text=` fallback adds one line and covers an entire class of
   tag-refactor regressions.

## Files touched

- `flow/landing.py` — rewritten dismiss helper with reload-retry + `force=True`
- `flow/operations/generate.py` — widened ready predicate + `get_by_text`
  fallback for "+ New project"
- `tests/test_landing_dismiss.py` — 15 tests, including reload-retry and
  anchor-abandon coverage
- `scripts/probe_marketing_overlay.py` — diagnostic DOM probe (new)

## Commits on branch

```
0f09551 fix(#51): force=True click + tag-agnostic New-project fallback
4bcb26e fix(#51): reload-retry loop for marketing landing A/B variance
ef3774e fix(#48): JS .click() fallback + widen New-project ready predicate
6c5ce2a fix(#48): scope marketing-landing CTA to hero, abandon anchor-scroll candidates
```
