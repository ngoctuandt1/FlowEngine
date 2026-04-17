# Session Report — `Tier1-R2` Post-B11/B12 live DOM re-validation

---

## 1. Metadata

| Field | Value |
|---|---|
| Task ID | `Tier1-R2` |
| Task type | validation / docs-only |
| Session started | 2026-04-17 |
| Session ended | 2026-04-17 |
| Duration actual | ~20m |
| Duration estimate | ~30m (supervisor prompt "2-3 phút per bug" + report) |
| Worker | Claude Opus 4.7 (Chrome MCP) |
| Branch | `claude/jolly-cannon-9387fe` (worktree) |

---

## 2. Commits landed

```
<R2-COMMIT>  docs(validation): Tier1 R2 — re-verify B11+B12 on live Flow DOM
```

One docs-only commit. Zero `.py` diff.

---

## 3. Files changed

```
docs/session-reports/2026-04-17_Tier1r2_revalidation.md   new       (this file)
docs/SPEC.md                                              +2 / -0   (§D.4 B11+B12 Tier1 R2 verdict lines)
```

Tổng: `2 files` (1 new + 1 minor SPEC append). `FLOW_UI_REFERENCE.md` **untouched** — no discrepancy vs current docs found during probe.

---

## 4. Tests

| Test | Result | Notes |
|---|---|---|
| Live DOM probe via Chrome MCP (`mcp__Claude_in_Chrome__*`) | N/A | validation-only, no pytest |

This is a docs-only re-validation of already-landed fixes. Unit-test coverage is already green from B11 (`ce6683a` — 29/29) and B12 (`78d3e40` — 28/28). No test changes.

Manual verify commands used (executed via MCP `javascript_tool` on tab `1988716788`, project `785d2255-.../edit/f1994aba-...`):

```js
// ==== B11 — Canvas target selector (_base.py:255-267) ====
// Step 1: prove old B2 target would still hit the wrong element
document.querySelector('video');
// → <video src="flow_camera/Dolly_in.mp4"> at (33.6, 135), CSS 105.6×59.8.
// wouldPassOldCheck (width/height ≥ 50): TRUE — the old `< 50` filter
// still does not reject the thumbnail, i.e. pre-B11 code would silently
// drag on Dolly_in.mp4 instead of the preview canvas.

// Step 2: run the exact B11 JS snippet from _base.py:255-267
(() => {
    const canvases = Array.from(document.querySelectorAll('canvas'));
    let best = null;
    for (const c of canvases) {
        const r = c.getBoundingClientRect();
        if (r.width < 300 || r.height < 200) continue;
        const area = r.width * r.height;
        if (!best || area > best.area) {
            best = {left: r.left, top: r.top, width: r.width, height: r.height, area};
        }
    }
    return best;
})();
// → {left: 144.14, top: 162, width: 478.91, height: 269.39, area: 129013.05}
// The 478.91×269.39 preview canvas — exactly what Tier1 R1 §B2 identified
// as the correct target. Pre-B11 (querySelector('video')) would miss.

// Step 3: elementFromPoint at canvas center
document.elementFromPoint(383.59, 296.69);
// → <CANVAS> (sameAsCanvas: true) → pointer-trust model is sound:
// the drag lands on a canvas element, Flow accepts the region.

// Note: 2 canvases at identical rect present in Insert mode (natural 598×336,
// CSS 478.91×269.39, both at (144.14, 162)). B11 selector picks first-by-order
// (tie on area); either is a correct drag target since they stack at same rect.

// ==== B12 — Camera preset verify (camera.py:186-202) ====
// Baseline: NO preset clicked yet
// All 13 preset labels: rgb(255, 255, 255), sum 765, threshold_passes=false

// Click "Dolly in" preset (MCP computer.left_click at (102, 426) — real pointer)
// Then run EXACT verify script from camera.py:186-202:
(direction => {
    const buttons = Array.from(document.querySelectorAll('button'));
    for (const btn of buttons) {
        const labels = btn.querySelectorAll('div');
        for (const lbl of labels) {
            if ((lbl.textContent || '').trim() === direction) {
                const color = getComputedStyle(lbl).color;
                const m = color.match(/rgb\((\d+),\s*(\d+),\s*(\d+)\)/);
                if (!m) return false;
                const sum = (+m[1]) + (+m[2]) + (+m[3]);
                return sum < 400;
            }
        }
    }
    return false;
})("Dolly in");
// → true  (color rgb(48,48,48), sum=144, threshold_passes=true)
// All 5 other motion presets still rgb(255,255,255), sum=765, false.

// Flip test: click "Dolly out" (MCP computer.left_click at (214, 426))
// Re-query both:
//   "Dolly in"  → rgb(255,255,255), sum 765, passes=false  ← correctly flipped
//   "Dolly out" → rgb(48,48,48),    sum 144, passes=true   ← new selection
// Threshold 400 sits cleanly between 144 and 765 — robust classification.
```

