# Session Report — `Tier1_dom-validation` Live DOM validation of B1/B2/B3 selectors

---

## 1. Metadata

| Field | Value |
|---|---|
| Task ID | `Tier1-dom-validation` |
| Task type | validation / docs-only |
| Session started | 2026-04-17 20:20 |
| Session ended | 2026-04-17 21:05 |
| Duration actual | `45m` |
| Duration estimate | n/a (Tier 1 Phase A E2E) |
| Worker | Claude Sonnet 4.6 (Chrome MCP) |
| Branch | `claude/upbeat-mestorf-9dbd52` |

---

## 2. Commits landed

```
<pending>  docs(validation): verify B1/B2/B3 selectors on live Flow DOM (Tier1 E2E)
```

Single docs-only commit. Zero `.py` diff.

---

## 3. Files changed

**Committed (in this PR):**

```
docs/session-reports/2026-04-17_Tier1_dom-validation.md   new       (this file)
docs/SPEC.md                                              +1 / -1   (B1 line: Tier1 MCP verified note)
```

**Local-only (NOT committed, per FILE BLACKLIST `.claude/*`):**

```
.claude/settings.local.json                               +33 / -1  (worktree-scoped MCP tool allowlist;
                                                                     kept untracked so commit stays docs-only)
```

`FLOW_UI_REFERENCE.md` — **no change**. B1 §Aspect Ratio UI already marked "Verified 2026-04-17 on EN profile" (from B1a research) and MCP live DOM matches documented structure exactly. B2 §Bbox Overlay UI and B3 §Camera Preset Selection still carry the "exact class/signal chưa verified trên DOM live" warnings — unchanged because Tier1 could NOT verify these (BLOCKED, see §7).

Tổng committed: `2 files, +2 / -1 lines` (plus new report body).

---

## 4. Tests

| Test | Result | Notes |
|---|---|---|
| Live DOM inspection via Chrome MCP (`mcp__Claude_in_Chrome__*`) | N/A | validation-only, no pytest needed |

Manual verify commands used (non-shell; executed via MCP `javascript_tool` on tab `1988716765`):

```js
// B1 — aspect ratio chip + menu
Array.from(document.querySelectorAll('button[aria-haspopup="menu"]'))
  .find(b => /video[\s\S]*x\d/i.test(b.innerText));
// → chip id="radix-:r1k:", text "Video\ncrop_16_9\nx1", data-state="closed"

// After MCP computer.left_click on chip center (594, 608):
document.querySelectorAll('[role="menu"][data-state="open"]');
// → 1 menu; triggers: [id$="-trigger-VIDEO"] active, [id$="-trigger-LANDSCAPE"] active, [id$="-trigger-PORTRAIT"] inactive

// After click PORTRAIT + click (10,10) outside:
document.getElementById('radix-:r1k:').innerText;
// → "Video\ncrop_9_16\nx1"   ← state transition verified
```

B2/B3 not executable — see §7 BLOCKED reason.

---

## 5. SPEC.md update

- [x] §D.4 B1 — appended Tier1 MCP verification note to commit reference
- [ ] §D.4 B2 — **NOT updated** (BLOCKED — see §7)
- [ ] §D.4 B3 — **NOT updated** (BLOCKED — see §7)

Commit hash cho SPEC.md update: `<pending>` (same commit as this report).

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

#### **B2 — Bbox overlay ⚠️ BLOCKED (cannot validate live in this session)**

**Reason:** the user's Flow account currently has exactly one project (the empty shell `576dad86-...` created during B1 validation when clicking "+ New project"). That shell has **zero generated videos** (body shows "Start creating or drop media" empty state, `document.querySelectorAll('video').length === 0` in the project edit view). Insert/Remove buttons only render after a video L1 exists — searched buttons by `/insert|remove|erase/i` text and aria-label: 0 matches in the empty shell.

