# STUCK_JOB_AUDIT — 2026-05-17

> Comprehensive read-only audit of every way a FlowEngine job can get stuck/deadlocked/leak resources. Triggered by production incident: 6 stuck-pending jobs observed on https://ai.hassio.io.vn (cleaned manually 2026-05-17).
>
> Source: opus agent audit. Master HEAD at audit time: `23bc718`.

## Scope & method

Read of `server/db/job_store.py`, `server/routes/{worker,jobs,ws}.py`, `server/app.py`, `server/db/database.py`, `server/models/job.py`, `worker/{main,dispatcher,project_lock,profile_manager}.py`, `flow/wait.py`. Findings focus on lifecycle holes that can leave a job pending/claimed/running forever, leak a profile/project slot, or hide state from the operator.

Severity vocabulary (CLAUDE.md): **Critical** = silently corrupts state or blocks throughput indefinitely; **Important** = recoverable but requires manual intervention; **Minor** = cosmetic / rare.

Legend: `[COV-N]` already covered by in-flight codex unit. `[NEW]` not covered.

In-flight codex units (dispatched 2026-05-17 15:48):
- U1 cascade-fail (server/db/job_store.py update_job)
- U2 stale-claim reaper (server/app.py lifespan task)
- U3 parent-alive guard (server/routes/jobs.py submit)
- U4 chain-tree UI orphan visibility (frontend)
- U5 batch-queue UI stuck cleanup (frontend)

---

## 1. Server: job_store.py (claim / update / lifecycle)

### 1.1 `update_job` does not cascade-fail descendants `[COV-1]`
- **Symptom**: L2/L3 children sit `pending` forever after their parent flips `failed`.
- **Trigger**: worker PATCH `status=failed` on an L1/L2 parent. Claim SQL line 677 requires `parent.status='completed'`, so child is no longer claimable but never marked failed.
- **Gap**: `update_job` server/db/job_store.py:405-474 — only releases profile on terminal states; no descendant sweep.
- **Severity**: Critical.

### 1.2 No persistent reaper for `claimed`/`running` rows `[COV-2]`
- **Symptom**: Worker crash/SIGKILL leaves job stuck `claimed` forever; profile row stuck (`profiles.current_job_id` set).
- **Gap**: `recover_stale_jobs` (job_store.py:1168) exists but only invoked via manual POST `/api/jobs/recover` (routes/jobs.py:314). No scheduler in `app.py` lifespan; no startup reset.
- **Severity**: Critical.

### 1.3 Submit-time has no parent-alive guard `[COV-3]`
- **Symptom**: A child created against an already-failed/cancelled parent is accepted and sits pending forever.
- **Gap**: routes/jobs.py:199-214 only inherits target fields when `parent.status == COMPLETED`; does NOT reject non-completed terminal parents.
- **Severity**: Critical.

### 1.4 L2+ accepted with `profile=None` and project fields blank `[NEW]`
- **Symptom**: Production pattern #2 — `profile=None` pending forever (3/6 stuck jobs).
- **Trigger**: parent on a profile this worker doesn't advertise — child never claimed.
- **Gap**: routes/jobs.py:199-214 — `_build_job` at line 217 only copies `parent.profile` on COMPLETED parent. Submit handler should *always* copy `parent.profile` when present.
- **Severity**: Important.

### 1.5 Claim-by-id permits assigning a profile the worker is not allowed to use `[NEW]`
- **Gap**: `claim_specific_pending_job` (job_store.py:1097) takes request's `profile` argument unchallenged — no `get_available_profiles(worker_id)` ACL.
- **Severity**: Important.

### 1.6 `delete_job` deletes only direct leaves; mid-chain pendings become orphans `[NEW]`
- **Symptom**: User cancels L2 from chain-tree → L2 cancelled (good) BUT any pending L3 descendant not cancelled.
- **Gap**: job_store.py:1219-1271 — only direct-descendant check; no recursive cancellation.
- **Severity**: Important.

