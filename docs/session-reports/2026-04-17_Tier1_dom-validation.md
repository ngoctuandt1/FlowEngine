# Session Report — `Tier1_dom-validation` Live DOM validation of B1/B2/B3 selectors

---

## 1. Metadata

| Field | Value |
|---|---|
| Task ID | `Tier1-dom-validation` |
| Task type | validation / docs-only |
| Session started | 2026-04-17 20:20 |
| Session ended | 2026-04-17 22:10 |
| Duration actual | `110m` (45m first pass BLOCKED + 65m retest on live L1 project) |
| Duration estimate | n/a (Tier 1 Phase A E2E) |
| Worker | Claude Opus 4.7 (Chrome MCP) |
| Branch | `claude/upbeat-mestorf-9dbd52` |

---

## 2. Commits landed

```
9a033dd  docs(validation): verify B1/B2/B3 selectors on live Flow DOM (Tier1 E2E)   [first pass — B1 ✅, B2/B3 BLOCKED]
<pending> docs(validation): B2+B3 retest on live L1 project — both ❌ SELECTOR MISMATCH  [this commit]
```

Two docs-only commits total. Zero `.py` diff across both.

---

## 3. Files changed

**Committed (across both commits):**

```
docs/session-reports/2026-04-17_Tier1_dom-validation.md   new + revised   (this file)
docs/SPEC.md                                              +3 / -3         (B1 Tier1 note + B2/B3 MISMATCH flags)
docs/FLOW_UI_REFERENCE.md                                 ~30 / ~10       (B2 canvas-painted evidence, B3 verify-signal
                                                                           ground truth, all "Known unknowns" resolved)
```

**Local-only (NOT committed, per FILE BLACKLIST `.claude/*`):**

```
.claude/settings.local.json                               expanded         (worktree-scoped MCP tool allowlist;
                                                                             kept untracked so commit stays docs-only)
```

Tổng committed across both commits: `3 files` with documented updates (report body + SPEC flags + REFERENCE ground-truth).

---

## 4. Tests

| Test | Result | Notes |
|---|---|---|
| Live DOM inspection via Chrome MCP (`mcp__Claude_in_Chrome__*`) | N/A | validation-only, no pytest needed |

Manual verify commands used (non-shell; executed via MCP `javascript_tool` on tab `1988716765`):

```js
// ==== B1 — aspect ratio chip + menu (first pass) ====
Array.from(document.querySelectorAll('button[aria-haspopup="menu"]'))
  .find(b => /video[\s\S]*x\d/i.test(b.innerText));
// → chip id="radix-:r1k:", text "Video\ncrop_16_9\nx1", data-state="closed"

// After MCP computer.left_click on chip center (594, 608):
document.querySelectorAll('[role="menu"][data-state="open"]');
// → 1 menu; triggers: [id$="-trigger-VIDEO"] active, [id$="-trigger-LANDSCAPE"] active, [id$="-trigger-PORTRAIT"] inactive

// After click PORTRAIT + click (10,10) outside:
document.getElementById('radix-:r1k:').innerText;
// → "Video\ncrop_9_16\nx1"   ← state transition verified

// ==== B2 — bbox overlay (retest on project 785d2255-...) ====
// Main video display identity check
document.querySelector('video');
// → <video> 105×60 thumbnail in card strip (NOT the main preview)

document.querySelector('canvas');
// → <canvas width="598" height="336"> at CSS rect (144.1, 162, 478.9, 269.4) — THIS is the main preview

// After MCP left_click_drag from (240,220)→(520,370) inside canvas region,
// a dashed rectangle bbox is visually painted on the canvas (see screenshot).
// Union selector match attempt:
document.querySelectorAll('svg rect, [class*="bbox" i], [class*="selection" i], [class*="region" i], [class*="mask" i]').length;
// → 0   ← defensive union selector finds ZERO matches

// Hit test at 3 points inside the visible bbox:
[[350,280], [420,300], [480,340]].map(([x,y]) => document.elementFromPoint(x,y).tagName);
// → ["CANVAS", "CANVAS", "CANVAS"]   ← bbox is painted onto canvas bitmap, NOT a DOM element

// ==== B3 — camera preset + verify (retest) ====
// aria-label strategy (camera.py strategy #1)
["Dolly in","Dolly out","Orbit left","Orbit right","Orbit up","Orbit low","Center","Left","Right","High","Low","Closer","Further"]
  .map(n => [n, !!document.querySelector(`[aria-label="${n}"]`)]);
// → all 13/13 return false   ← aria-label strategy misses EVERY preset

// role=button strict attribute strategy (camera.py strategy #2, as CSS)
document.querySelectorAll('[role="button"]').length;
// → 0   ← NO element in the entire document has explicit role="button" attribute

// Exact-text strategy (#3) — count matches
Array.from(document.querySelectorAll('button'))
  .filter(b => b.textContent.trim() === "Dolly in").length;
// → 1   ← textContent exact match works

// Partial-hazard proof (direction "Dolly in" vs "Dolly in zoom out"):
Array.from(document.querySelectorAll('button'))
  .filter(b => b.textContent.trim() === "Dolly in zoom out").length;
// → 1   ← distinct; exact=True correctly separates

// After MCP left_click on Low preset (real pointer), verify step probes:
const low = [...document.querySelectorAll('button')].find(b => b.textContent.trim() === "Low");
[low.getAttribute('aria-pressed'), low.getAttribute('aria-selected'),
 /active|selected|pressed/i.test(low.className),
 /active|selected/i.test(low.parentElement.className)];
// → [null, null, false, false]   ← ALL 4 verify signals miss

// Only detectable selection signal: label DIV computed color
const label = [...low.querySelectorAll('div')].find(d => d.textContent.trim() === "Low");
getComputedStyle(label).color;
// → "rgb(48, 48, 48)"   (selected)   vs   "rgb(255, 255, 255)"   (unselected siblings)
```