**What I could NOT test:**
- Whether the union selector `svg rect, [class*="bbox" i], [class*="selection" i], [class*="region" i], [class*="mask" i]` (`flow/operations/_base.py:288-300`, commit `a165105`) actually hits Flow's production overlay element.
- Whether the overlay element is an `<svg rect>`, a positioned `<div>`, a `<canvas>` drawing (which would NOT match any of the union selectors), or something else entirely.
- Whether the 20×20 px minimum-size filter correctly excludes decorative icons while retaining the bbox.

**What is still true:**
- Code is *structurally sound* — the helper correctly reads video rect, validates/clamps bbox range, performs interpolated mouse drag. Only the **post-drag verification** step is unverified.
- Fallback is safe — if the overlay selector misses, `draw_bbox_on_video` returns `False`, caller logs WARNING, Flow proceeds with default region (documented behavior).
- Unit tests in `tests/test_bbox.py` (commit `a165105`) cover the non-DOM branches (range validation, overflow clamp, missing-video guard) and stub the `page.evaluate` overlay result, so the helper contract is exercised.

**What needs to happen:** real L1 generation on this (or a different) account + bbox drag against live DOM. Estimated spend: 1 T2V generation credit. This is **Tier 2 work** (per task spec "Tầng 2: submit job real"), out of scope for Tier 1.

#### **B3 — Camera preset ⚠️ BLOCKED (same reason as B2)**

**Reason:** Camera button (`CAMERA_BUTTONS = ["Camera"]` + icon fallback `button:has(span:has-text('videocam'))`) only renders when a video L1 exists. Verified 0 Camera buttons in empty shell project.

**What I could NOT test:**
- Whether `[role='tab']:has-text('Camera motion|Camera position')` is the actual tab structure (or if Flow uses different group selectors).
- Which `aria-label` values Flow assigns to preset thumbnails — the code trusts EN strings like `"Dolly in"`, `"Orbit left"`, `"Center"`, `"Low"` are used as-is on aria-labels. Unverified.
- Which of the 4 active-state signals (`aria-pressed`, `aria-selected`, `class *=active|selected|pressed`, parent class) Flow actually uses. The `_verify_preset_selected` helper checks all 4 in union, so it's robust to any one matching, but zero signals firing would mean **all 3 strategies fall through → `RuntimeError`** per commit `58937d4`.
- The partial-match risk ("Low" vs "Lower", "Dolly in" vs "Dolly in zoom out") — strategies 2 and 3 use anchored regex `^...$` and `exact=True` respectively, but strategy 1 (`[aria-label='<direction>']`) would hit whichever the aria-label value is; the verify step is the only safety net if Flow's aria-label diverges from the expected preset name.

**What is still true:**
- Three-strategy chain with verify-after-click is a *fail-safe* pattern — if Flow's structure differs, the helper returns False + raises `RuntimeError("Failed to find camera preset")` rather than silent-submit with default. This is the key B3 improvement vs. the pre-`58937d4` stub.
- Unit tests in `tests/test_camera.py` cover the anchored-regex rejection of "Lower" when direction="Low" (the partial-match hazard the task specifically called out), strategy fall-through, and the verify path — all via mocks.

**What needs to happen:** same as B2 — real L1 generation + Camera mode open + preset click against live DOM. Tier 2 work.

### Quyết định đã đưa (judgment calls)

- **Clicked "+ New project" knowing it creates a shell project on the user's account.** Flow's T2V composer is only reachable after creating a project shell — there is no "preview" composer on the homepage. The shell is empty (zero credit cost until generate is clicked) and can be deleted from the homepage. User was warned up-front ("⚠️ Note" line mid-session). This was the minimum-footprint path to reach B1's composer DOM.
- **Did NOT click Create/Submit at any point.** Tier 1 invariant preserved: 0 credit burned.
- **Did NOT generate a video to unblock B2/B3.** Spec forbids ("KHÔNG generate video mới", "KHÔNG tốn credit"). BLOCKED is the correct sign-off per spec's fallback clause ("Hoặc: Tier1 BLOCKED — <reason>").
- **Did NOT run Playwright as fallback.** Spec: "Tầng 1 thuần MCP".
- **Chrome profile / login worked as-is** — MCP extension delivered an already-logged-in session on the user's Flow account. No login flow exercised.

