# PRD: FlowEngine batch-dispatch (1 Chrome → N submits)

**Status:** open / not implemented
**Branch:** `claude/batch-dispatch-l2-siblings`
**Owner:** TBD
**Created:** 2026-05-03 (rewritten 2026-05-04 with concrete spine + L1-first scope)

---

## 0. Spine — anchor concepts (read this once, never re-derive)

### 0.1 Levels (canon — supersedes any prior memory)

| Term | Meaning | Identifying fields |
|---|---|---|
| **Project** | Flow project = container, owns a `project_url` (one Google account). Created either by UI ("New project" empty) or implicitly by the first L1 submitted from the composer landing. Holds N L1 generations. | `project_url` |
| **L1** (`job_level=1`) | Generation **inside** a project: `text-to-video` / `text-to-image` / `frames-to-video` / `ingredients-to-video`. **N L1 can coexist in one project** = "L1 siblings". Mints a new `media_id`. No `parent_job_id`. | `project_url`, `media_id`, `chain_id` |
| **L2** (`job_level=2`) | Op on a specific L1 output: `extend-video` / `camera-move` / `insert-object` / `remove-object`. `parent_job_id` = L1. **N L2 sharing the same L1 parent** = "L2 siblings". Same profile + same project as parent. | + `parent_job_id` (= L1) |
| **L3+** (`job_level≥3`) | Op stacked on L2/L3 output. `parent_job_id` = immediate L2/L3 job. Same project + profile. | + `parent_job_id` (= L2/L3) |

### 0.2 What "batch dispatch" means

Engine today: 1 worker dispatch = 1 Chrome = open → submit → wait → download → close. Jobs serial.

Batch: 1 worker dispatch = 1 Chrome = N successive submits → parallel poll → sequential download → close. The whole batch shares one Flow page and one Chrome lifecycle.

### 0.3 Three target topologies (each unlocked in its own phase)

```
Phase 1 — L1 siblings same project (FOUNDATION)
  Project P (newly created)
  ├── L1a  text-to-video                 ← submit 1
  ├── L1b  text-to-video                 ← submit 2  (same /project/{id})
  └── L1c  text-to-video                 ← submit 3

Phase 2 — L2 siblings on one L1
  Project P
  └── L1a (done)
        ├── L2a  extend                   ← submit 1  (in /edit/{L1a.media_id})
        ├── L2b  camera-move "Dolly in"   ← submit 2
        └── L2c  camera-move "Orbit left" ← submit 3

Phase 3 — L3 siblings on one L2 (and beyond)
  Project P
  └── L1a → L2a (done) → L3 siblings stacked similarly
```

Phase 1 is non-negotiable foundation: it proves metadata isolation between successive submits in one Chrome. Every higher level depends on the same isolation primitives. **If Phase 1 leaks metadata, no further phase is safe.**

### 0.4 Metadata isolation — the load-bearing invariant

When N submits happen back-to-back in one Chrome, Flow emits N independent network/DOM events. The engine must keep them disjoint. Concretely:

| Per-submit signal | Today (1-1-1) | Batch invariant |
|---|---|---|
| `gen_id` (Flow generation uuid) | `client._gen_id` single attr, set in `submit_with_confirmation` | Captured into a **per-submit dict** at submit time, never read from `client._gen_id` later |
| Network `/pq/api` events | `client._calls` list, cleared via `client.clear_captures()` before submit | **Never cleared mid-batch.** Per-submit slice = `client._calls[calls_before:calls_after]` recorded at submit boundaries |
| Final `media_id` | resolved post-wait via `resolve_final_media_id(page, parent_media_id, download_media_ids)` | Resolution scoped to **this submit's** network slice + parent_media_id; does not look at sibling submits' mids |
| Output files | `download_video(client, media_ids=[...])` filters network for matching mid | Same, but `media_ids` parameter is **the resolved per-submit mid only**, not the union |
| WS progress events | Worker streams `{job_id, progress}` per job | Progress emitter must key by `gen_id`, not by "active job" |

Any path that says "use the latest captured X" or "clear captures and grab next" is broken under batch — those are 1-1-1 idioms that silently merge sibling state.

### 0.5 Default behavior is unchanged

`FLOW_BATCH_DISPATCH=1` opts in. Default OFF → legacy `dispatch_job` runs, every legacy wrapper (`run_generate`, `extend_video`, `camera_move`, `insert_object`, `remove_object`) is byte-identical in behavior. **No changes to production default.**