---

## 5. SPEC.md update

- [x] §D.4 B1 — appended Tier1 MCP verification note to commit reference (commit `9a033dd`)
- [x] §D.4 B2 — flagged ❌ SELECTOR MISMATCH: bbox is canvas-painted (not DOM), current union selector unreachable (this commit)
- [x] §D.4 B3 — flagged ❌ SELECTOR MISMATCH: all 4 verify signals miss on live DOM, strategies 1+2 find 0 elements (this commit)

See §7 "Followup B-tickets" for required code fixes (B11, B12).

---

## 6. Invariants & rules verified

- [x] INV-1 Account Binding — không chạm worker/profile code; chỉ browse
- [x] INV-2 Navigate by `edit_url` — không scan DOM card; validation-only
- [x] INV-3 Store Everything — n/a (no job run)
- [x] INV-4 Serial per Project — n/a (no worker)
- [x] INV-5 media_id stable — n/a (no L2 run)
- [x] **R-CODE-3 Locale-Independent** — ✅ confirmed: chip detection regex `/video[\s\S]*x\d/i` matches `"Video\ncrop_16_9\nx1"` (EN) without hardcoding locale; the `crop_9_16`/`crop_16_9` icon substrings are Material icon names (ALWAYS English regardless of UI locale), NOT translated text. Tab trigger attribute selectors `[id$="-trigger-VIDEO|LANDSCAPE|PORTRAIT"]` are also locale-agnostic (Radix internal enum suffixes, not user-facing labels).
- [x] R-CODE-10 No `datetime.utcnow()` — n/a
- [x] R-CC-1 KHÔNG restructure kiến trúc — zero `.py` diff

---

## 7. Issues / Decisions

### Per-bug verdicts

#### **B1 — Aspect ratio ✅ VERIFIED LIVE**

Every selector in `flow/operations/generate.py::_set_aspect_ratio` (commit `b359c84`) + its docs in `FLOW_UI_REFERENCE.md` §Aspect Ratio UI matches production DOM exactly.