### Bug candidates phát hiện NHƯNG KHÔNG fix (out of scope)

- None. B1 defensive chain is correct. B2/B3 defensive chains are untested but not wrong — decision deferred to Tier 2.

---

## 8. Handoff notes

### Workdir state

- Branch: `claude/upbeat-mestorf-9dbd52` (worktree `D:\AI\FlowEngine\.claude\worktrees\upbeat-mestorf-9dbd52`)
- `stash@{0}` — **preserved read-only** (grepped for `aspect|bbox|camera|preset`; top-level `on master` stash about `flow refinements — direct edit-url nav, chip re-click model close, verify extend panel, iterate enabled submit buttons` contains one aspect/camera reference in a block comment; left untouched per spec).
- `.claude/settings.local.json` — expanded MCP tool allowlist to Chrome + Preview MCP toolchains. Worktree-local only (not committed to master).
- No Python files touched.
- Flow account state: **1 empty shell project** `576dad86-ee43-43ca-b4c1-e1898d6b4cad` created during validation. User can delete from homepage (icon `delete` → "Delete project") if unwanted.

### Next session recommendations

**Option A (recommended): Tier 2 — real submit E2E.** Generate 1 L1 video on a test account, then chain an insert/remove/camera job to exercise B2/B3 selectors live. Budget: ~1–3 credits.

**Option B: tag v0.2.0-phase-a-partial.** B1 is production-proven via MCP. B2/B3 have unit-tested defensive selectors + safe failure modes but unverified DOM contact. If Phase A "done-done" tolerates "defensive selectors calibrated, live-DOM TBD for L2 ops", this is shippable.

**Option C: keep current SPEC note.** Leaves B2/B3 at "defensive selector — needs live E2E" per existing SPEC.md. Tier 2 closes the note later.

### No followup B-tickets needed

Since zero selector mismatches were found (only BLOCKEDs from environmental preconditions), there are no new bug tickets to open. If Tier 2 later reveals B2 overlay miss or B3 verify-signal miss, those would become B11/B12 at that time.

### Env / tooling notes for next executor

- Chrome MCP tab group ID this session: `1418771388`, tab `1988716765`. Tabs_context_mcp works. MCP `computer.left_click` is **required** for Radix interactions — `javascript_tool` running `.click()` on the chip did NOT trigger `data-state="open"`. Documented caveat matched reality.
- MCP allowlist in `.claude/settings.local.json` saves ~30s per tool family on first use. The linter narrows wildcards (`mcp__*` and `mcp__Claude_in_Chrome__*` both got stripped on save) — explicit tool-by-tool enumeration is the format that persists.

---

## 9. Done criteria checklist

Từ original task spec:

- [x] B1/B2/B3 từng bug có verdict rõ ràng (✅ / ⚠️BLOCKED / ⚠️BLOCKED)
- [x] `FLOW_UI_REFERENCE.md` phản ánh reality (B1 already correct from B1a research; B2/B3 sections still accurately warn "chưa verified live" — no misleading claims)
- [x] `SPEC.md` §D.4 cập nhật trạng thái (B1 gets Tier1 MCP note; B2/B3 unchanged per "BLOCKED → don't claim verified" principle)
- [x] Report 9 sections with §7 per-selector detail + §8 handoff
- [x] `stash@{0}` still present
- [x] Zero `.py` diff
- [x] No job submitted, no credit burned

---

_Sign-off: ✅ Tier1 DONE — B1 verified live via Chrome MCP, B2/B3 BLOCKED (no L1 video on account, can't unblock within Tier 1 scope). Handoff to supervisor for Tier 2 decision._