### 1.7 `recover_stale_jobs` always resets to `pending` — poison-pill loop `[NEW]`
- **Symptom**: Job crashes worker every claim → reaper → pending → claim → crash → forever.
- **Gap**: job_store.py:1168-1216 no retry counter / poison-pill threshold.
- **Severity**: Important.

### 1.8 `claim_next_job` L2+ branch requires non-null `project_url` AND `media_id` `[NEW]`
- **Severity**: Minor (defensive).

### 1.9 No DB FK `ON DELETE CASCADE`, no `idx(status, updated_at)` for reaper `[NEW]`
- **Severity**: Minor.

---

## 2. Server: worker.py (claim / update / heartbeat)

### 2.1 `_workers` heartbeat dict in-memory + unused by reaper `[COV-2 adjacent]`
- **Gap**: worker.py:147-160 — pure log endpoint; never queried. Reaper unit must NOT silently rely on `_workers`.
- **Severity**: Important. **Highlight to reaper codex unit.**

### 2.2 Worker can update someone else's job — no `worker_id` check `[NEW]`
- **Gap**: worker.py:135-144 — `update_job_status` only fetches by id; no `WHERE worker_id = req.worker_id` guard.
- **Severity**: Important.

### 2.3 Claim PATCH does not emit WS event for sibling subtree
- **Severity**: Minor.

---

## 3. Server: jobs.py (validation / create)

### 3.1 `ChainCreate` requires profile but single `POST /api/jobs` does not `[NEW — CRITICAL]`
- **Symptom**: Submit-shape pattern #2 — `profile=None` L2+ pending forever (matches production).
- **Gap**: routes/jobs.py:187-222 — no profile validation at all. ChainCreate's `resolve_chain_profile` enforces (models/job.py:58); single-job path doesn't.
- **Severity**: Critical (matches production root-cause).

### 3.2 `_build_job` keeps `req.parent_job_id` even if parent doesn't exist for chain creates
- **Severity**: Minor.

### 3.3 `cancel_job` broadcasts before DB persists in some race
- **Severity**: Minor.

### 3.4 `requeue_job` does NOT clear `output_files` / `project_url` / `media_id`
- **Severity**: Minor.

---

## 4. WebSocket (ws.py)

### 4.1 No replay / catch-up on reconnect `[COV-4 partial]`
- **Symptom**: Client disconnects during status-flip → reconnects → never sees missed transitions → UI shows stale state.
- **Gap**: ws.py:51-69 — no `last_seen_ts` query param, no replay queue.
- **Severity**: Important. **Chain-tree UI codex unit fixes presentation, not state recovery.**

### 4.2 Broadcast not in DB transaction
- **Severity**: Minor.

---

## 5. App lifespan (`server/app.py`)

### 5.1 Lifespan does not run a startup reaper `[COV-2]`
- server/app.py:77-86 — only calls `init_db`. After server restart, any `claimed`/`running` row stuck until manual `/api/jobs/recover`.
- **Severity**: Critical (covered).

### 5.2 No background reaper task `[COV-2]`
- **Severity**: Critical (covered).

---

## 6. Worker process

### 6.1 No try/finally around run loop to PATCH `status=failed` on SIGTERM `[NEW]`
- **Symptom**: SIGTERM mid-Chrome-page → job left `claimed`/`running` → depends on server reaper.
- **Gap**: worker/main.py:193-243 — `run_claimed_job` lacks `finally` PATCH `status=failed` on cancellation.
- **Severity**: Important.

### 6.2 Worker never sets `status=running` `[NEW]`
- claim_next_job transitions to `claimed`; worker reports `completed`/`failed`/`pending(requeue)`. Schema enum includes RUNNING but unused. Reaper should focus on `claimed`.
- **Severity**: Minor (semantic).

### 6.3 `requeue` path double-discards profile `[NEW]`
- worker/main.py:217-233 — race in burn-recovery edge.
- **Severity**: Important.

### 6.4 `project_lock` in-memory only `[NEW]`
- Per-worker; cross-worker safety relies entirely on DB count. WAL read race possible.
- **Severity**: Minor.

### 6.5 Profile lock leak on `LeafLockoutError` `[NEW]`
- **Severity**: Minor.