| Check | Spec says | Observed live | Verdict |
|---|---|---|---|
| Chip locator | `button[aria-haspopup="menu"]` w/ text `/video.*x\d/i` | Found chip `id="radix-:r1k:"`, text `"Video\ncrop_16_9\nx1"`, `data-state="closed"` | ✅ |
| Chip opens menu | `data-state → "open"` + `[role="menu"][data-state="open"]` mounts | After MCP `computer.left_click`, chip `data-state="open"`; 1 `[role="menu"]` with `data-state="open"` (id `radix-:r1l:`) | ✅ |
| Media type tab | `[id$="-trigger-VIDEO"]` | Found, `data-state="active"`, text `"videocam\nVideo"` | ✅ |
| Aspect tab — LANDSCAPE | `[id$="-trigger-LANDSCAPE"]` | Found, `data-state="active"`, text `"crop_16_9\n16:9"` | ✅ |
| Aspect tab — PORTRAIT | `[id$="-trigger-PORTRAIT"]` | Found, `data-state="inactive"` → after click `data-state="active"` + `aria-selected="true"` | ✅ |
| Active transition | `data-state` flips on click | LANDSCAPE became `"inactive"`, PORTRAIT became `"active"` after MCP real click | ✅ |
| Menu close via outside click | Click `(10,10)` closes menu, does NOT close composer | 0 open menus after click; project URL unchanged; composer still mounted | ✅ |
| Chip text reflects choice | `crop_16_9` → `crop_9_16` | Before: `"Video\ncrop_16_9\nx1"`; after PORTRAIT+close: `"Video\ncrop_9_16\nx1"` | ✅ |
| Radix id prefix variance | Warning in docs: never hardcode `radix-:rXX:` | Observed two different hash prefixes co-existing: chip trigger uses `radix-:r1r:-trigger-VIDEO`, aspect group uses `radix-:r21:-trigger-LANDSCAPE|PORTRAIT` in the SAME rendered menu. Attribute-ends-with selector `[id$="-trigger-XXX"]` handles both correctly | ✅ warning in docs is still necessary |
| JS `.click()` works on Radix | Docs warn it doesn't | Confirmed: `chip.click()` alone kept `data-state="closed"` (menu never opened). Real pointer via MCP `computer.left_click` on chip center required | ✅ warning in docs is correct |
| Locale-independence | R-CODE-3 | EN profile; chip regex uses pattern, not literal. Material icon names (`crop_16_9`) are always English. Tab attribute-suffix `-trigger-VIDEO|LANDSCAPE|PORTRAIT` is Radix enum, not user-facing text | ✅ |

**B1 status:** defensive chain in code (b359c84) is correctly calibrated. No code changes needed.

#### **B2 — Bbox overlay ❌ SELECTOR MISMATCH (live retest on project `785d2255-...`)**

After the first-pass BLOCKED verdict, the user opened a project with an existing L1 video (`785d2255-c9e4-4bc6-b4e6-8c2fdf8825d0/edit/f1994aba-fd2d-49be-b1d8-780aaf2e5663`) and asked for retest ("mở sẵn proj có video r đó, test lại chỗ miss đi"). Retest produced concrete evidence that **the current selector chain in `flow/operations/_base.py::draw_bbox_on_video` (commit `a165105`) cannot reach Flow's production DOM**. Two independent mismatches:

**Mismatch 1 — wrong target element for drag:**

| Check | Code assumes | Live DOM reality |
|---|---|---|
| Line `_base.py:236`: `const video = document.querySelector('video')` | `<video>` = main preview element | `document.querySelector('video')` returns a **105×60 thumbnail** in the card strip, NOT the preview. The main preview is a `<canvas width="598" height="336">` at CSS rect (144.1, 162, 478.9, 269.4). |
| Drag coords | derived from video rect | would drag over a tiny thumbnail off to the side — outside the canvas where the user wanted to draw |

Runtime effect: the drag happens at wrong screen coords (thumbnail strip coords, not preview coords). The canvas never receives the mouse events. Flow falls back to default region silently. User's bbox input is ignored.

**Mismatch 2 — bbox is canvas-painted, not DOM:**

After forcing a drag on the actual canvas via MCP `left_click_drag` from (240,220)→(520,370), a dashed-rectangle bbox IS visually rendered (screenshot confirmed — gray fill, dashed stroke, ~220×150 px). Probed with `elementFromPoint` at three locations inside the visible bbox: all three returned the **same `<CANVAS>` element**. The bbox is **painted onto the canvas bitmap via 2D context draw calls**, not rendered as a DOM overlay. Consequence: the union selector `svg rect, [class*="bbox" i], [class*="selection" i], [class*="region" i], [class*="mask" i]` returns **zero matches** — it is fundamentally incapable of detecting a canvas-painted shape.

