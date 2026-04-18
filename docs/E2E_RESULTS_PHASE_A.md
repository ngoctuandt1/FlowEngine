# Phase A — E2E Results

> Live engine E2E validation log per `docs/WORKPLAN.md` §5 / §7 Meta.
> Format: one section per attempt, most recent first. `Tier 1 = DOM probe via Chrome MCP`; `Tier 2 = full engine-driven chain via REST API`.

---

## Tier 2 — 2026-04-18 — Run 8 — ⚠️ **PARTIAL** (B19 fix holds end-to-end on J1; J2/J3 expose independent L2 inheritance gap — out of B19 scope)

| Field | Value |
|---|---|
| Date | 2026-04-18 ~07:40 UTC (~14:40 local) |
| Profile | `ngoctuandt20` |
| Chain type | 3-job (t2v 9:16 → camera Dolly in → insert bbox) |
| B19 commit under test | `e1597b2` (this branch — `claude/gallant-jang-cbe036`) |
| Session report | [`docs/session-reports/2026-04-18_B19_aspect-chip-multiline.md`](session-reports/2026-04-18_B19_aspect-chip-multiline.md) |

### Per-job verdict

| # | Job | Target bug | Status | Verdict |
|---|---|---|---|---|
| 1 | text-to-video `9:16` | B1 aspect (via B19 fix) | `completed` | ✅ B19 two-part fix holds in chain context: icon-ligature selector matched `crop_9_16` + pre-open guard correctly skipped chip click when `data-state="open"`. Persisted `project_url=https://labs.google/fx/tools/flow/project/bf4c75fa-e039-43bb-b994-bf7d6373138e` + `media_id=03fe613e-988d-4f29-b0b1-3d0603c916a1`. |
| 2 | camera-move `Dolly in` | B12 preset verify | `failed` | **Independent L2 inheritance gap (NOT B19).** Worker raised `Cannot navigate: no edit_url, project_url=, media_id=` — server's `claim_next_job` (`server/db/job_store.py`) currently inherits only `profile` from parent, NOT `project_url` / `media_id`. |
| 3 | insert-object bbox | B11 canvas drag | `pending` | Not reached — parent J2 failed. |

### Outcome

B19 fix (two-part) landed cleanly. B1 end-to-end **unblocked** in chain context. The downstream L2 inheritance bug is pre-existing (predates B19) and surfaces only once a chain gets past J1 — it was masked in Phase A Tier 1 because Tier 1 jobs were exercised individually, and masked in Tier 2 Runs 1-6 because no chain ever reached J2. Proposed **B22 (P0)**: extend `claim_next_job` to also inherit `project_url` + `media_id` from parent when L2+ job is claimed.

---

## Tier 2 — 2026-04-18 — Run 7 — ✅ **B19 FIX VERIFIED LIVE (single job)**

| Field | Value |
|---|---|
| Date | 2026-04-18 ~07:30 UTC (~14:30 local) |
| Profile | `ngoctuandt20` |
| Job type | single `text-to-video` (aspect 9:16) |
| B19 commit under test | `e1597b2` |
| Session report | [`docs/session-reports/2026-04-18_B19_aspect-chip-multiline.md`](session-reports/2026-04-18_B19_aspect-chip-multiline.md) |

### Verdict: ✅ PASS

First full green run of the aspect-ratio code path after B19 fix v3 landed. Engine output:
- Chip located via icon selector: `button[aria-haspopup="menu"]:has-text("crop_9_16")` matched directly (bypassing model-name text that was `"🍌 Nano Banana Pro\ncrop_9_16\nx1"`).
- Pre-click `get_attribute("data-state")` returned `"open"` — engine SKIPPED `chip.click()` per B19 guard and fell through to `wait_for("[role=\"menu\"][data-state=\"open\"]")` which resolved immediately.
- Portrait trigger clicked, chip verified `crop_9_16`, submit succeeded.
- Persisted: `project_url=https://labs.google/fx/tools/flow/project/f656f223-7e65-4309-bc34-cd39e9b3da24`, `media_id=f2f736d2-5094-4bdb-abc6-d4f8ed254ccb`.

### Invariants observed

| Invariant | Status | Evidence |
|---|---|---|
| INV-1 Account Binding | ✓ | `profile=ngoctuandt20` on J1 claim + completion |
| INV-3 Store Everything | ✓ | `project_url` + `media_id` persisted |
| R-CODE-3 Locale-Independent | ✓ | Icon ligature `crop_9_16` matches across models/locales |
| R-CC-1 No architecture restructure | ✓ | Single-function patch in `_set_aspect_ratio` |

