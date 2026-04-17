# Session Report — `B11` Bbox canvas-target + pointer-trust verify (fixes B2 silent fallback)

---

## 1. Metadata

| Field | Value |
|---|---|
| Task ID | `B11` |
| Task type | bug-fix (P0 — silent fallback, superseding B2 commit `a165105`) |
| Session started | 2026-04-17 |
| Session ended | 2026-04-17 |
| Duration actual | ~45m |
| Duration estimate | n/a (P0 from Tier1 E2E retest — queued after B12) |
| Worker | Claude Opus 4.7 |
| Branch | `claude/laughing-feistel-853f9a` |

---

## 2. Commits landed

```
<B11-COMMIT>  fix(bbox): target canvas preview + verify via pointer trust (B11 — fixes B2 silent fallback)
```

One commit: `_base.py` rewrite + test rewrite + 3 docs + this report. No multi-commit sequence.

---

## 3. Files changed

```
flow/operations/_base.py                                          +62 / -53   (draw_bbox_on_video: canvas target + pointer-trust verify)
tests/test_bbox.py                                                +130 / -83  (full rewrite: 6 cases for canvas contract + 2 trip-wires)
docs/FLOW_UI_REFERENCE.md                                         +52 / -23   (§Bbox Overlay UI — canvas target + Option A/B rationale + pitfalls)
docs/SPEC.md                                                      +20 / -11   (§D.4 B2 → FIXED via B11; §D.4 B11 struck-through + resolution block)
docs/WORKPLAN.md                                                  +1 / -1     (§8 B11 strike-through + commit pointer)
docs/session-reports/2026-04-17_B11_bbox-canvas-fix.md            new         (this file)
```

Scope: 1 prod file + 1 test file + 3 docs + 1 new session report. Zero diff outside whitelist. `insert.py` / `remove.py` / `camera.py` / server/worker/models untouched.

---

## 4. Tests

| Test | Result | Notes |
|---|---|---|
| `tests/test_bbox.py::test_bbox_rejects_out_of_range` | ✅ pass | `x=1.5` → False + ERROR; no drag |
| `tests/test_bbox.py::test_bbox_rejects_no_canvas` | ✅ pass | `canvas_rect=None` → False + ERROR; no drag; evaluate called once |
| `tests/test_bbox.py::test_bbox_targets_largest_canvas_rect` | ✅ pass | 600×400 canvas at (100, 50) → drag start/end (250, 150)↔(550, 350) |
| `tests/test_bbox.py::test_bbox_evaluate_script_targets_canvas` | ✅ pass | JS contract: contains `canvas` + `300`, does NOT contain `querySelector('video')` |
| `tests/test_bbox.py::test_bbox_clamps_overflow` | ✅ pass | `x=0.7 w=0.5` → clamp `w=0.30`, drag end caps at canvas edge |
| `tests/test_bbox.py::test_bbox_returns_true_after_drag_no_post_verify` | ✅ pass | contract trip-wire: `page.evaluate.await_count == 1` (canvas-find only) |

- Suite-wide: `29 pass / 0 fail / 0 skipped` (one additional bbox test beyond the prior 28/28 baseline).
- Commands:
  - `pytest tests/test_bbox.py -v` → 6/6 GREEN.
  - `pytest tests/ -v` → 29/29 GREEN (no regression in aspect-ratio / camera / job-store / etc).
  - `pytest tests/ -W error::DeprecationWarning` → 29/29 GREEN (no new deprecations).
- RED → GREEN proof:
  - Before `_base.py` rewrite, on old code: **3/6 RED**.
    - `test_bbox_rejects_no_canvas` FAILED — old ERROR message says "video element not found" (expected "canvas").
    - `test_bbox_evaluate_script_targets_canvas` FAILED — old JS uses `querySelector('video')` (expected "canvas" + "300").
    - `test_bbox_returns_true_after_drag_no_post_verify` FAILED — old code calls `page.evaluate` twice (canvas-find + overlay check); expected 1.
  - After `_base.py` rewrite: 6/6 GREEN. RED tests go green precisely because the two contract trip-wires fail on old code and pass on new.