Both mismatches mean the current `draw_bbox_on_video` body is broken end-to-end on Flow's production DOM:
1. Wrong element → drag misses canvas → no visible bbox.
2. Even if the drag landed, there would be no DOM element to detect the result.

**What is still useful:**
- Range validation (`x/y/w/h ∈ [0,1]`), overflow clamping (`x+w>1 → w=1-x`), and the mouse-drag interpolation logic are correct — they just operate on the wrong coordinate basis.
- Fallback is safe in the "silent-degraded" sense — function returns False, caller logs WARNING, Flow uses default region — but that is no longer "verified degraded behavior", it is "the bbox feature never worked in production".
- Unit tests in `tests/test_bbox.py` (commit `a165105`) still pass because they stub `page.evaluate` — they do not exercise live DOM and therefore missed this.

**Followup ticket required** — see "Followup B-tickets" subsection below.

#### **B3 — Camera preset ❌ SELECTOR MISMATCH (live retest — MULTIPLE signals fail)**

Same live project, Camera mode opened via `left_click (763, 760)`. All selectors from `flow/operations/camera.py::_click_preset` + `_verify_preset_selected` (commit `58937d4`) probed against the real preset DOM. Result: **2 of 3 click strategies find zero elements, and all 4 verify signals return false even when the click succeeds**. The combination means `camera_move` will **raise `RuntimeError("Failed to find camera preset: <direction>")` on every call** against current Flow DOM.

**Click strategy breakdown (preset targeting):**

| Strategy | Selector | Live DOM result | Verdict |
|---|---|---|---|
| 1 | `[aria-label='<direction>']` (e.g. `[aria-label='Dolly in']`) | `document.querySelector('[aria-label="Dolly in"]')` = `null` — tested all 15 presets across both tabs, **0/15** have any `aria-label` attribute | ❌ 0 hits |
| 2 | `[role='button']` CSS (strict attr), filtered `^<direction>$` | `document.querySelectorAll('[role="button"]').length` = **0** across entire document — no element has explicit `role="button"` (Playwright's CSS selector does NOT match implicit roles; only `get_by_role('button')` would). Presets ARE `<BUTTON>` tags, but the CSS selector misses them. | ❌ 0 hits |
| 3 | `page.get_by_text(direction, exact=True)` | 2 matches per preset (the `<BUTTON>` container + an inner `<DIV>` label). `.first` picks BUTTON (document order). Click fires real pointer event → preset IS selected (preview video plays the motion, label color flips, submit arrow enables). | ✅ click works |

**Verify signal breakdown (post-click state detection):**

After confirmed successful pointer-click on "Low" preset (Flow internal state updated, submit enabled), checked all 4 union signals from `_verify_preset_selected`:

| Signal | Observed on selected "Low" button |
|---|---|
| `aria-pressed="true"` | `null` — attribute absent |
| `aria-selected="true"` | `null` — attribute absent |
| className matches `/active\|selected\|pressed/i` | className = `"sc-16c4830a-1 hxjMEo sc-e7a64add-0 sc-e7a64add-1 gdoOJp cqfBcP sc-2384ceab-3 byyZkY"` — **no keyword** (pure styled-components hashes) |
| parent className matches `/active\|selected/i` | parent className = `"sc-2384ceab-7 jrdoRH"` — **no keyword** |

All 4 return false → `_verify_preset_selected` returns `False` → strategy 3 logs "clicked but not verified; falling through" → no more strategies → `_click_preset` logs ERROR and returns False → outer `camera_move` raises `RuntimeError`. **Every camera-move job fails**, even though Flow has already accepted and applied the preset.

**Where the REAL state marker lives (discovered but NOT in current verify chain):**

| Marker | Selected state | Unselected state | Stability |
|---|---|---|---|
| Inner label DIV class (inside BUTTON, text = direction) | `sc-2384ceab-4 jYmHac` | `sc-2384ceab-4 hkGUbO` | hash names — ⚠ may rotate per Flow release |
| Inner label DIV computed `color` | `rgb(48, 48, 48)` (dim, selected) | `rgb(255, 255, 255)` (bright, unselected) | semantic — ✅ stable across releases (dim-vs-bright is the design intent) |
| Submit arrow `disabled` attribute | `disabled={false}` after any preset click | `disabled={true}` before any click | boolean, not preset-specific (tells us "A selection happened", not which) |
| Video thumbnail `<video src="...">` URL | `flow_camera/Low.mp4` (the `<video>` inside the preset button — NOT the preview canvas) | same src — doesn't change with selection | identifies the preset, doesn't indicate active |

The most robust semantic signal is **computed label `color` flip on the label DIV** — but this requires getComputedStyle, not an attribute selector, and is not what `_verify_preset_selected` checks.

**Partial-hazard check (bonus — what B3 was supposed to defend against):**

| Check | Hazard direction | Result |
|---|---|---|
| `textContent === "Dolly in"` across all buttons in Motion tab | collides with "Dolly in zoom out"? | **Safe** — 1 button matches "Dolly in" exactly; 1 separate button matches "Dolly in zoom out" exactly. Playwright `exact=True` correctly separates them. |
| `textContent === "Low"` across all buttons in Position tab | collides with "Lower" / "Low angle"? | **Safe** — no "Lower" button exists. "Low" returns 1 exact match. |
| `textContent.includes("Low")` | any accidental matches? | Only the "Low" button itself. No hazard. |

So the anti-partial-match design in commit `58937d4` is sound — the hazard it prevents doesn't happen anyway (Flow doesn't render a "Lower" button). The defensive anchoring is harmless but not load-bearing.