---

## Tier 2 — 2026-04-18 — Run 6 — ❌ BLOCKED (live DOM diag — real root cause surfaced)

| Field | Value |
|---|---|
| Date | 2026-04-18 ~07:10 UTC |
| Profile | `ngoctuandt20` |
| Fix version | v2 (CSS `:has-text`, no state guard yet) — + temporary `DIAG aspect chip:` log line |
| Verdict | Same `Locator.wait_for: Timeout 3000ms` symptom, but diag log exposed true cause |

### Diagnostic output (critical finding)

```
DIAG aspect chip: { exists: true, dataState: 'open', innerText: '🍌 Nano Banana Pro\ncrop_9_16\nx1' }
```

Two facts that flipped B19's hypothesis from v1/v2 to v3:
1. **Chip text is NOT `"Video"`** — default model on this account is `"🍌 Nano Banana Pro"`. Pre-B19 regex `r"video.*x\d"` matched nothing.
2. **`data-state="open"` BEFORE `_set_aspect_ratio` called** — a prior interaction (likely `flow/model_selector.py::_open_model_dropdown` which uses `button:has-text('Video')` — same substring match as the chip's old-DOM label) left the aspect chip's Radix trigger pre-open. Unconditional `chip.click()` then TOGGLED CLOSED → subsequent `wait_for` timed out.

This run is the pivot: from "regex multi-line" (wrong hypothesis) to "text probe wrong + pre-open state" (real hypothesis). Triggered fix v3 (icon-ligature selector + state guard) → Run 7 ✅.

---

## Tier 2 — 2026-04-18 — Runs 4 + 5 — ❌ BLOCKED (fix v1/v2 still fail)

| Run | Fix version | Selector form | Verdict |
|---|---|---|---|
| 4 | v1 | `button:has(i.google-symbols:has-text(/crop_(9_16|16_9)/))` (nested `has=` with regex) | ❌ same timeout — selector resolved correctly in Playwright's eyes but click-toggle effect still closed the menu |
| 5 | v2 | `button[aria-haspopup="menu"]:has-text("crop_9_16"), …:has-text("crop_16_9")` (CSS `:has-text`, simpler form) | ❌ same timeout — simpler selector, same behavior |

**Lesson:** whichever selector resolved the chip, the `.click()` call happened on a trigger that was already open → toggle-closed the menu. Selector-only fixes could not succeed without a pre-open state check.

---

## Tier 2 — 2026-04-18 — Run 3 — ❌ BLOCKED (wrong hypothesis: `re.DOTALL`)

| Field | Value |
|---|---|
| Date | 2026-04-18 ~06:30 UTC |
| Fix v0 | `re.compile(r"video.*x\d", re.IGNORECASE \| re.DOTALL)` — added `re.DOTALL` flag so `.` crosses `\n` |
| Verdict | ❌ Same `Locator.wait_for: Timeout 3000ms` — DOTALL didn't help |

### Why fix v0 failed

Initial hypothesis was that chip `innerText` is `"Video\ncrop_9_16\nx1"` (multi-line) and regex `video.*x\d` needed `re.DOTALL` to cross the newlines. Unit-test-green (pattern matches multi-line string), but live run showed the **actual chip text did not start with `"Video"` at all** — default model had been switched to `"🍌 Nano Banana Pro"` since Phase A Tier 1 tag `db4c746`. Even with `DOTALL`, the `video` token was absent. Ran 1-line fix live → identical failure symptom → triggered Chrome MCP live DOM probe that surfaced the real root cause (Run 6).

---

## Tier 2 — 2026-04-18 — Run 2 — ⚠️ **PARTIAL** (B18 PASS, new B19 candidate blocker)

| Field | Value |
|---|---|
| Date | 2026-04-18 05:21 UTC (12:21 local) |
| Profile | `ngoctuandt20` (ULTRA tier — unchanged from Run 1) |
| Chain IDs | 2 sequential retries (both halted at same downstream point — first attempt + post-login re-click) |
| Jobs per chain | 3 (t2v 9:16 → camera Dolly in → insert bbox seagull) |
| LP consumed | 0 |
| Supervisor commit | `e618731` (master — pre-B18) |
| B18 commit under test | `8dc357c` (worktree `claude/brave-villani-73e607`) |
| Session report | [`docs/session-reports/2026-04-18_B18_homepage-locale-fix.md`](session-reports/2026-04-18_B18_homepage-locale-fix.md) |

### Per-job verdict

| # | Job | Target bug | Status | Verdict |
|---|---|---|---|---|
| 1 | text-to-video `9:16` | B1 aspect ratio | `failed` | **B18 PASS live** (homepage button clicked twice via icon selector, 2 projects created). **B19 candidate FAIL** (aspect-ratio chip panel never opens `[role="menu"][data-state="open"]`). |
| 2 | camera-move `Dolly in` | B12 preset verify | `pending` | Not reached — parent J1 failed at B19 candidate |
| 3 | insert-object bbox | B11 canvas drag | `pending` | Not reached — parent J2 never ran |

### B18 verification evidence (LIVE — ✅ PASS)

```
flow.operations.generate: Clicked new project via: button:has(i.google-symbols):has-text('add_2')
```

Same log line emitted on BOTH the initial attempt (before login re-check) AND the post-login re-click loop — proves the module-level `NEW_PROJECT_SELECTORS` constant is shared across both paths as contract-tested. Engine successfully transitioned from `https://labs.google/fx/tools/flow` → `/project/cf20a347-…/edit/...` (attempt 1) and again `/project/82fa5465-…/edit/...` (attempt 2). Pre-B18 this transition never happened — `RuntimeError("Failed to find '+ New project' button on Flow homepage")` fired at `generate.py:125` every time.

### Downstream blocker (NEW — B19 candidate, OUT OF B18 SCOPE)

```
error: Locator.wait_for: Timeout 3000ms exceeded.
       waiting for locator("[role=\"menu\"][data-state=\"open\"]")
```

Triggered at the aspect-ratio chip panel step. Chrome MCP DOM probe on the failing editor page (`/edit/82fa5465-…`) found:
- 6 `button[aria-haspopup="menu"]` buttons on the editor toolbar.
- The target chip (aspect) at y=599 carries multi-line text: `"Video\ncrop_9_16\nx1"` (newlines between tokens).
- Suspected root cause: B1's regex `re.compile(r"video.*x\d", re.IGNORECASE)` in `flow/operations/generate.py` lacks `re.DOTALL` — `.` does not match `\n`, so the chip is never found and a wrong (or no) click occurs, leaving the Radix menu closed.

### Invariants observed

| Invariant | Status | Evidence |
|---|---|---|
| INV-1 Account Binding | ✓ honored | `profile=ngoctuandt20` claimed both retries under same worker |
| INV-2 Navigate by `edit_url` | n/a | No L2+ nav |
| INV-3 Store Everything | partial | J1 failed pre-submit; `project_url` created client-side twice, not persisted (failed before L2+) |
| INV-4 / INV-5 | n/a | Chain halted pre-submit |
| **R-CODE-3 Locale-Independent** | ✓ **RESTORED** | B18 selector matches VI + EN via `add_2` icon ligature |
| R-CODE-10 No `datetime.utcnow()` | ✓ | Unchanged from Run 1 |
| B5 auto `completed_at` | ✓ incidental | Both J1 failures auto-stamped `completed_at` |
| B6 profile release | ✓ incidental | `ngoctuandt20` marked AVAILABLE after each terminal status |
| B4 chain aggregate | ✓ incidental | `status=failed` (rule #1) on both retries |

### Next action

B18 (homepage locale) is closed. Blocker moves to **B19 candidate — aspect-ratio chip regex/selector**. Propose:

1. **B19** — multi-line chip text breaks `re.compile(r"video.*x\d", re.IGNORECASE)`. Add `re.DOTALL` or switch to `[\s\S]*`; alternatively select by `aria-haspopup="menu"` + label sibling. P0 for any T2V. DOM probe session needed.
2. **B-stdout-encoding** (carried from Run 1, P2) — still open.

Until B19 lands, Tier 2 still cannot exercise B1 (aspect verify), B11 (bbox canvas), or B12 (camera preset) code paths on any profile. B18 alone was necessary but not sufficient to complete Tier 2.

---

## Tier 2 — 2026-04-18 — Run 1 — ⚠️ **BLOCKED**

| Field | Value |
|---|---|
| Date | 2026-04-18 04:51 UTC |
| Profile | `ngoctuandt20` (ULTRA tier — confirmed via page text) |
| Chain ID | `cd8ec66b-348f-4f49-a964-d1d11f5ca767` |
| Jobs | 3 (t2v 9:16 → camera Dolly in → insert bbox seagull) |
| LP consumed | 0 |
| Supervisor commit | `b80cc05` (master) |
| Session report | [`docs/session-reports/2026-04-18_Tier2_e2e-live.md`](session-reports/2026-04-18_Tier2_e2e-live.md) |

### Per-job verdict

| # | Job | Target bug | Job ID | Status | Verdict |
|---|---|---|---|---|---|
| 1 | text-to-video `9:16` | B1 aspect ratio | `9314caf4-…` | `failed` (21s) | Not reached — halted pre-aspect-ratio at Flow homepage button |
| 2 | camera-move `Dolly in` | B12 preset verify | `787cd278-…` | `pending` (never claimed) | Not reached — parent J1 failed |
| 3 | insert-object bbox | B11 canvas drag | `17e525e8-…` | `pending` (never claimed) | Not reached — parent J2 never ran |

### Root cause

`flow/operations/generate.py:125` raised `RuntimeError: Failed to find '+ New project' button on Flow homepage`.

Account **is** logged in (page text shows `ULTRA` tier + existing projects with dated edit/delete buttons) and LP **is** available (pre-run user confirmation: >3 slots). Flow homepage rendered Vietnamese despite engine appending `?locale=en`:

> `ULTRA / Apr 16, 08:49 PM / edit / Chỉnh sửa dự án / delete / Xoá dự án / …`

The English "+ New project" button locator misses the Vietnamese "Dự án mới" entry point — direct violation of **R-CODE-3 Locale-Independent** in `SPEC.md`.

### Invariants observed

| Invariant | Status | Evidence |
|---|---|---|
| INV-1 Account Binding | ✓ honored | Chain payload `profile=ngoctuandt20` → all 3 job rows stored that profile; J1 claim log shows `profile=ngoctuandt20` on `worker-1` |
| INV-2 Navigate by `edit_url` | n/a | No L2+ navigation occurred |
| INV-3 Store Everything | ✓ (vacuous) | J1 failed pre-submit → `project_url`/`media_id` stayed `null` (correct) |
| INV-4 Serial per Project | n/a | No project was created |
| INV-5 `media_id` stable | n/a | Never allocated |
| R-CODE-3 Locale-Independent | ❌ **VIOLATION** | Root cause of this BLOCKED run |
| R-CODE-10 No `datetime.utcnow()` | ✓ | All API timestamps ISO-8601 UTC with `Z` suffix |
| B5 auto `completed_at` | ✓ incidental | J1 `completed_at=2026-04-18T04:52:08.455557Z` after failure |
| B6 profile release | ✓ incidental | `Profile ngoctuandt20 marked AVAILABLE` log after J1 failure |
| B4 chain aggregate | ✓ incidental | `GET /api/chains/{id}` → `status=failed` (rule #1: any failed → failed), `progress.completed=0/3` |

### Next action

Blocked on a new P0 for non-English Google accounts. Proposed follow-up:

1. **B18 (propose)** — locale-independent Flow homepage new-project locator. Requires DOM probe session on `ngoctuandt20`. See session report §7 for fix-direction candidates.
2. **B-stdout-encoding (P2)** — Windows `cp1252` stdout encoder crashes on Vietnamese diagnostics. Inline `PYTHONIOENCODING=utf-8` or `sys.stdout.reconfigure(...)` in worker bootstrap.

Until B18 lands, Tier 2 cannot exercise any B1/B11/B12 code path on a non-English Google account. A rerun on an English-locale account (if one is available in the profile pool) might unblock B1/B11/B12 validation independently.

---

## Tier 1 — 2026-04-17 — Round 2 — ✅ PASS

`docs/session-reports/2026-04-17_Tier1r2_revalidation.md` — B11 canvas selector and B12 `getComputedStyle` verify both re-probed live on project `785d2255-…/edit/f1994aba-…`. Threshold margins: bbox canvas 479×269 (pass ≥300×200); camera color sum 144 vs 765 (pass <400). Evidence recorded in SPEC.md §D.4 B11/B12.

## Tier 1 — 2026-04-17 — Round 1 — ⚠️ B2/B3 flipped

`docs/session-reports/2026-04-17_Tier1_dom-validation.md` — revealed B2 and B3 initial fixes targeted non-existent DOM elements. Spawned B11 and B12 as supersessions.

---

_Maintained per WORKPLAN §5.3 — append new attempts at the top._