---

## 5. SPEC.md update

- [x] §D.4 B2 (FIXED via B11 block): Tier1 R2 verdict line appended (`✅ verified live — canvas 478.91×269.39, elementFromPoint=CANVAS, querySelector('video') still hits Dolly_in thumbnail 105.6×59.8`).
- [x] §D.4 B3 (FIXED via B12 block): Tier1 R2 verdict line appended (`✅ verified live — selected sum=144 vs unselected sum=765, flip test passed`).
- [ ] §D.4 B11 struck-through block: already complete from `ce6683a` — no further edit needed (Tier1 R2 verdict belongs in B2/B3 blocks since those are the originally-affected bug entries).
- [ ] §D.4 B12 struck-through block: same — Tier1 R2 verdict belongs in B3 block.

No strike-through changes; both bugs were already marked FIXED. This commit adds one terminal "Tier1 R2" evidence line under each FIXED block.

Commit hash: `<R2-COMMIT>` — to be replaced by a follow-up `docs:` commit (same pattern as B11 `ce6683a` ← `85e2f45`/`6612215` and B12 `78d3e40` ← `b7ac05b`).

---

## 6. Invariants & rules verified

- [x] INV-1 Account Binding — n/a (no profile/worker code touched)
- [x] INV-2 Navigate by `edit_url` — n/a (MCP browsing only)
- [x] INV-3 Store Everything — n/a (no job run)
- [x] INV-4 Serial per Project — n/a
- [x] INV-5 media_id stable — n/a
- [x] R-CODE-3 Locale-Independent — probe used tag + computed style + exact-text match on Material-icon strings + preset direction names; no locale coupling introduced. Preset text ("Dolly in", "Dolly out") remains EN-only per phase-1 stance (documented in FLOW_UI_REFERENCE §Camera Preset Selection & Active State).
- [x] R-CODE-10 No `datetime.utcnow()` — n/a
- [x] R-CC-1 KHÔNG restructure kiến trúc — zero `.py` diff

---

## 7. Issues / Decisions

### Per-bug verdicts

#### **B11 — Bbox canvas target ✅ VERIFIED LIVE ROUND 2**

Code: `flow/operations/_base.py::draw_bbox_on_video` (commit `ce6683a`, `_base.py:215-322`).

| Check | Code behavior | Observed live on `785d2255-…/edit/f1994aba-…` | Verdict |
|---|---|---|---|
| Canvas selector JS (lines 255-267) — filter `width ≥ 300 && height ≥ 200`, pick largest area | returns `{left, top, width, height, area}` of the preview canvas | returned `{left: 144.14, top: 162, width: 478.91, height: 269.39, area: 129013.05}` — the 478.91×269.39 preview canvas | ✅ exact match |
| Canvas count & dimensions | Tier1 R1 documented single 598×336 preview canvas | 2 canvases present (both natural 598×336, CSS 478.91×269.39 at `(144.14, 162)`). Selector picks first-by-order on tie | ✅ verified — both qualify, either is correct drag target |
| `document.querySelector('video')` target (pre-B11 / B2 behavior) | would return the thumbnail-strip `<video>`, not preview | returned `<video src="flow_camera/Dolly_in.mp4">` 105.6×59.8 at `(33.6, 135)` | ✅ matches Tier1 R1 ground truth — fix is load-bearing |
| Old B2 `width/height < 50` filter | Does NOT reject the 105.6×59.8 thumbnail (both dims ≥ 50) | `wouldPassOldCheck: true` — old code would have silently dragged on Dolly_in.mp4 | ✅ confirms why B11 threshold 300 > 50 is necessary |
| `elementFromPoint` at canvas center | Should return a `<CANVAS>` element (topmost at the drag point) | `{topTag: "CANVAS", sameAsCanvas: true}` at `(383.59, 296.69)` | ✅ pointer-trust model sound |
| `page.evaluate` round-trip count per call | Exactly 1 (canvas-find only, no post-drag verify) | Verified structurally in `_base.py` — no second `page.evaluate` in the helper | ✅ matches test_bbox contract trip-wire |