**What is still true:**
- Flow's internal selection IS updated by a real pointer-click on the exact-text BUTTON. The click mechanism of strategy 3 works.
- Submit button does not become enabled from a no-op, so the enabled-state transition is a reliable "SOMETHING was selected" global signal (not preset-specific).
- Unit tests in `tests/test_camera.py` pass because they stub `page.evaluate` — they do not reflect live DOM behavior.

**Followup ticket required** — see "Followup B-tickets" subsection below.

### Quyết định đã đưa (judgment calls)

- **Clicked "+ New project" on first pass knowing it creates a shell project on the user's account.** Flow's T2V composer is only reachable after creating a project shell — there is no "preview" composer on the homepage. The shell is empty (zero credit cost until generate is clicked) and can be deleted from the homepage. This was the minimum-footprint path to reach B1's composer DOM.
- **Did NOT click Create/Submit at any point** on either pass. Tier 1 invariant preserved: 0 credits burned across both passes.
- **Retest on live L1 used the user-supplied project `785d2255-...`** — user opened it explicitly and authorized ("mở sẵn proj có video r đó, test lại chỗ miss đi"). No new project created. No generate called. Exit via back arrow `←`, not submit.
- **Did NOT run Playwright as fallback.** Spec: "Tầng 1 thuần MCP".
- **Chrome profile / login worked as-is** — MCP extension delivered an already-logged-in session. No login flow exercised.

### Followup B-tickets (code fixes — NOT done in this docs-only session)

These are the NEW bugs that Tier 1 surfaced. Engineering work to fix them is deliberately **out of scope** for this validation session (spec: "KHÔNG fix code trong lần này"). Handing off to supervisor to open issues / allocate to next epic.

#### **B11 — Bbox: drag targets wrong element + verify cannot match canvas-painted overlay (P0, from B2 retest)**

- **File:** `flow/operations/_base.py::draw_bbox_on_video` (commit `a165105`)
- **Issues:**
  1. Line 236 `document.querySelector('video')` hits the card-strip thumbnail (105×60 px), not the main preview canvas.
  2. Union selector `svg rect, [class*="bbox" i], ...` on line ~290 cannot match a `<canvas>` with a 2D-painted bbox.
- **Proposed fix direction (not prescriptive — engineering team to validate):**
  - Replace `querySelector('video')` with a canvas-targeting query: `document.querySelectorAll('canvas')` filtered by `getBoundingClientRect()` (largest visible canvas wins). Observed canvas: 598×336 natural, 478.9×269.4 CSS — a `width ≥ 300` threshold safely excludes other canvases.
  - Replace post-drag DOM verify with one of: (a) accept pointer-delivery without DOM verify (trust the framework — current fallback is already "Flow uses default region on miss"), (b) `canvas.toDataURL()` snapshot before/after, compare pixel hash, (c) observe Flow's network request body on submit — if bbox coords are included, the feature worked.
