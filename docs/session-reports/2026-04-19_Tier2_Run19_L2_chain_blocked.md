---
date: 2026-04-19
epic: Phase B — multi-level chains (Tier 2 live)
run: Run 19 (L2 extend chain, on top of Run 18 stable L1 baseline)
status: BLOCKED — profile auth not established; handoff to new session
author: Claude (Opus 4.7)
---

# Tier 2 Run 19 — L2 chain BLOCKED by profile authentication

## 1. Goal

Extend Run 18's stable L1 t2v baseline (3/3 pass) to L2 chain operations:
submit one `extend-video` under parent L1, verify it produces
`extend_1080p_*.mp4` end-to-end with B38 UI-driven upscale.

## 2. Chain under test

- L1 `4a032d83` text-to-video — **COMPLETED** (ngoctuandt20, chain `1871a218`)
- project_url: `https://labs.google/fx/tools/flow/project/fb9728e5-a5f4-4bb5-8579-df258dd8969f`
- parent media_id: `df78a409-9a01-4594-a47c-948e2bef71d3`

## 3. L2 attempts — all FAILED

| ID        | Type         | Status  | Error                                  | Root cause                         |
|-----------|--------------|---------|----------------------------------------|------------------------------------|
| `7969b8de`| extend-video | failed  | `extend-video failed: POLICY`          | FALSE POSITIVE (regex too broad)   |
| `27051d66`| extend-video | failed  | `extend-video failed: POLICY`          | FALSE POSITIVE (same)              |
| `582761dc`| camera-move  | failed  | `Failed to find Camera button`         | `/edit/` loaded landing, not editor|
| `7c41a24a`| extend-video | claimed | (silent, killed mid-run)               | same as above — silent hang        |

## 4. Diagnosis — two distinct bugs uncovered

### 4.1 POLICY false positive (wait.py regex, FIXED in working tree)

Prior observer regex `/policy|violated/i` matched Flow footer links like
"Privacy Policy" on **every** page load. Any time the observer ran on a
URL serving the marketing landing (see §4.2), the regex fired before
generation even started → emitted `POLICY` error at `progress=0s`.

**Fix (uncommitted, `flow/wait.py`):**
- Tightened regex to `/content\s+policy|policy\s+(violation|violated)|violat(es|ed|ing)\s+.{0,30}\s*polic/i`
- Added `window.__flowErrorSnippet` capture with ±80 char context
- Added screenshot + HTML dump to `logs/error_<label>_<ts>.{png,html}` on every DOM error

### 4.2 Profile auth not established (ROOT BLOCKER)

User screenshot evidence: navigating to `/edit/df78a409-...` displayed
the Flow **marketing landing page** ("Where the next wave of storytelling
happens" / "Create with Flow") instead of the editor SPA.

Flow serves the marketing splash to **unauthenticated** visitors at deep
links. Worker profile `chrome-profiles/ngoctuandt20/` apparently lacks
Google SSO session cookies — either never signed in, or cookies expired.

This explains BOTH remaining symptoms:
- camera-move → "Failed to find Camera button" (no editor DOM at all)
- extend-video → silent hang (observer finds no progress indicators)

Raw string `POLICY` logs were masking the real issue. No screenshot was
being captured at failure → couldn't see that the page was rendering
marketing splash. User demanded screenshot-on-error; fix landed but
can't be validated until auth blocker cleared.

## 5. Remediation attempt — warm profile via Gmail login

**Strategy:** open Playwright Chrome against `chrome-profiles/ngoctuandt20`,
navigate to `mail.google.com`, let user sign in manually, close window.
Cookies persist; subsequent worker launches inherit the session.

**Script:** `scripts/warm_profile.py` (untracked, new)

```python
ctx = await p.chromium.launch_persistent_context(
    user_data_dir=str(base.resolve()),
    headless=False,
    args=["--no-first-run", "--no-default-browser-check"],
    viewport={"width": 1280, "height": 800},
)
page = ctx.pages[0] if ctx.pages else await ctx.new_page()
await page.goto("https://mail.google.com", wait_until="domcontentloaded")
await ctx.wait_for_event("close", timeout=0)
```

**Outcome:** FAILED twice with `TargetClosedError`. Chrome PID launched
(e.g. 41340, 53200), Playwright immediately issued `<gracefully close start>`
→ `<kill>` → `exitCode=2147483651` (0x80000003, breakpoint / access violation).

Cleanup attempted:
- `taskkill //F //IM chrome.exe` — killed 19 ghost Chromes left over from
  earlier worker runs (`python.exe` kill doesn't cascade to Chrome child).
- `Singleton*` lock files — none present in profile dir.
- Retry still failed with same error.

Hypothesis: profile dir has corrupted state from repeated
FlowClient clone/launch cycles, or the Playwright Chromium binary (1200)
has a compatibility issue with this specific user_data_dir. Needs further
diagnosis in fresh session.

## 6. Uncommitted changes (main repo `D:/AI/FlowEngine`, branch master)

```
modified:   flow/download.py   # B38 UI-driven upscale primary path (not yet committed)
modified:   flow/login.py      # stuck-detection + reload (feedback_login_stuck_reload.md)
modified:   flow/wait.py       # tightened POLICY regex + screenshot/HTML dump
untracked:  flow/upscale.py    # UI upscale module (referenced by download.py)
untracked:  scripts/warm_profile.py  # ops helper for profile bootstrap
```

These should be reviewed, tested, and committed once the L2 path is proven
end-to-end.

## 7. Next session — concrete steps

1. Kill all `chrome.exe` processes (worktrees spawn ghosts).
2. Diagnose `warm_profile.py` `TargetClosedError`:
   - Try a **fresh profile dir** (`chrome-profiles/warm-test`) to isolate
     whether the crash is profile-specific or general.
   - If fresh dir launches fine → corrupt state in `ngoctuandt20`; wipe
     cache/ but preserve Cookies + Login Data, retry.
   - If fresh dir also crashes → Playwright / Chromium 1200 issue; try
     passing `--remote-debugging-port=0` instead of pipe, or drop to
     Chromium 1199.
3. Once warm Chrome stays up: user signs into Google, closes window.
4. Launch worker: `WORKER_PROFILES=ngoctuandt20 python run_worker.py`
5. Post NEW L2 extend via API (old jobs `7c41a24a`/etc. are DB-locked,
   use fresh job under chain `1871a218` with parent `4a032d83`).
6. Watch for `extend_1080p_*.mp4` in `downloads/`.
7. If DOM error fires: check `logs/error_*.{png,html}` dump — should now
   show actual page state (landing vs editor vs policy banner).

## 8. Open questions

- Is `ngoctuandt20` profile actually logged in, or were prior successful
  runs using cached JWT that has since expired?
- Does Flow's SPA need an additional warmup beyond Gmail cookies
  (e.g. first-visit `labs.google/fx` to accept tools ToS)?
- Would switching from clone-to-temp (default) to direct profile use
  (`FLOW_USE_BASE_PROFILE=1`) preserve the session better?

## 9. Artifacts

- Worker log (L2d silent hang): `D:/AI/FlowEngine/logs/worker-run19-*.log`
- DB snapshot queried 2026-04-19T16:25 (see §3 table)
- Screenshot from user (not committed): showed marketing landing at
  `/edit/df78a409...` URL
- Playwright call log: `tasks/bhslwpqgh.output` + `tasks/bsaq1nyuk.output`
  (both show identical `TargetClosedError` pattern)
