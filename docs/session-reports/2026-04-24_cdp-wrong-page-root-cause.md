# CDP picked the wrong page — the real root cause behind the 2026-04-24 batch failures

**Date:** 2026-04-24
**Branch:** `claude/bug-51-marketing-landing-reload-retry` (worktree `condescending-darwin-749d14`)
**Key commits:**
- `b7843b8` fix(client): skip chrome:// internal UI pages when picking primary CDP page **← real fix**
- `b7a2620` revert(landing): drop storage-reset + /fx bounce (misdiagnosis)
- `backup/ab-layers-pre-revert` tag preserves the over-engineered A/B layer for recovery
**Status:** ✅ Live-verified: 3 consecutive L1 text-to-video completions + 23-job rerun in flight.

---

## TL;DR

All the L1 `text-to-video` failures on 2026-04-24 (20/28 jobs in the DB marked `failed` before the fix) were caused by Playwright attaching to a **`chrome://omnibox-popup.top-chrome/`** page — the URL-bar autocomplete dropdown that Chrome 147 exposes as a CDP "page" — instead of the real browser tab. `page.goto("https://labs.google/fx/tools/flow")` returned success on that internal WebUI surface; `page.url` reported the new URL; but the visible tab stayed on `chrome://new-tab-page/` with an empty URL bar. The symptoms — "app never mounts after CTA click", "#capabilities scroll on every CTA attempt" — read as a sticky marketing-landing A/B and sent the prior debugging pass down three wrong roads. The actual fix is one filter in `_start_cdp` that skips `chrome://omnibox*`, `chrome://tab-search*`, and `devtools://` before binding `self.page`.

---

## Why this matters (and why it took so long)

Every layer of "fix" added before this session was aimed at the wrong target:

| Commit | Intent | What it actually did |
|---|---|---|
| `0f09551` | `force=True` click bypass header intercept | Correct; header intercept was real |
| `4bcb26e` | reload-retry to re-roll A/B | Legitimately hides the symptom for lucky runs |
| `7f6ac29` | `localStorage.clear()` + `/fx` bounce to defeat "sticky A/B" | Treats a non-existent disease |
| `52b12f9` | Stop abandoning CTA on URL hash change | Correct per memory; tangentially helpful |

All four were debugged from worker logs alone. Logs said navigation succeeded, the page had the marketing-landing DOM, CTA clicks scroll-navigated to `#capabilities`. Every signal pointed at Google's server. None pointed at Playwright holding the wrong handle.

The thing that broke the loop was a **user-provided screenshot** showing Chrome open with an empty URL bar, while the worker log for the same run claimed `On Flow homepage: https://labs.google/fx/tools/flow`. That mismatch — "log says URL X, monitor shows URL Y" — is the signature diagnostic of a wrong-page attachment bug.

---

## The debug iteration log

### Step 0: state when this session picked up the thread

The prior session had landed commits `0f09551` → `52b12f9`. Live-verify had produced two completions (`baceb286`, `34c073be`) and a partial success pattern that looked like ~20% A/B luck. The user then submitted a batch of 10 jobs to gauge real-world stability. 8/10 failed at the "+ New project" step.

### Step 1: added storage-reset + /fx bounce (commit `7f6ac29`)

Root-cause theory: the marketing-landing A/B assignment persists via `localStorage` / `sessionStorage`, and `page.reload()` doesn't touch those. Remedy: wipe client storage and re-navigate through `labs.google/fx` to force a fresh roll.

Unit-tested, green. Ran one live job (`34c073be`) → completed. Declared "fix works" on a single sample.

### Step 2: user called out the 1-sample claim

> "làm ăn vô trách nhiệm thế? t bảo sao"
>
> (*"Irresponsible — I told you so"*)

Queued 8 more jobs. 2 failed immediately even though the storage-reset path fired correctly. Storage-reset isn't the fix.

### Step 3: user pointed at the memory

> "thế mày có thấy trong memory có ghi rõ mkt bypass k"
>
> (*"Didn't you see the memory file literally spells out the marketing bypass?"*)