- **Test plan:** integration test requires live Flow + real L1 video (Tier 2). Unit tests need new fixture with canvas mock.
- **Blast radius:** Insert + Remove operations (both depend on this helper). Currently silently broken in production (always falls back to default region).

#### **B12 — Camera: strategies 1+2 miss 100%, verify signals miss 100% → RuntimeError on every camera-move (P0 regression, from B3 retest)**

- **File:** `flow/operations/camera.py::_click_preset` + `_verify_preset_selected` (commit `58937d4`)
- **Issues:**
  1. No preset has any `aria-label` → strategy #1 always finds 0.
  2. No element has explicit `role="button"` attribute; presets are `<BUTTON>` tags. Playwright CSS `[role='button']` is strict-attr and misses them → strategy #2 always finds 0.
  3. Strategy #3 `get_by_text(exact=True)` works and clicks successfully, but `_verify_preset_selected` checks 4 signals — all 4 return false on live DOM (no aria-pressed, no aria-selected, no `active|selected|pressed` in className, no such keyword in parent className).
  4. Strategy #3 therefore falls through. All strategies exhausted → `_click_preset` returns False → `camera_move` raises `RuntimeError("Failed to find camera preset: ...")`.
- **Regression claim:** pre-`58937d4` code did not verify; it would click and submit. Post-`58937d4` code added verification that can never succeed against current DOM. Net effect: **every camera-move job fails hard**, where it previously "worked" (silent-submit with correct preset per Flow's internal state).
- **Proposed fix direction:**
  - Drop strategies #1 and #2 as they never match; keep #3 as primary.
  - Replace verify chain with a signal that actually fires. Options ordered by stability:
    - **(a)** Read inner label `color` via `getComputedStyle(labelDiv).color` — selected ≈ `rgb(48, 48, 48)`, unselected ≈ `rgb(255, 255, 255)`. Semantic (design-intent "dim when selected"), stable across class-hash rotations.
    - **(b)** Detect submit arrow transitioning from `disabled=true` to `disabled=false` around the click (not preset-specific, but proves "a selection happened"; combine with the strategy-3 element identity for preset identity).
    - **(c)** Detect inner label DIV className transition from `hkGUbO` to `jYmHac` — works but ties us to hashed styled-components classes that may rotate per Flow release.
  - Current defensive anti-partial-match design (anchored regex, `exact=True`) is correct and should be preserved. Flow does not render "Lower" so the hazard is theoretical, but keeping the anchoring protects against future UI changes.
- **Test plan:** integration test requires live Flow + real L1 + Camera open (Tier 2). Unit test can be updated to stub the new computed-style-based verify.
- **Blast radius:** every camera-move job — both motion and position presets, all 15 labels.

#### **B13 — `docs/FLOW_UI_REFERENCE.md` Known-unknowns stale (P2, docs)**

- **File:** `docs/FLOW_UI_REFERENCE.md` §Bbox Overlay UI + §Camera Preset Selection & Active State
- **Issue:** both sections end with "Known unknowns — chưa verified trên DOM live" bullet lists. Live retest resolved them (all the unknowns are now knowns, and they invalidate the assumed selectors). This commit's FLOW_UI_REFERENCE edit replaces those Known-unknowns with the actual ground truth.

---

## 8. Handoff notes

### Workdir state

- Branch: `claude/upbeat-mestorf-9dbd52` (worktree `D:\AI\FlowEngine\.claude\worktrees\upbeat-mestorf-9dbd52`)
- `stash@{0}` — **preserved read-only** (unchanged across both passes).
- `.claude/settings.local.json` — expanded MCP tool allowlist. Worktree-local only (not committed).
- No Python files touched across either pass.
- Flow account state after retest:
  - Empty shell project from first pass: `576dad86-ee43-43ca-b4c1-e1898d6b4cad` — still present. User can delete.
  - Retest project: `785d2255-c9e4-4bc6-b4e6-8c2fdf8825d0` — pre-existing, user-supplied. State UNCHANGED: **no new generation, no submit**. I entered Insert mode → drew bbox on canvas (no submit) → exited. Entered Camera mode → clicked Dolly in (Motion tab) → switched to Position tab → clicked Low → exited via back-arrow `←`. None of these clicks triggered Create/Generate.

### Next session recommendations (updated after retest)

**Option A (recommended): open B11 + B12 as code-fix issues, plan next epic.** Both are P0 selector mismatches discovered by this Tier 1 retest. B12 is a regression introduced by commit `58937d4` — the verify step cannot succeed against live DOM, so every camera-move currently raises `RuntimeError`. Fix direction is documented in §7 Followup B-tickets. Estimated complexity: low-to-medium per bug (selector swap + unit-test rewrite).

**Option B: Tier 2 — real submit E2E AFTER B11/B12 fix.** Once selectors are corrected, a single L1 generation + chained insert + camera job closes the Phase A validation loop. Budget: ~3 credits.

**Option C: ship v0.2.0-phase-a-partial.** NOT recommended anymore — unlike after the first-pass BLOCKED verdict, we now know B2 and B3 code do not work in production. Shipping with broken selectors was acceptable when "unverified" but is not acceptable when "known broken".

### Env / tooling notes for next executor

- Chrome MCP tab group ID across both passes: `1418771388`, tab `1988716765`.
- MCP `computer.left_click` is **required** for Radix interactions — `javascript_tool` running `.click()` on the aspect chip did NOT trigger `data-state="open"`. Same rule held on the camera preset buttons (though the visible effect is smaller — the preview video autoplay still shows a frame change).
- MCP coordinate note: `element.getBoundingClientRect()` returns CSS pixels that match MCP `computer.left_click` coordinates on most elements (e.g. Dolly in at JS (102, 426) clicked correctly). BUT the "Camera position" tab click missed at JS coords (217, 352) and only registered at screenshot coords (270, 445) — possibly a transient layout shift around tab state. Lesson: if a click doesn't register, take a fresh screenshot and retry at visual coordinates.
- Canvas-element detection: Flow uses `<canvas>` for both the video preview AND bbox rendering. When targeting the preview, filter by `getBoundingClientRect` size — the main preview canvas is the largest (~480×270 CSS, 598×336 natural). The other `<canvas>` on the page are tiny thumbnails.
- Camera preset active state: the ONLY selector-stable DOM signal is `getComputedStyle(labelDiv).color` — white = unselected, `rgb(48, 48, 48)` = selected. Styled-components class tokens (`jYmHac` vs `hkGUbO`) also reflect state but may rotate per Flow release.

---

## 9. Done criteria checklist

Từ original task spec:

- [x] B1/B2/B3 từng bug có verdict rõ ràng (✅ B1 / ❌ B2 SELECTOR MISMATCH / ❌ B3 SELECTOR MISMATCH)
- [x] `FLOW_UI_REFERENCE.md` phản ánh reality — B2 §Bbox Overlay UI updated with canvas-painted ground truth; B3 §Camera Preset Selection & Active State updated with actual active-state signal (label `color` computed style); all "Known unknowns" lists resolved or removed
- [x] `SPEC.md` §D.4 cập nhật trạng thái — B2 flagged ❌ with B11 followup pointer; B3 flagged ❌ with B12 followup pointer
- [x] Report 9 sections with §7 per-selector detail + §7 followup tickets + §8 handoff
- [x] `stash@{0}` still present (untouched across both passes)
- [x] Zero `.py` diff across both commits (`9a033dd` + this one)
- [x] No job submitted, no credit burned across both passes
- [x] Followup tickets documented for supervisor to open as issues (B11 + B12 = P0 code fixes; B13 = P2 docs cleanup, resolved by this commit's FLOW_UI_REFERENCE update)

---

_Sign-off: ✅ Tier1 DONE — B1 verified live via Chrome MCP, B2/B3 retest on live L1 project confirmed ❌ SELECTOR MISMATCH for both. Two P0 code-fix followups (B11 bbox canvas, B12 camera verify-signal) handed off to supervisor. No code fixed in this session (spec: docs-only validation). B3 is a regression from commit `58937d4` — camera-move currently raises `RuntimeError` on every call._