- Manual E2E (live bbox on a real insert/remove job) deferred to Tier 2 per supervisor's handoff. Unit-test level only for this session.

---

## 5. SPEC.md update

- [x] §D.4 B2 — updated from `⚠️ FIXED-BUT-MISMATCH` to `✅ FIXED via B11`; initial `a165105` mismatch history preserved; resolution block points to B11 commit.
- [x] §D.4 B11 — strike-through + resolution block (canvas target + pointer-trust rationale + 6 test cases + docs refs).
- [x] Commit hash placeholder `<B11-COMMIT>` left in both entries — will be rewritten to real hash by a follow-up `docs:` commit (same pattern as B12 → `78d3e40`).

---

## 6. Invariants & rules verified

- [x] INV-1 Account Binding — n/a (no profile/worker code touched)
- [x] INV-2 Navigate by `edit_url` — n/a (no navigation changes)
- [x] INV-3 Store Everything — n/a (helper returns bool, not job result)
- [x] INV-4 Serial per Project — n/a
- [x] INV-5 media_id stable — n/a
- [x] R-CODE-3 Locale-Independent — JS inspects `<canvas>` tag + size; no text match; zero locale coupling.
- [x] R-CODE-10 No `datetime.utcnow()` — n/a
- [x] R-CC-1 KHÔNG restructure kiến trúc — surgical fix to one helper + test rewrite; no module moves, no new abstractions, signature preserved (`(page, bbox) -> bool`).
- [x] §1.3 "no dead code" — union-selector verify removed entirely (not extended, not gated behind a flag); it was observably dead against Flow's canvas-painted UI.

---

## 7. Issues / Decisions

### Option A (pixel sampling) vs Option B (pointer-trust) — chose B (pointer-trust, contract preserved)

Supervisor prompt allowed either. Chosen **Option B1 (keep contract, return True after drag without post-drag verify)** because Option A has two concrete failure modes that Option B doesn't:

1. **Video-frame noise floor.** The preview is a `<canvas>` that plays video frames continuously. `getContext('2d').getImageData(rect)` before drag and after `mouseup` captures two different frames from the video playback — there is a non-zero RGBA delta even when no bbox was painted. Choosing a threshold large enough to exclude frame-advance noise but small enough to catch subtle bbox paints (dashed grey stroke) requires per-project tuning that we don't have a mechanism to do (unit tests cannot ground-truth this; integration would need live Flow). A false-negative on the threshold means we log WARNING and Flow uses default region — same silent fallback as the B2 bug, just with more code.

2. **CORS / WebGL `SecurityError`.** If Flow's canvas is CORS-tainted (streaming remote video frames) or backed by WebGL without `preserveDrawingBuffer`, `getImageData` throws `SecurityError` / returns zeros. We have no signal from Tier1 on the canvas's internal type. Option A would need a try/except that falls back to… pointer-trust anyway. Simpler to pick pointer-trust from the start.

**Option B has no such failure modes.** The load-bearing guarantee is: "the drag lands on the correct canvas rect." We prove this structurally:
- Canvas-find JS returns the largest visible canvas with `width ≥ 300`. Tier1 confirmed there is exactly ONE such canvas on the edit page (the preview). Thumbnail canvases are < 200 px and excluded.
- Drag coordinates derive from `canvas_rect.left/top/width/height`. If the canvas exists and the rect is non-zero, the drag physically traverses pixels inside that canvas.
- Flow accepts pointer events on its preview canvas (Tier1 §B2: forced drag via MCP `left_click_drag` produced a visible bbox). No additional signal is needed to confirm acceptance.