The memory file `feedback_flow_marketing_landing_bypass.md` prescribes "click + settle + proceed — never bail on URL state." The helper had drifted: `is_marketing_anchor_url()` was being used as an **early abandon** inside the click-poll loop, killing CTA candidates the moment React scroll-navigated to `#partners` before the SPA route completed. Fixed in `52b12f9` — don't abandon on hash change; wait the full per-click timeout; poll `is_ready` only.

Tests green. Queued more jobs. **Still failing.** Same symptom, new layer peeled off.

### Step 4: user's screenshot

User shared a screenshot of the worker's Chrome window: open, focused, **URL bar empty**. No `labs.google/...`, no `#capabilities`, nothing. Just a blank Chrome new-tab page.

> "bản thân mày k load được cái url ra hồn nên mới lỗi, mày thấy ảnh chụp tao gửi k, url có điền cái j đâu mà mày dám nói là do google?"
>
> (*"You yourself can't even load the URL — the URL bar is empty in the picture, how can you blame Google?"*)

The worker log claimed it navigated to Flow, detected the marketing landing, clicked the CTA. The user's screen said Chrome never went anywhere. Two irreconcilable facts from the same process, same minute.

### Step 5: added the diagnostic

Added page enumeration to `_start_cdp`:

```python
for ci, ctx in enumerate(self.browser.contexts):
    for pi, pg in enumerate(ctx.pages):
        logger.info("CDP attach: ctx[%d].pages[%d] url=%r", ci, pi, pg.url)
```

Ran one job. Log output:

```
CDP attach: ctx[0].pages[0] url='chrome://omnibox-popup.top-chrome/'
CDP attach: ctx[0].pages[1] url='chrome://omnibox-popup.top-chrome/omnibox_popup_aim.html'
CDP attach: ctx[0].pages[2] url='chrome://new-tab-page/'
CDP primary page picked: url='chrome://omnibox-popup.top-chrome/' (of 3 page(s) in context)
```

**`pages[0]` is the URL-bar autocomplete dropdown.** `pages[1]` is the AIM variant of the same dropdown. `pages[2]` is the real new-tab. Chrome 147 exposes top-chrome WebUI surfaces through CDP alongside real tabs. The code's `self.page = pages[0]` had Playwright steering the dropdown.

Why didn't this blow up with a proper error? Because `chrome://` pages accept `page.goto()`. They won't render the target URL, but they don't refuse the command. `page.url` reports the new URL. To any log-only observer, navigation looks fine. Only the human looking at the monitor sees the blank real tab that Playwright isn't driving.

### Step 6: the actual fix (commit `b7843b8`)

One filter in `_start_cdp`:

```python
def _is_real_tab(p) -> bool:
    u = (p.url or "").lower()
    if u.startswith("chrome://omnibox"):
        return False
    if u.startswith("chrome://tab-search"):
        return False
    if u.startswith("devtools://"):
        return False
    return True

real_tabs = [p for p in pages if _is_real_tab(p)]
if real_tabs:
    self.page = real_tabs[0]
elif pages:
    self.page = await self.context.new_page()
else:
    self.page = await self.context.new_page()
```

The enumeration log stays — cheap insurance for the next Chrome version that exposes a new WebUI as a "page".

### Step 7: live-verify

Three back-to-back L1 text-to-video jobs on `ngoctuandt20`:

| Job | Claimed | Completed | media_id | Primary picked |
|---|---|---|---|---|
| `b46ecde6` | 20:38:25 | 20:41:24 | `73320d32` | `chrome://new-tab-page/` (real_tabs=1/3) |
| `5c595810` | 20:41:45 | 20:44:48 | `65f99639` | `chrome://new-tab-page/` (real_tabs=1/3) |
| `1b92a6f9` | 20:44:48 | 20:48:03 | `01f292b1` | `chrome://new-tab-page/` (real_tabs=1/3) |

3/3. Each ~3 min, 1080p download, `project_url` and `media_id` populated. No "Failed to find '+ New project' button" anywhere post-fix.

### Step 8: revert the misdirected A/B layers (commit `b7a2620`)