---

## 1. Problem

User fan-out 1 project + 3 L1 (or 1 L1 + 3 L2 / 1 L2 + 3 L3) → engine runs SERIAL (~9-12 min for 3 jobs).

Flow UI **natively** allows multi-gen concurrent (verified 2026-05-03: in 1 Chrome tab, click composer + submit 3 times back-to-back, Flow runs 3 gens in parallel). Engine has not exploited this.

Wall-time goal: `max(gen_time) + N × (submit + download)` ≈ 4-5 min instead of 9-12.

---

## 2. CRITICAL — DO NOT BREAK STABLE 1-1-1

### 2.1 Branch isolation

- Branch off `master` into `claude/batch-dispatch-l2-siblings`. No direct commits to master.
- No force-push, no rebase of master.
- No PR merge until user approves.
- No edits to production env file `/etc/flowengine/flowengine.env`. Live-verify uses inline env (`FLOW_BATCH_DISPATCH=1 python ...`).

### 2.2 Feature gate (mandatory at every entrypoint)

```python
if os.environ.get("FLOW_BATCH_DISPATCH", "0").strip() == "1":
    # new batch path
else:
    # legacy path (UNCHANGED)
```

Default OFF. Production only enables when user explicitly sets the env.

### 2.3 Legacy wrappers preserved

Refactor each op into 3 phases **but keep the legacy entrypoint as a wrapper** that calls submit → wait → download in sequence. Pre-existing call sites (legacy `dispatch_job`, tests, scripts) keep working unchanged.

```python
# flow/operations/extend.py
async def submit_extend(client, job) -> dict:        # NEW: click submit + capture gen_id
    ...
    return {"gen_id": ..., "submit_ts": ..., "calls_before": ..., "calls_after": ...}

async def wait_for_gen(client, gen_id, *, parent_media_id, calls_window) -> dict:  # NEW
    ...
    return {"media_id": ..., "edit_url": ..., "status": "completed"}

async def download_gen(client, media_id, prefix) -> list[str]:                       # NEW
    ...

async def extend_video(client, job, ...):  # LEGACY — DO NOT REMOVE / RENAME
    sub = await submit_extend(client, job, ...)
    res = await wait_for_gen(client, sub["gen_id"],
                             parent_media_id=job.get("media_id"),
                             calls_window=(sub["calls_before"], sub["calls_after"]))
    files = await download_gen(client, res["media_id"], "ext")
    return {**res, "output_files": files, "generation_id": sub["gen_id"], ...}
```

Same shape for `camera.py`, `insert.py`, `remove.py`, `generate.py`. The shared post-submit code in `flow/operations/_base.py:finalize_operation` splits into `wait_for_gen_generic()` + `build_completed_result()` helpers; legacy `finalize_operation` becomes a wrapper around the two.

### 2.4 Tests must verify legacy does NOT regress

- Existing pytest count (681 today) must all pass.
- Add `tests/test_<op>_legacy_wrapper.py` per op — snapshot legacy output identical to current monolithic version (mocked FlowClient + recorded responses).
- New batch tests are additive, not substitutional.

### 2.5 DO NOT touch

- `dispatch_job`, `run_generate`, `extend_video`, `camera_move`, `insert_object`, `remove_object` — keep names & signatures.
- `ProjectLock` default cap (still 1).
- Worker `max_concurrent` default (still 1).
- `FLOW_USE_BASE_PROFILE` default (still 1).
- `flow/client.py`, `flow/login.py`, `worker/profile_swapper.py`, `worker/profile_manager.py` — no edits in this PR.
- No amend / force-push / interactive rebase.

### 2.6 Rollback safety

If a problem surfaces in production: revert PR + production auto-falls-back to legacy (because `FLOW_BATCH_DISPATCH` unset = OFF).

---

## 3. Phase 1 — Batch 3 inflight L1 same-project (FOUNDATION)

### 3.1 Target scenario

1. Worker creates a fresh Flow project on profile `ngoctuandt20` (single-profile worker, `FLOW_USE_BASE_PROFILE=1`).
2. Inside `/project/{id}`, submits 3 `text-to-video` jobs back-to-back (no wait between submits).
3. Captures 3 distinct `gen_id`s from network events.
4. Parallel-polls all 3 to completion.
5. Downloads each in turn.
6. Writes 3 distinct `media_id`s + 3 distinct `output_files` to DB.
7. Closes the Chrome.