If the canvas is missing or the rect is degenerate, we return False pre-drag — same failure mode as any other "preview not loaded" scenario, caught at ERROR level with a clear message. The caller's existing WARNING block in `insert.py:82-84` + `remove.py:82-85` still fires exactly when it should (pre-drag canvas not found or input out of range).

**Contract preservation (B1 vs B2):** Chose B1 per supervisor recommendation — function keeps `(page, bbox) -> bool` signature, `True`/`False` semantics preserved. Caller code (`insert.py`, `remove.py`) untouched. If B2 (change signature to return `None`) had been chosen, I would have needed to extend the whitelist to touch `insert.py` + `remove.py` — unnecessary for minimal scope.

### Removing the union-selector verify entirely (not gating behind a flag)

Per spec §1.3 "no dead code": the union selector `svg rect, [class*="bbox" i], …` matches 0 elements on Flow's DOM (Tier1 §B2 ground truth) — it is observably dead code. Keeping it as a "defense layer" would add ~15 lines of noise that never execute, plus a second `page.evaluate` round-trip per call returning false. Same judgment call as B12 (dead strategies 1+2 pruned). If a future Flow refactor introduces a DOM overlay for bbox, we re-add the verify at that point — the Tier1 evidence trail tells us when/why.

### 300-px width threshold (not 50-px)

The B2 helper's `width/height < 50` threshold was too permissive — a 105×60 card-strip thumbnail would pass. `width ≥ 300 && height ≥ 200` cleanly separates the 598×336 preview from any thumbnail canvas (card-strip canvases are far below 300 px in CSS). Threshold chosen so it has margin on both sides: the preview is at minimum 479 CSS px on an edit page; thumbnails are at most ~105 CSS px. 300 sits in the gap with ~175-px margin each side — robust against Flow responsive-layout changes.

### Not touching `insert.py` / `remove.py`

`if not drew: logger.warning(...)` in both callers fires ONLY when `draw_bbox_on_video` returns False — which, post-B11, means a genuine pre-drag failure (canvas not found or input out of range). These are legitimate operator-visible conditions worth warning on. Removing the warning would lose signal. Keeping callers unchanged also satisfied the FILE BLACKLIST.

### Test contract trip-wires (2 of 6 tests)

Two tests intentionally exist as contract guards, not behavior checks:
- `test_bbox_evaluate_script_targets_canvas` inspects the JS source string for `canvas`, `300`, and absence of `querySelector('video')`. Guards against silent regression to the B2 bug.
- `test_bbox_returns_true_after_drag_no_post_verify` asserts `page.evaluate.await_count == 1`. Guards against silent reintroduction of a post-drag verify step that could bring back the pixel-noise / CORS-error class of failure.

