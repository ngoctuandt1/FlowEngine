# Phase A — E2E Results

> Live engine E2E validation log per `docs/WORKPLAN.md` §5 / §7 Meta.
> Format: one section per attempt, most recent first. `Tier 1 = DOM probe via Chrome MCP`; `Tier 2 = full engine-driven chain via REST API`.

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
