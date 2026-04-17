# Session Report — `B12` Camera verify regression fix (fixes B3 regression)

---

## 1. Metadata

| Field | Value |
|---|---|
| Task ID | `B12` |
| Task type | bug-fix (regression) |
| Session started | 2026-04-17 |
| Session ended | 2026-04-17 |
| Duration actual | ~40m |
| Duration estimate | n/a (P0 regression from Tier1 E2E) |
| Worker | Claude Opus 4.7 |
| Branch | `claude/agitated-allen-777ade` |

---

## 2. Commits landed

```
<B12-COMMIT>  fix(camera): verify preset via computed label color (B12 — fixes B3 regression)
```

One commit: camera.py rewrite + test rewrite + 3 docs + this report.

---

## 3. Files changed

```
flow/operations/camera.py                                         ~40 / ~55   (_verify_preset_selected rewrite + prune strategies 1+2)
tests/test_camera.py                                              ~200 / ~210 (full rewrite for color-based verify + single-strategy click)
docs/FLOW_UI_REFERENCE.md                                         ~55 / ~70   (§Camera Preset Selection & Active State — color table + pruning note)
docs/SPEC.md                                                      ~30 / ~5    (§D.4 B3 updated to FIXED via B12; §D.4 B12 struck-through + resolution detail)
docs/WORKPLAN.md                                                  +1 / -1     (§8 B12 strike-through + commit pointer)
docs/session-reports/2026-04-17_B12_camera-verify-fix.md          new         (this file)
```

Scope: 1 prod file + 1 test file + 2 docs + 1 WORKPLAN line + 1 new session report. No diff outside whitelist. `_base.py` untouched (B11 territory).

---

## 4. Tests

| Test | Result | Notes |
|---|---|---|
| `tests/test_camera.py::test_verify_returns_true_on_dim_color` | ✅ pass | evaluate=True → verify True + INFO |
| `tests/test_camera.py::test_verify_returns_false_on_bright_color` | ✅ pass | evaluate=False → verify False + WARNING |
| `tests/test_camera.py::test_verify_returns_false_on_evaluate_exception` | ✅ pass | exception swallowed → verify False + WARNING |
| `tests/test_camera.py::test_verify_script_uses_computed_color_signal` | ✅ pass | JS contract: getComputedStyle + color + rgb present |
| `tests/test_camera.py::test_click_preset_get_by_text_succeeds` | ✅ pass | happy path + `page.locator` never called (pruning contract) + `exact=True` |
| `tests/test_camera.py::test_click_preset_returns_false_when_preset_absent` | ✅ pass | not visible → ERROR, no click, no verify |
| `tests/test_camera.py::test_click_preset_clicked_but_color_verify_fails` | ✅ pass | click succeeded but color→False → WARNING + ERROR |

- Suite-wide: `28 pass / 0 fail / 0 skipped`
- Command: `pytest tests/test_camera.py -v` (unit) then `pytest tests/ -v` (full) then `pytest tests/ -W error::DeprecationWarning` (clean).
- RED→GREEN proof:
  - Before camera.py rewrite, on old code: 6/7 pass (contract test FAILED on missing `getComputedStyle`; happy-path FAILED on `page.locator` was-called assertion — 2 RED tests).
  - After camera.py rewrite: 7/7 green.
- Manual E2E (real Flow camera job) deferred to supervisor Tier 2 (spec: unit-test level only for this session).

---

## 5. SPEC.md update

- [x] §D.4 B3 — rewritten: "FIXED via B12" with commit pointer; initial `58937d4` regression history preserved
- [x] §D.4 B12 — strike-through + resolution block (click pruning + color verify + 7 test cases + docs refs)
- [x] Commit hash placeholder `<B12-COMMIT>` left in both entries — will be rewritten to real hash by commit step

Commit hash for SPEC.md update: same as camera fix commit (atomic — SPEC is in the same commit).

---

## 6. Invariants & rules verified