Dropped:
- `localStorage.clear()` + `sessionStorage.clear()` in `dismiss_flow_marketing_landing`.
- `goto('labs.google/fx')` + `goto('labs.google/fx/tools/flow')` bounce.
- Post-bounce re-run of the candidate loop.
- `test_storage_reset_bounce_runs_after_reload_retries_exhaust` (covered dead code).

Kept (each on evidence):
- `force=True` on the CTA click — header intercept was real; the Playwright error message literally named `<a href="#partners"> from <header> subtree intercepts pointer events`.
- Single-pass candidate loop with full-timeout poll, no URL-hash abandon — memory-prescribed.
- `page.reload()` safety net (`max_reloads=2`) — cheap, harmless, and a real user would F5 too.

Tests: `pytest tests/test_landing_dismiss.py` → 15/15 green after revert.

### Step 9: resubmit the 23 previously-failed jobs

All 23 failed `text-to-video` jobs resubmitted as fresh pending jobs, same prompt / model / aspect / profile. Worker (1 concurrent, `ngoctuandt20`) is draining the queue. ETA ~70 min.

---

## The backup tag

Everything that was deleted can be brought back:

```bash
git tag -l "backup/*" -n1
# backup/ab-layers-pre-revert   Pre-revert snapshot: all A/B-layer code intact ...

# Restore the A/B layers to the working tree:
git checkout backup/ab-layers-pre-revert -- flow/landing.py tests/test_landing_dismiss.py

# Or cherry-pick the individual storage-reset commit:
git cherry-pick 7f6ac29
```

Kept because if the marketing-landing A/B ever turns out to be real and not a confounding variable of the CDP bug, we want the code ready.

---

## Memory updates

- **New:** `feedback_cdp_skip_chrome_internal_pages.md` — full gotcha with symptom / cause / fix / cross-links.
- **Indexed in:** `MEMORY.md`.
- **Cross-linked from:** `feedback_flow_marketing_landing_bypass.md` (the memory that memorialized the misdiagnosis).

---

## Lessons

1. **Log-only debugging has a ceiling.** When the model of what's happening doesn't match the user's direct observation, the model is wrong — not the observation. Log → log → log → screenshot is a valid escalation ladder; it was the screenshot that broke this.
2. **`page.url` is what Playwright *thinks* is true, not what the user sees.** For any Playwright-driven process, the first question when things go sideways is "which page is `self.page`?" — not "what does `self.page.url` report?"
3. **CDP pages include WebUI surfaces.** The assumption that `browser.contexts[0].pages[*]` is a list of web tabs is false on modern Chrome. Filter before binding.
4. **One-sample "works" is not a fix claim.** The storage-reset "victory" on `34c073be` was pure A/B luck masking the real bug. The `≥2 consecutive` rule the user enforced later would have caught it.
5. **When a theory requires the third epicycle to explain new evidence, throw it out.** "A/B stickiness" → "…persists across reload" → "…persists via localStorage" → "…persists despite storage-reset" is the pattern of a wrong root cause being defended, not diagnosed.

---

## Files touched this session

- `flow/client.py` — added page enumeration log + `_is_real_tab` filter (`b7843b8`).
- `flow/landing.py` — reverted storage-reset + /fx bounce (`b7a2620`).
- `tests/test_landing_dismiss.py` — removed `test_storage_reset_bounce_runs_after_reload_retries_exhaust`.
- `C:\Users\Tuan\.claude\projects\D--AI-FlowEngine\memory\feedback_cdp_skip_chrome_internal_pages.md` — new gotcha.
- `C:\Users\Tuan\.claude\projects\D--AI-FlowEngine\memory\MEMORY.md` — indexed the gotcha.
- `docs/session-reports/2026-04-24_cdp-wrong-page-root-cause.md` — this document.

## Commits on branch (this session)

```
b7a2620 revert(landing): drop storage-reset + /fx bounce (misdiagnosis)
b7843b8 fix(client): skip chrome:// internal UI pages when picking primary CDP page
```

Plus tag `backup/ab-layers-pre-revert` at `b7843b8`.
