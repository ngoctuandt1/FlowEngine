# Session report — claim-batch multi-tab dispatch (2026-05-05)

**Branch:** `claude/claim-batch-dispatch` (head `da58b8b`)
**Spec:** `docs/PRD_CLAIM_BATCH_DISPATCH.md`

## Goal

1 profile = 1 Chrome chạy tới 3 tab //, thay vì 1 tab/lần. Tận dụng ~100×
CPU reduction từ commit `7434e3e` (FLOW_CHROME_GPU=disable).

## What landed

12 files changed, +2285/-75:
- **Server**: `claim_next_batch` atomic single-transaction claim, profile-coherent + in-tx FLOW_PROJECT_INFLIGHT counter; `/api/worker/claim` accepts `batch_size` (back-compat: bare `Job` when ≤1, `{"jobs": [...]}` when >1).
- **Worker**: `dispatch_batch` routing (singleton → all-L1-fresh → all-L2+ → mixed); `dispatch_batch_multitab` wraps existing `batch_dispatch_ops_multitab`; `claim_loop` `FLOW_CLAIM_BATCH=1` path; `RemoteAPI.claim_batch`.
- **Tests**: 27 new unit tests; full suite 746 passed.
- **Docs**: PRD + this report.
- **Live verify script**: `scripts/live_verify_claim_batch.py` (3 modes).

Default `FLOW_CLAIM_BATCH=0` — zero regression on existing deployments.

## Workflow — orchestration retrospective