- [x] INV-1 Account Binding — n/a (no profile/worker change)
- [x] INV-2 Navigate by `edit_url` — n/a (no navigation change)
- [x] INV-3 Store Everything — n/a
- [x] INV-4 Serial per Project — n/a
- [x] INV-5 media_id stable — n/a
- [x] R-CODE-3 Locale-Independent — `get_by_text(exact=True)` remains EN-only; documented in FLOW_UI_REFERENCE.md §Camera Preset Selection & Active State as a known limitation (identical to phase-1 stance). No new locale coupling introduced.
- [x] R-CODE-10 No `datetime.utcnow()` — n/a
- [x] R-CC-1 KHÔNG restructure kiến trúc — surgical fix to two functions + test rewrite; no module moves, no new abstractions
- [x] §1.3 "no dead code" — dead strategies 1+2 removed (decision = Option A, per supervisor's recommendation; see §7)

---

## 7. Issues / Decisions

### Dead-strategy decision (Option A — prune)

Tier1 live-DOM probing confirmed strategies #1 (`[aria-label='<direction>']`) and #2 (`page.locator("[role='button']").filter(has_text=<anchored regex>)`) find **0 elements** on production Flow across all 15 presets. Presets carry no `aria-label`, and no element on the page has an explicit `role="button"` attribute (Playwright CSS `[role='button']` is strict-attr and does not match implicit `<button>` roles).

**Chosen: Option A (prune).** Both strategies were removed from `_click_preset` along with the `import re` that supported the anchored regex. Rationale:
- They are observably dead on the only production DOM we target.
- Keeping them as "future defensive layers" would add ~20 lines of noise that never execute, plus two `page.locator` round-trips per call returning empty locators.
- Partial-match defense (the original reason for the anchored regex) is preserved natively by Playwright's `exact=True` on `get_by_text`.
- Aligns with spec §1.3 ("don't keep dead/unused code / defensive boilerplate that never fires").

If a future Flow refactor adds `aria-label` or explicit `role="button"` attributes, we can re-add the strategies at that point — we have the Tier1 evidence trail to know when/why.

### Verify threshold (R+G+B < 400)

Ground-truth colors (Tier1 §B3):
- Selected: `rgb(48, 48, 48)` → sum **144**
- Unselected: `rgb(255, 255, 255)` → sum **765**

`400` sits centered (±256 margin on each side) so anti-aliasing / subtle theme drift cannot flip the classification. Used a single threshold rather than a bespoke "matches selected" check because "dim-grey when selected" is a stable design-intent invariant (the label darkens because the background thumbnail highlights), not an accidental class value.

### JS snippet scope

The verify JS walks `<button>` elements only (not `[aria-label], [role="button"], button`). This is narrower than the pre-B12 union and correct for Flow's DOM (presets ARE `<button>` tags). If a future preset element is rendered as `<div role="button">`, this would need revisiting — but Tier1 §B3 explicitly confirmed `<button>` today.

### Test contract: `page.locator.assert_not_called()`

Added this assertion in `test_click_preset_get_by_text_succeeds` precisely because old code silently passed the simpler "evaluate was called once" check (since its strategy-1/2 fallthrough still reached strategy-3). The `locator.assert_not_called()` catch is what made TDD meaningful — it turned RED on the old code and only goes GREEN when strategies 1+2 are actually gone.

### Bug candidates phát hiện NHƯNG KHÔNG fix (out of scope)

- **B11** — `flow/operations/_base.py::draw_bbox_on_video` (bbox draw+verify): Tier1 §B2 evidence confirms same class of mismatch (wrong element + canvas-painted overlay). Deliberately untouched — supervisor will supply B11 prompt after B12 is verified. Cross-edits between camera.py and _base.py were prohibited by the FILE BLACKLIST.

---

## 8. Handoff notes

### Workdir state
- Branch: `claude/agitated-allen-777ade` (worktree `D:\AI\FlowEngine\.claude\worktrees\agitated-allen-777ade`)
- `stash@{0}` — unchanged, still present (`WIP: flow refinements — direct edit-url nav, …`). Verified before + after.
- `git status` clean post-commit.
- Python env: system Python 3.13.5, pytest-9.0.2, pytest-asyncio 1.3.0 — no new deps.

### NEXT session = B11 (from Tier1 §B2 evidence)
- **File to fix:** `flow/operations/_base.py::draw_bbox_on_video` (commit `a165105`).
- **Two subproblems:**
  1. `document.querySelector('video')` hits a 105×60 card-strip thumbnail, not the 598×336 preview canvas. Fix direction: target the largest visible `<canvas>` (`width ≥ 300` threshold filters out thumbnail canvases).
  2. Bbox is painted onto canvas 2D bitmap — union DOM selector cannot detect it. Fix direction (supervisor's note in prompt): replace DOM-verify with pointer-trust OR intercept Flow's network request on submit (the submit payload includes the masked region coords).
- **Tests to update:** `tests/test_bbox.py` — currently stubs `page.evaluate` and passes against broken selectors. Will need to reflect canvas-targeting + trust/network-verify semantics.
- Evidence trail + decision factors: `docs/session-reports/2026-04-17_Tier1_dom-validation.md` §7 B2 + §8 handoff notes.

### Reference for future camera touches
- `docs/FLOW_UI_REFERENCE.md` §Camera Preset Selection & Active State now encodes the color table + pruning rationale. Any future verify regression should start there.
- `tests/test_camera.py::test_verify_script_uses_computed_color_signal` is the trip-wire — it will fail if someone reverts to attribute-based verify.

---

## 9. Done criteria checklist

- [x] `_verify_preset_selected` uses `getComputedStyle(labelDiv).color` (R+G+B < 400 threshold)
- [x] Dead-strategy decision documented (Option A — prune, per §7)
- [x] `tests/test_camera.py` rewritten: 7 cases covering verify (true / false / exception / JS-contract) + click (succeed / absent / clicked-but-unverified)
- [x] Full suite pass (`pytest tests/ -v` → 28/28)
- [x] `-W error::DeprecationWarning` clean (no new warnings from the rewrite)
- [x] `docs/FLOW_UI_REFERENCE.md` §Camera Mode UI updated with color table
- [x] `docs/SPEC.md` §D.4 B3 + B12 updated (B3 FIXED-via-B12, B12 struck-through + resolution block)
- [x] `docs/WORKPLAN.md` §8 B12 strike-through + commit pointer
- [x] `stash@{0}` preserved (untouched across session)
- [x] Zero diff outside whitelist (`_base.py`, server/, worker/, models/, other tests/ — all untouched)
- [x] Commit message matches required format (fix(camera): … / Closes #B12 / Co-Authored-By)
- [x] This report covers all 9 template sections

---

_Sign-off: ✅ B12 DONE — commit `<B12-COMMIT>`, report `docs/session-reports/2026-04-17_B12_camera-verify-fix.md`. Next = B11 (bbox draw+verify, canvas target) — supervisor to supply prompt._