### 3.2 Files touched

#### `flow/operations/generate.py`

Split `run_generate` into:

- `submit_generate(client, job, *, project_already_open: bool) -> dict`
  - If `not project_already_open`: navigate to composer (`/`), submit first gen → mints project → wait until `/project/{id}` URL settles → return `{gen_id, project_url, calls_before, calls_after, submit_ts}`.
  - If `project_already_open`: assume page is on `/project/{id}`; just type prompt + click submit; capture `gen_id`. Project_url already known by caller.

- `wait_for_l1_gen(client, gen_id, *, calls_window) -> dict`
  - Polls Flow for completion of this specific `gen_id` only. Resolves final `media_id` from network mids in `calls_window` (never from `client._gen_id` or "latest tile"). Returns `{media_id, status, edit_url, error?}`.

- `download_l1_gen(client, media_id, prefix="t2v") -> list[str]`
  - Downloads the 1080p mp4 for `media_id`. Network filter scoped to this mid only.

- Legacy `run_generate(client, job)` becomes:
  ```python
  sub = await submit_generate(client, job, project_already_open=False)
  res = await wait_for_l1_gen(client, sub["gen_id"], calls_window=(sub["calls_before"], sub["calls_after"]))
  if res["status"] != "completed":
      raise RuntimeError(...)
  files = await download_l1_gen(client, res["media_id"])
  return {**res, "project_url": sub["project_url"], "output_files": files,
          "generation_id": sub["gen_id"], "profile": client.profile_name}
  ```
  Output dict shape **unchanged** vs today.

#### `flow/operations/_batch.py` (NEW)

```python
async def batch_dispatch_l1_same_project(
    client: FlowClient,
    l1_jobs: list[dict],   # 3 t2v jobs, no project_url yet
) -> list[dict]:
    """Submit N L1 t2v jobs back-to-back into a freshly-created project.

    Returns list of per-job results in input order. Each result either
    carries {project_url, media_id, edit_url, output_files, generation_id,
    profile, status='completed'} or {status='failed', error}.
    """
    if not l1_jobs:
        return []

    submits: list[dict] = []  # [{job_id, gen_id, project_url, calls_window, submit_ts}, ...]
    project_url = None

    # Phase A — sequential submits (cannot parallelize: must wait for /project/ URL after submit #1)
    for idx, job in enumerate(l1_jobs):
        try:
            sub = await submit_generate(
                client, job,
                project_already_open=(idx > 0),
            )
            if idx == 0:
                project_url = sub["project_url"]
            else:
                sub["project_url"] = project_url
            submits.append({"job": job, "submit": sub, "ok": True})
        except Exception as e:
            logger.exception("L1 submit %d/%d failed: %s", idx+1, len(l1_jobs), e)
            submits.append({"job": job, "submit": None, "ok": False, "error": str(e)})

    # Phase B — parallel wait
    async def _wait_one(s):
        if not s["ok"]:
            return {"status": "failed", "error": s["error"]}
        sub = s["submit"]
        try:
            return await wait_for_l1_gen(
                client, sub["gen_id"],
                calls_window=(sub["calls_before"], sub["calls_after"]),
            )
        except Exception as e:
            return {"status": "failed", "error": str(e)}

    waits = await asyncio.gather(*[_wait_one(s) for s in submits])

    # Phase C — sequential downloads (avoid parallel CDP attach contention)
    results = []
    for s, w in zip(submits, waits):
        job = s["job"]
        if not s["ok"] or w.get("status") != "completed":
            results.append({
                "job_id": job["id"],
                "status": "failed",
                "error": (s.get("error") or w.get("error") or "unknown"),
                "project_url": project_url or "",
            })
            continue
        try:
            files = await download_l1_gen(client, w["media_id"])
        except Exception as e:
            results.append({
                "job_id": job["id"],
                "status": "failed",
                "error": f"download: {e}",
                "media_id": w["media_id"],
                "project_url": project_url,
            })
            continue
        results.append({
            "job_id": job["id"],
            "status": "completed",
            "project_url": project_url,
            "media_id": w["media_id"],
            "edit_url": w["edit_url"],
            "output_files": files,
            "generation_id": s["submit"]["gen_id"],
            "profile": client.profile_name,
        })

    return results
```