**B11 status (post-Round-2): ✅ FIXED + VERIFIED LIVE.** Selector chain in `ce6683a` is correctly calibrated. Pointer-trust design holds: the preview canvas is reliably detected, pointer events delivered to it reach a `<CANVAS>` via `elementFromPoint`. No code changes needed.

**Subtle observation (not a bug):** Insert mode mounts 2 canvases at identical rect (likely display + bbox-overlay layer). B11 selector picks the first-by-order on area tie — since both have identical CSS rect, drag coords are the same either way, and Flow handles which layer receives the paint internally. If Flow ever splits the two canvases into different rects in a future release, the `area >` tiebreak would still pick the larger one, and both layers would still get the drag via stacking.

#### **B12 — Camera preset color verify ✅ VERIFIED LIVE ROUND 2**

Code: `flow/operations/camera.py::_verify_preset_selected` (commit `78d3e40`, `camera.py:164-212`).

| Check | Code behavior | Observed live on `785d2255-…/edit/f1994aba-…` | Verdict |
|---|---|---|---|
| Baseline (no preset clicked) | all labels bright `rgb(255,255,255)` sum 765 | 13/13 presets surveyed, all `sum=765, threshold_passes=false` | ✅ exact match |
| After click "Dolly in" (real pointer via MCP) | target label `rgb(48, 48, 48)` sum 144, `threshold_passes: true` | `{color: "rgb(48,48,48)", sum: 144, threshold_passes: true}` | ✅ exact match with spec |
| Other presets post-click (Dolly out/Orbit left/Orbit right/Orbit up/Orbit low) | remain unselected | all 5 remain `sum=765, passes=false` | ✅ no false positives |
| Threshold `sum < 400` | splits selected (144) from unselected (765) — 256 margin each side | confirmed | ✅ robust |
| Flip test — click "Dolly out" next | "Dolly in" flips to unselected, "Dolly out" becomes selected | `Dolly in: sum=765, passes=false` ← flipped; `Dolly out: sum=144, passes=true` ← new selection | ✅ state flip works |
| Exact JS snippet from `camera.py:186-202` | runs `document.querySelectorAll('button')` → descendant DIV text match → `getComputedStyle(lbl).color` → rgb regex → sum threshold | identical result when executed verbatim via MCP | ✅ contract intact |

**B12 status (post-Round-2): ✅ FIXED + VERIFIED LIVE.** The color-based verify signal from `78d3e40` behaves exactly as specified against production Flow DOM. Threshold 400 remains well-centered. No code changes needed.

### Quyết định đã đưa (judgment calls)

- **Used the existing `785d2255-…` project** the user opened (same project as Tier1 R1 — L1 video `f1994aba-…`). Did NOT create a new project or generate.
- **Clicked Insert + 2 Camera presets** (Dolly in, Dolly out) — all with real pointer events via MCP, none followed by submit. Exit via back-arrow (same as Tier1 R1 convention). Zero credits consumed.
- **Chose Dolly in + Dolly out** (motion tab) over Low/High (position tab) because Camera motion tab is active by default — no tab switch needed, faster probe. "Low" was used in Tier1 R1; switching presets is sufficient to prove the flip behavior.
- **Did NOT update `FLOW_UI_REFERENCE.md`** — every observation matched the current doc text (canvas 598×336 natural / ~479×269 CSS at (144.1, 162); selected color `rgb(48,48,48)` sum 144; unselected `rgb(255,255,255)` sum 765; threshold 400). No discrepancy → no edit. File whitelist permits updates but only if live evidence contradicts docs; it does not.
- **Did NOT strike-through §D.4 B11/B12** — both are already struck-through with resolution blocks from `ce6683a`/`78d3e40`. Tier1 R2 verdict belongs in the adjacent B2/B3 FIXED-via blocks since those are the originally-affected bug entries that now carry verification status across three passes (R1-fix → R1-mismatch → R2-verified).

### Bug candidates phát hiện NHƯNG KHÔNG fix (out of scope)

- **None.** Live DOM probe showed zero discrepancies with code. All selectors land where the code expects.

---

## 8. Handoff notes