### 6.6 `with_retry(handler, max_retries=2)` retries entire handler on ANY exception `[NEW]`
- **Symptom**: Partial-success handler (e.g. extend_video already submitted + got media_id, then disconnected) → fresh retry → double-submit. **Could explain ghost children.**
- **Gap**: dispatcher.py:518, flow/retry.py.
- **Severity**: Important.

---

## 7. Long-running operations (flow/wait.py)

### 7.1 Hard timeout raises `RecaptchaError(kind='timeout')` `[NEW]`
- **Symptom**: Legitimate slow Flow response → classified as recaptcha-burn → profile swap → pool exhaustion → eventual pending forever.
- **Gap**: flow/wait.py:155-161.
- **Severity**: Important.

---

## 8. Frontend `[COV-4, COV-5]`

### 8.1 `chain-builder` allows shapes that produce orphans `[NEW]`
- Submit-side does not refuse `parent_job_id` referencing failed/cancelled row. Belt + braces.
- **Severity**: Important.

### 8.2 `batch-queue` filter for stuck rows `[COV-5]` — in-flight.

### 8.3 `chain-tree` orphan visualisation `[COV-4]` — in-flight. Also surface **completed-parent + claimed-child >30min** as warning.

---

## 9. Cross-cutting NEW findings (priority order)

1. **#3.1** Single-job POST accepts `profile=None` for L2+. **CRITICAL — matches production root cause.**
2. **#2.1 + #2.2** Worker auth: no `worker_id` ownership check on PATCH; in-memory heartbeat ignored by reaper.
3. **#6.6** `with_retry` double-submit on partial-success handlers — possible "ghost children" source.
4. **#6.1** Worker SIGTERM does not PATCH `status=failed`.
5. **#7.1** Timeout misclassified as profile burn → pool exhaustion.
6. **#1.5** `claim_specific_pending_job` skips profile ACL.
7. **#1.6** `delete_job` doesn't cascade-cancel descendant pendings.
8. **#5.1** Lifespan auto-reap on startup hook (overlaps reaper unit).
9. **#4.1** WS reconnect catch-up.
10. **#1.9** Missing `idx_jobs(status, updated_at)` + `ON DELETE` semantics.

---

## 10. Recommended invariants

- `INV-A`: every L2+ job in DB has non-null `profile` (DB CHECK constraint).
- `INV-B`: child of a non-terminal/non-completed parent cannot be created with a different `profile`.
- `INV-C`: terminal-state cascade — failing a parent implies failing all non-terminal descendants in same transaction.
- `INV-D`: reaper enforces poison-pill (≥3 reaps → auto-fail with `error="poison_pill"`).
- `INV-E`: worker PATCH must include matching `worker_id`.
- `INV-F`: server PATCH `status=cancelled` cascades to descendants.

---

## 11. File:line index

| Gap | Location |
|---|---|
| Cascade-fail gap | `server/db/job_store.py:445-470` |
| Submit-time parent guard | `server/routes/jobs.py:199-217` |
| Profile=None acceptance | `server/routes/jobs.py:217`, `server/models/job.py:119` |
| Reaper exists, never scheduled | `server/db/job_store.py:1168`, `server/app.py:77-86` |
| Heartbeat dict unused | `server/routes/worker.py:80,147-160` |
| Worker ownership check missing | `server/routes/worker.py:135-144` |
| claim-by-id ACL skip | `server/db/job_store.py:1097-1152` |
| delete_job no recursion | `server/db/job_store.py:1219-1271` |
| with_retry retries everything | `worker/dispatcher.py:518`, `flow/retry.py` |
| Worker SIGTERM no failed-PATCH | `worker/main.py:193-243` |
| Timeout-as-burn | `flow/wait.py:155-161` |
| WS no replay | `server/routes/ws.py:51-69` |
| DB indices/FK | `server/db/database.py:78-80,132-143` |

---

## 12. Follow-up dispatch

U6 dispatched 2026-05-17 covering #3.1 (single-job POST profile-required for L2+).

Remaining NEW findings #2.2 / #6.6 / #6.1 / #7.1 / #1.5 / #1.6 / #4.1 — defer to next session.