Module also exports placeholders for Phase 2/3 (`batch_dispatch_l2_siblings`, `batch_dispatch_l3_siblings`) raising `NotImplementedError` so the import surface is stable.

#### `worker/dispatcher.py`

Add (do not modify `dispatch_job`):

```python
async def dispatch_batch_l1_same_project(
    jobs: list[dict],
    profile_manager,
    project_lock,
) -> list[dict]:
    """Open one Chrome, batch-submit N L1 t2v jobs into a fresh project."""
    # acquire profile
    # launch FlowClient (existing helper)
    # call batch_dispatch_l1_same_project
    # release profile
```

#### `server/db/job_store.py`

```python
async def list_pending_l1_siblings(project_url: str | None,
                                   profile: str | None,
                                   limit: int = 5) -> list[Job]:
    """Pending L1 jobs that share project_url (or all unclaimed L1 if project_url is None).
    ORDER BY created_at ASC.
    """
```

For Phase 1 the worker calls this with `project_url=None` after claiming the first unclaimed L1, to find "siblings to be merged into the new project we're about to create" — bounded by `limit`. Phase 2 will add `parent_job_id` variant.

#### `server/routes/jobs.py`

```python
@router.get("/jobs/l1-siblings")
async def get_pending_l1_siblings(
    project_url: str | None = Query(None),
    profile: str | None = Query(None),
    limit: int = Query(5, ge=1, le=10),
):
    return await job_store.list_pending_l1_siblings(project_url, profile, limit)
```

#### `worker/remote_api.py`

```python
async def list_pending_l1_siblings(self, project_url=None, profile=None, limit=5) -> list[dict]:
    ...
```

#### `worker/main.py`

```python
if FLOW_BATCH_DISPATCH and claimed["job_level"] == 1 and claimed["type"] == "text-to-video":
    siblings = await api.list_pending_l1_siblings(project_url=None, profile=claimed["profile"], limit=BATCH_L1_MAX)
    siblings = [s for s in siblings if s["id"] != claimed["id"]]
    if siblings:
        # claim siblings (one by one — Phase 1 single-worker; race-safe atomic claim deferred to Phase 2)
        batch = [claimed] + claimed_siblings
        results = await dispatcher.dispatch_batch_l1_same_project(batch, profile_mgr, project_lock)
        for r in results:
            await api.update_job(r["job_id"], r)
        return
# else: legacy dispatch_job path (UNCHANGED)
```

`BATCH_L1_MAX` = `int(os.environ.get("FLOW_BATCH_L1_MAX", "3"))`. Default 3.

### 3.3 Tests (Phase 1)

Mocked Playwright + recorded network events.

- `tests/test_batch_l1_metadata_isolation.py`
  - 3 simulated submits with distinct `gen_id`s and distinct network mids.
  - Asserts each per-submit `calls_window` slice is disjoint, no overlap of `gen_id`s, `resolve_final_media_id` returns the correct mid per submit (not "the latest" of all 3).
  - Asserts `client._gen_id` is **not** read after batch begins (test patches it to a sentinel; if the new code reads it, test fails).
  - Asserts results returned in input order.
- `tests/test_batch_l1_partial_failure.py`
  - Submit #2 raises mid-batch → result[1] = failed, results[0] and [2] still complete.
  - Wait timeout on #3 → result[2] = failed with `error="timeout"`, others ok.
- `tests/test_generate_legacy_wrapper.py`
  - Snapshot legacy `run_generate` output dict for a recorded scenario; confirm post-refactor wrapper produces the byte-identical dict.
- `tests/test_l1_siblings_api.py`
  - GET endpoint filters by profile + project_url + status correctly. Order ASC by created_at. Limit honored.