### Workdir state

- Branch: `claude/jolly-cannon-9387fe` (worktree `D:\AI\FlowEngine\.claude\worktrees\jolly-cannon-9387fe`).
- `stash@{0}` — **preserved untouched** (`WIP: flow refinements — direct edit-url nav, chip re-click model close, verify extend panel, iterate enabled submit buttons`). Verified before + after.
- `git status` post-session: `docs/session-reports/2026-04-17_Tier1r2_revalidation.md` (new) + `docs/SPEC.md` (§D.4 append). `?? .claude/settings.local.json` untracked (worktree-local MCP allowlist; not committed per FILE BLACKLIST).
- Flow account state after probe:
  - Project `785d2255-…` — **unchanged** (no new generation, no submit). Entered edit mode → Insert → Camera (Dolly in → Dolly out) → back-arrow exit. All clicks were preset/mode toggles; no Create button clicked.
  - Empty shell project `576dad86-…` from Tier1 R1 — still present on homepage; user can delete.

### Recommendation

**Tag `v0.2.0-phase-a`.** Both P0 Tier1 blockers (B11 bbox canvas, B12 camera verify) verified on live DOM round 2, confirming the code fixes work against production Flow without needing Tier 2 credits. Phase A is complete:

- B1 ✅ (b359c84, MCP-verified R1)
- B2 ✅ via B11 (ce6683a, MCP-verified R2)
- B3 ✅ via B12 (78d3e40, MCP-verified R2)
- B5 ✅ (4d24c10)
- B6 ✅ (0118e6d)
- B7 ✅
- B8 ✅ (573cffd)
- B9 ✅ (adca116)
- B10 (P2, deferred — `default_factory=datetime.utcnow`)
- B11 ✅ (ce6683a, R2-verified)
- B12 ✅ (78d3e40, R2-verified)
- B13 ✅ (resolved inline with Tier1 R1)

No B-followup opened from Round 2. Tier 2 (real submit E2E) remains optional — not required for tag since selector contracts now have evidence on both unit-test and live-DOM tiers.

### Env / tooling notes for next executor

- Chrome MCP tab group created fresh this session (tab `1988716788`). Not reused from Tier1 R1 group `1418771388` — that session had already closed.
- Flow homepage loads with stacked promo dialogs ("Nano Banana 2", "Meet the new Flow", "Veo 3.1 + New Controls") — all dismissed by clicking `close` text-button; takes ~3 iterations to clear all.
- Project cards only appear AFTER all dialogs are dismissed — initial `a[href*="/project/"]` query returns 0 while any dialog is open. Lesson: scripted workflows may need a dialog-dismissal loop before enumerating projects.
- Insert mode mounts **2 canvases at identical rect** (not 1 as Tier1 R1 documented). Both qualify under B11 selector; first-by-order wins the area tie. Non-issue for drag correctness — the stacking canvases share coords.
- Camera motion tab is **active by default** on entering Camera mode — no tab click needed to click motion presets. Position tab presets have `width/height = 0` until the tab is activated.

---

## 9. Done criteria checklist

Từ supervisor prompt `[DONE CRITERIA]`:

- [x] B11 verdict: ✅ VERIFIED LIVE (evidence: canvas 478.91×269.39 selected by B11 JS, elementFromPoint=CANVAS, `querySelector('video')` still hits Dolly_in thumbnail 105.6×59.8 as predicted)
- [x] B12 verdict: ✅ VERIFIED LIVE (evidence: selected "Dolly in" sum=144 passes=true, 5 unselected presets sum=765 passes=false, flip test to "Dolly out" flipped both correctly)
- [x] SPEC §D.4 updated — Tier1 R2 verdict lines appended to B2 FIXED-via-B11 and B3 FIXED-via-B12 blocks
- [x] Report 9 sections (this file)
- [x] Zero `.py` diff
- [x] Không submit job — no Create/Generate click across any probe; zero credits burned
- [x] `stash@{0}` preserved (verified before + after)

---

_Sign-off: ✅ Tier1 R2 DONE — commit `<R2-COMMIT>`, report `docs/session-reports/2026-04-17_Tier1r2_revalidation.md`. Both P0 post-fix re-validations passed on live Flow DOM. Recommend supervisor tag `v0.2.0-phase-a` — no Tier 2 E2E required (code contracts have evidence at both unit-test and live-DOM tiers)._