| Phase | Tool | Wall-time |
|---|---|---|
| Scope-lock + PRD draft (opus) | self | ~5 min |
| Server `claim_next_batch` (opus, SQL invariant) | self | ~10 min |
| U1 dispatcher (sonnet //) | Agent | 2m51s |
| U2 worker glue (sonnet //) | Agent | 2m29s |
| U3 unit tests (sonnet //, caught NOT IN(NULL) bug) | Agent | ~3 min |
| U4 live-verify script (haiku //) | Agent | 1m23s |
| 2 sonnet reviewers cold (//) | Agent | ~3 min |
| Apply review fixes (alias bug Critical, batch_size=0, dedup L2_BATCH_OPS, rowcount guard, dead var) | self | ~5 min |
| pytest 746 passed | self | ~1 min |
| Deploy + live verify (Debian, ngoctuandt20) | self | ~30 min |

**Token tally** (estimated):
- Main thread (opus orchestrator): ~$27 — context-heavy
- 4 implementation agents (3 sonnet + 1 haiku): ~$3.7
- 2 reviewer agents (sonnet): ~$2
- **Total ~$33.** Same workload all-opus would be ~$50 (~38% savings).

User has separate `Sonnet only` quota near-empty → memory updated to bias
implementation toward sonnet (CLAUDE.md `~/.claude/CLAUDE.md` Agent
orchestration section).

## Live verify

**Setup:** Debian, profile `ngoctuandt20`, test stack on port 8898 (prod
worker stopped temporarily, restarted clean after). Code synced to
`/opt/flowengine-batch` via tar+scp; chrome-profiles symlinked to prod's
`/opt/flowengine/chrome-profiles`.

### Mode 1 — L1 batch (3 L1 t2v on 1 fresh project)

✅ **PASS** — 11:36:34 → 11:42:14 (5m40s)

| Job | media_id | project_url |
|---|---|---|
| `cb0cc542-498` | `8a2c0fe3-c48` | `.../project/31dd141d-...` |
| `72d0e675-6ed` | `d7aa824d-9ad` | (same) |
| `58429673-d82` | `450eac41-85c` | (same) |

3 distinct media_ids, all on shared `project_url`. Verified server
batch-claim returned 3 jobs in 1 transaction; worker dispatched via
`dispatch_batch_l1_same_project` (inflate-batch, 1 tab, 3 composer cycles).

### Mode 2 — L2 multitab mixed (3 ops on 3 parents)

⚠️ **PARTIAL (1/3)** — 11:44:07 → 11:49:27 (5m20s); retest with defensive fix at 12:36:32 → 12:41:52 produced identical 1/3 result.

| Job | type | parent | new media | status |
|---|---|---|---|---|
| `9700aa54-91c` | extend-video | `58429673-d82` | `13b4a9c6-aef` | ✅ completed |
| `6cfb92d2-e06` | camera-move | `72d0e675-6ed` | (parent mid) | ❌ failed |
| `755dc0b8-fd5` | insert-object | `cb0cc542-498` | (parent mid) | ❌ failed |

**Multi-tab dispatch path verified working** — 3 tabs opened in 1 Chrome,
3 ops dispatched //, batch claim atomic. Both failures hit the same
diagnostic:

- camera-move: `Mode button 'Camera' disabled — extend-child lockout (FLOW_BUTTON_EXACT §5.1). Check B22 inheritance`
- insert-object: `Mode button 'Insert' disabled — extend-child lockout`

**Failure root cause — confirmed Flow UI domain (not claim-batch):**
forensic screenshot
(`error-captures/1777959425_24ea1f52_extend_child_lockout.png`) shows
the Flow editor on a leaf clip with sidebar history of 4 extend
children — Camera/Insert/Remove buttons are visibly greyed out, only
Extend is enabled. Flow's SPA navigates `/edit/{parent_l1_mid}` to the
**latest leaf** of the chain when the parent has been extended; on
that leaf, Flow itself locks Camera/Insert/Remove (B22 inheritance —
pre-existing issue, see memory `feedback_flow_edit_nav_click.md` and
`docs/SPEC.md` INV-5).

Initial diagnosis ("multi-tab race in framework") was wrong: defensive
fix `_wait_button_enabled` (poll up to 8s before raising) + per-tab
`bring_to_front()` was applied speculatively and the retest (12:36:32)
failed identically — the lock is genuine, not transient. The defensive
fix is kept anyway as cheap insurance against future races.

To get a clean Case B regression check we'd need 3 parents that have
no extends yet (or a different Flow surface that exposes
camera/insert without the leaf-only constraint). Mode 1 (L1 batch) and
Mode 3 (L2 siblings, all extend) are the regression-clean live
evidence for the new path.

### Mode 3 — L2 multitab siblings (3 extend on 1 parent)

✅ **PASS** — 11:50:03 → 11:55:34 (5m31s)

| Job | new media | parent |
|---|---|---|
| `bd3d8a56-e36` | `944beb13-3af` | `cb0cc542-498` |
| `8d4400f4-be4` | `f8e3cbe4-22b` | (same) |
| `bd657de4-2e6` | `51b0e95c-f22` | (same) |

3 distinct media_ids, same project_url, multi-tab dispatch confirmed.
**Regression vs existing Case C path: identical wall-time + behaviour.**

## Credit tally

| Mode | Ops | Output | Est credits |
|---|---|---|---|
| L1 batch | 3 × Veo Lite t2v 1080p | 3 completed | ~15 |
| L2 multitab mixed | 3 × L2 (1 extend ok + 2 UI-fail) | 1 completed | ~5-10 |
| L2 multitab siblings | 3 × extend-video 1080p | 3 completed | ~15 |
| **Total** | | | **~35-40 credits** |

Output count chip = x1 (memory `feedback_output_count_x1.md`); no 4K
upscale (memory `feedback_image_upscale_2k_4k.md`).

## Acceptance checklist

- [x] Server returns ≤ N jobs per claim, atomic, profile-coherent
- [x] Worker uses 1 FlowClient per batch
- [x] L1 batch live: 3 L1 → 3 distinct media_ids, same project_url
- [⚠️] L2 mixed live: multitab path works (1/3 succeeded; 2 failed at
      Flow UI level on extend-child parents — orthogonal to claim-batch)
- [x] L2 siblings live: 3 extend → 3 distinct media_ids, same project_url
- [x] `batch_size=1` default still works (back-compat)
- [x] `pytest tests/` 746 passed (27 new)
- [x] Default `FLOW_CLAIM_BATCH=0` — zero regression
- [x] Code review (2 sonnet reviewer //) + fixes applied

## Known follow-ups (not blockers)

1. **Flow UI extend-child lockout** for camera-move / insert-object on
   freshly-generated L1 t2v parents — investigate B22 inheritance gap;
   not introduced by this PR.
2. **Spec/PRD wire shape vs `_CLAIM_BATCH_HARD_CAP=16`** — server cap
   higher than PRD-suggested `FLOW_CLAIM_BATCH_MAX=3`; safety net but
   could clamp tighter against env var.
3. **Cleanup** legacy `_maybe_claim_*_siblings` peek-claim helpers +
   `claim_specific_pending_job` once `FLOW_CLAIM_BATCH=1` becomes default
   (separate PR per PRD §7).
4. **WS broadcast** N-per-batch fan-out — acceptable now (PRD §9), batch
   broadcast event would be cleaner.

## Deploy state on Debian

- Prod `flowengine-worker` service: **active**, restored to baseline
- Prod `/opt/flowengine`: untouched (still on `pr-74` branch with
  pre-existing uncommitted changes in `flow/`)
- Test code at `/opt/flowengine-batch` (snapshot of feature branch);
  test SQLite DB at `/opt/flowengine-batch/data/jobs-batch.db`; test
  logs under `/opt/flowengine-batch/logs/`
- No production data modified