- `tests/test_worker_batch_l1_claim.py`
  - 3 pending unclaimed L1 with `FLOW_BATCH_DISPATCH=1` → worker batch dispatches all 3.
  - 1 pending L1 (no siblings) → worker still calls batch dispatcher (with N=1) — verifies the batch path also works in degenerate N=1 case (so we don't carry two paths post-Phase-3).
  - `FLOW_BATCH_DISPATCH=0` → legacy `dispatch_job` called; batch dispatcher untouched.

### 3.4 Live verify (Phase 1)

Acceptance:

- `journalctl -u flowengine-worker` shows exactly **1** "Launching Chrome CDP" line for the batch.
- 3 distinct `gen_id`s captured.
- DB: 3 rows status=completed, 3 distinct `media_id`s, 3 distinct `output_files`, all sharing the same `project_url` and `profile`.
- 3 video files on disk; each plays a different generation.
- Wall-time < 6 min (vs ~10 min serial reference).
- WS stream during run shows 3 separate progress channels keyed by `job_id`, no event mis-routed across jobs.

Procedure:

```bash
ssh debian-root
sudo systemctl stop flowengine-worker   # only if user approves
# Inline-env runner — does NOT touch /etc/flowengine/flowengine.env
sudo -u flowengine env FLOW_BATCH_DISPATCH=1 FLOW_BATCH_L1_MAX=3 \
  /opt/flowengine/.venv/bin/python -m worker.main --once-batch
# Check DB
sqlite3 /opt/flowengine/data/flowengine.db "SELECT id, project_url, media_id, status FROM jobs ORDER BY created_at DESC LIMIT 3"
```

Test data prep: 3 `text-to-video` rows inserted via `POST /api/jobs` with the same `chain_id` and unique prompts (e.g. "a red cat", "a blue dog", "a yellow bird"), no `project_url`, `profile=ngoctuandt20`.

Credit cost: 3 × t2v 1080p ≈ 3 × 1 credit (free LP model) or 3 × ~50 credits if LP unavailable post-2026-05-10. Phase 1 must run before LP EOL or use Lite fallback.

### 3.5 Phase 1 acceptance gate

- Tests green (existing 681 + ~18 new).
- Live verify pass per §3.4.
- No edits to legacy callers' behavior (snapshot tests confirm).
- Memory `feedback_batch_dispatch_l1_metadata.md` written: which signals must be per-submit-scoped, which idioms break under batch.

**Only after Phase 1 passes, proceed to Phase 2.**

---

## 4. Phase 2 — Batch L2 siblings on one L1

### 4.1 Target scenario

1 L1 already done (from Phase 1 or earlier). 3 L2 children pending: 1 extend + 1 camera "Dolly in" + 1 camera "Orbit left", same `parent_job_id`, same `profile`.

Worker batch-dispatches: navigate `/edit/{L1.media_id}` once, submit 3 ops back-to-back, parallel poll, sequential download.

### 4.2 Files

- `flow/operations/extend.py`, `camera.py`, `insert.py`, `remove.py` — same 3-phase split + legacy wrapper as `generate.py` in Phase 1.
- `flow/operations/_base.py` — split `finalize_operation` into `wait_for_gen_generic(client, gen_id, *, parent_media_id, calls_window, job_type)` + `build_completed_result(...)`. Legacy `finalize_operation` becomes thin wrapper.
- `flow/operations/_batch.py` — implement `batch_dispatch_l2_siblings(client, parent_edit_url, parent_media_id, l2_jobs)`. Type-dispatches to per-op `submit_X`. Same Phase A/B/C structure as Phase 1.
- `worker/dispatcher.py` — `dispatch_batch_l2_siblings(...)`.
- `server/db/job_store.py` — `list_pending_l2_siblings(parent_job_id, profile)`.
- `server/routes/jobs.py` — `GET /api/jobs/l2-siblings?parent_job_id=...&profile=...`.
- `worker/remote_api.py` — corresponding client method.
- `worker/main.py` — extend the gate: after claiming an L2, peek L2 siblings sharing `parent_job_id` + same profile.

### 4.3 Per-op metadata invariants (extra over Phase 1)

- `extend-video` always mints NEW media_id (per existing convention). Resolution must produce 3 distinct mids when 3 extends are batched on the same L1 — none equal to `parent_media_id`.
- `camera-move` mints NEW on early-chain (L2 direct off L1). 3 batched cameras → 3 distinct mids.
- `insert-object` / `remove-object` mint NEW outputs (resolved 2026-04-23 via network mids). Bbox is per-job; `draw_bbox_on_video` must run on a freshly opened mode panel each iteration.

The mode panel state changes per submit (Extend → Camera → Insert). The batch helper must explicitly reset mode panel before each submit (not rely on whatever was open from previous iteration).

### 4.4 Tests + live verify (Phase 2)

Mirrors Phase 1 structure but on L2:

- Mocked: 3 mixed-type L2 batched → distinct gen_ids, distinct mids, distinct output_files, mode panel reset between submits.
- Live: 1 L1 done from Phase 1 + 3 L2 children → 3 completed, 3 distinct mids, wall-time < 6 min. DB intact.

---

## 5. Phase 3 — L3 siblings on one L2 (and beyond)

Same primitives as Phase 2. The only material change:

- L3 `parent_job_id` = L2 (not L1). Inheritance walks one step (B22 invariant: child inherits direct parent's `media_id` + `edit_url` together).
- `_base.py` already handles deep-chain `media_id` resolution correctly post-2026-04-23.

Files touched: just `_batch.py` adds `batch_dispatch_l3_siblings` + `worker/main.py` extends the gate condition. Op submit/wait/download functions are reused as-is from Phase 2 — they don't care which level their inputs are at.

Tests: 1 L3 batch (3 extend on one L2 output). Live: 1 L1 + 1 L2 + 3 L3 batched.

---

## 6. Open architectural decisions

### 6.1 Atomic batch claim

Phase 1 single-worker setup → naive "claim 1, peek N-1, claim each" is acceptable. Race window only matters with 2+ workers.

If we ship multi-worker before Phase 3 lands → add `POST /api/jobs/batch-claim` that atomically transitions `pending → claimed` for a list of ids in one transaction. Until then, mark in code with `# TODO(batch-claim-atomicity)`.

### 6.2 Per-submit gen_id capture API

Currently `submit_with_confirmation` writes `client._gen_id` as a side effect. Two options:

- **A.** Refactor `submit_with_confirmation` to **return** `gen_id` (and stop writing the attr). Legacy callers updated.
- **B.** Keep the side-effect for legacy; new `submit_X` reads `client._gen_id` immediately after `submit_with_confirmation` returns, into a local variable, then proceeds. The attr is overwritten by next submit but local copy is preserved per-submit.

Phase 1 decision: **option B**. Smaller blast radius; option A can be a later cleanup.

### 6.3 Project lock semantics under batch

`ProjectLock` ensures one job per `project_url`. Under batch: one **batch** holds the lock for the duration of all N submits + waits + downloads. Lock acquired before navigation, released after Chrome close. No change to the lock primitive.

---

## 7. Out of scope for this PR

- Multi-worker atomic batch claim.
- Multi-Chrome batch (e.g. 2 batches × 3 jobs each across 2 profiles in parallel) — covered by existing `ALLOW_SAME_PROFILE_CONCURRENCY` + `FLOW_PROJECT_INFLIGHT` work; orthogonal.
- WebSocket multiplexing optimizations.
- UI updates to chain-builder for "batch" affordance — engine-only PR.
- L1 of types other than `text-to-video` in Phase 1 (image / frames / ingredients can use the same primitives once their `submit_X` is split — defer to follow-up).

---

## 8. Acceptance for the PR

- Phases 1 + 2 + 3 implemented behind `FLOW_BATCH_DISPATCH=1` gate (default OFF).
- All legacy ops have 3-phase + wrapper structure; legacy entrypoints unchanged.
- pytest count = 681 (existing) + ~50 (new) all pass.
- Live verify pass on debian-root for at least Phase 1 (Phase 2/3 may be deferred to a separate live-verify session if user approves).
- No edits to systemd env / production config / `flow/client.py` / login / profile manager.
- PR body cites this PRD + bullet evidence per phase.
- Memory written:
  - `feedback_batch_dispatch_l1_metadata.md` (signals to scope per-submit)
  - `feedback_l1_siblings_only.md` updated (multi-branch L2 on same project now allowed via batch)

---

## 9. Production access (for live verify)

- SSH alias: `debian-root` (192.168.86.42)
- FlowEngine root: `/opt/flowengine/`
- DB: `/opt/flowengine/data/flowengine.db` (sqlite3, read-only OK from Claude)
- Profile in use: `ngoctuandt20` (warmed)
- Logs: `/opt/flowengine/logs/{server,worker}.out`
- `systemctl restart flowengine-worker` — ONLY when user explicitly approves

---

## 10. Time estimate

- Phase 1: ~0.5 day (foundation, narrowest surface)
- Phase 2: ~1 day (4 op refactors + per-op submit functions)
- Phase 3: ~0.25 day (reuses Phase 2 primitives)
- Live verify: ~30 min × 3 phases = 1.5 hr (incl. TOTP if profile burns)
- Total: ~2-3 days dev + verify

---

## 11. Done

When done: open PR vs master with body linking this PRD + bullet evidence per phase. Memory written. Production env unchanged.