Both trip-wires failed RED on old code and turn GREEN only when the implementation actually conforms — they are what made TDD meaningful here (similar to B12's `test_verify_script_uses_computed_color_signal`).

### Bug candidates phát hiện NHƯNG KHÔNG fix (out of scope)

- None new. Tier1 already surfaced B11 + B12 + B13, and all three are now resolved (B12 in `78d3e40`, B11 in this commit, B13 resolved inline with Tier1 commit `9facbe3`).

---

## 8. Handoff notes

### Workdir state

- Branch: `claude/laughing-feistel-853f9a` (worktree `D:\AI\FlowEngine\.claude\worktrees\laughing-feistel-853f9a`).
- `stash@{0}` — unchanged, still present (`WIP: flow refinements — direct edit-url nav, chip re-click model close, verify extend panel, iterate enabled submit buttons`). Verified before + after session.
- `git status` clean post-commit.
- Python env: system Python 3.13.5, pytest-9.0.2, pytest-asyncio 1.3.0 — no new deps.

### NEXT session — Tier1 RE-VALIDATION or Tier 2 E2E

With B11 + B12 both landed, Phase A code fixes are complete. Supervisor decides from three options:

1. **Tier1 re-validation (recommended, low cost).** Re-run Chrome MCP live-DOM probe to confirm:
   - B11 — largest-visible-canvas selection reliably returns the preview (and only the preview) on a real L1 project.
   - B12 — computed-color verify matches live selected/unselected state on real camera preset buttons.
   - B1 re-verify (quick smoke — aspect ratio chip still calibrates correctly post-stack-change).
   - Expected cost: 0 credits (no submit), ~30m executor time.

2. **Tier 2 — real submit E2E.** One chained L1 + insert + camera job. Validates the full flow end-to-end against production, including:
   - `draw_bbox_on_video` actually lands a bbox on the canvas and Flow uses the operator's region (not default).
   - `_verify_preset_selected` returns True on real Flow state.
   - Budget: ~3 credits.

3. **Tag `v0.2.0-phase-a`.** Acceptable if supervisor is confident the code-review + ground-truth evidence from Tier1 §B2/B3 is sufficient without re-probing. Carries residual risk: we verified B12 at the unit-test level and the fix direction via Tier1 evidence, but did not re-probe post-fix. Same for B11.

Personal recommendation: **Option 1 (Tier1 re-validation)** — cheapest signal. If selector contracts hold on live DOM, proceed directly to tag without spending credits on Tier 2.

### Reference for future bbox touches

- `docs/FLOW_UI_REFERENCE.md` §Bbox Overlay UI now encodes the canvas-target + pointer-trust contract + pitfall list.
- `tests/test_bbox.py::test_bbox_evaluate_script_targets_canvas` is the trip-wire — it will fail if someone reverts to `querySelector('video')` or drops the 300-px filter.
- `tests/test_bbox.py::test_bbox_returns_true_after_drag_no_post_verify` is the other trip-wire — it will fail if someone adds a post-drag `page.evaluate` call.

### Post-commit housekeeping

- `<B11-COMMIT>` placeholder appears in 4 locations: `SPEC.md §D.4 B2`, `SPEC.md §D.4 B11`, `WORKPLAN.md §8 B11`, and this report's §2 + §7. A follow-up `docs:` commit should rewrite them to the real hash after commit creation (same pattern as B12 → `b7ac05b` rewrote `<B12-COMMIT>` to `78d3e40`).

---

## 9. Done criteria checklist

Từ supervisor prompt `[DONE CRITERIA]`:

- [x] `draw_bbox_on_video` targets largest canvas (width ≥ 300)
- [x] Option A or B chosen — **Option B (pointer-trust, contract preserved)**, rationale documented §7
- [x] `tests/test_bbox.py` rewritten — 6 cases, all GREEN (including 2 contract trip-wires)
- [x] Full suite pass (`pytest tests/ -v` → 29/29) — no regression (camera B12 tests still green)
- [x] `pytest -W error::DeprecationWarning` clean
- [x] `docs/FLOW_UI_REFERENCE.md` §Bbox Overlay UI reflects canvas reality + pointer-trust rationale + pitfall list
- [x] `docs/SPEC.md` §D.4 B2 + B11 updated (B2 → FIXED via B11, B11 struck-through + resolution block)
- [x] `docs/WORKPLAN.md` §8 B11 strike-through + commit pointer
- [x] `stash@{0}` preserved (untouched across session)
- [x] Zero diff outside whitelist (insert.py / remove.py / camera.py / server / worker / models / other tests all untouched)
- [x] Report covers all 9 template sections

---

_Sign-off: ✅ B11 DONE — commit `<B11-COMMIT>`, report `docs/session-reports/2026-04-17_B11_bbox-canvas-fix.md`. Phase A code fixes complete (B1 + B2 + B3 + B5 + B6 + B7 + B8 + B9 + B11 + B12 all landed; B4 deferred P2; B10 deferred P2; B13 resolved inline with Tier1). Next = supervisor choice: Tier1 re-validation / Tier 2 E2E / tag `v0.2.0-phase-a`._
