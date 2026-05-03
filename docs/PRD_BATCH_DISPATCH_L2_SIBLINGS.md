# PRD: FlowEngine batch-dispatch L2 siblings (1 Chrome → N submits)

**Status:** open / not implemented
**Branch target:** `claude/batch-dispatch-l2-siblings`
**Owner:** TBD
**Created:** 2026-05-03

## Problem

Engine hiện tại chạy **1-job-1-Chrome** (open → submit → wait → download → close).
Khi user fan-out 1 L1 → 3 L2 children trên cùng Flow project, các jobs chạy SERIAL.
Tổng wall-time = `3 × per-job` ≈ 9-12 phút.

Flow UI **native** hỗ trợ multi-gen concurrent (verified 2026-05-03: trong 1 Chrome
tab, click composer + submit 3 lần liên tiếp, Flow chạy 3 gen song song).
Engine chưa khai thác được.

## Goal

Implement "batch dispatch" mode:
- 1 worker dispatch xử lý N L2 jobs cùng `parent_job_id` (cùng project_url)
- Mở 1 Chrome, navigate `/edit/{parent_media_id}` ONCE
- Submit N lần liên tiếp (không wait), capture `gen_id` mỗi lần qua network event
- Parallel poll N gen_ids
- Download từng cái khi hoàn thành (per-gen download endpoint)
- Update từng job result lên server
- Close Chrome khi tất cả done

Wall-time mới = `max(gen_time) + N × (submit + download)` ≈ 4-5 phút thay vì 9-12.

## Scope (phase 1)

- L2 siblings only: `extend-video` / `camera-move` / `insert-object` / `remove-object`
  trên cùng L1 (cùng `parent_job_id`).
- Skip L3+ ở phase này.
- Single profile worker (`FLOW_USE_BASE_PROFILE=1`, không clone).
- Native Chrome real-mode CDP — không multi-Chrome.

## CRITICAL — DO NOT BREAK STABLE 1-1-1

Production hiện tại đang chạy 1-job-1-Chrome (legacy `dispatch_job`).
Path này đã verified end-to-end, **đừng đụng vào hành vi của nó**.

### Branch isolation

- BRANCH OFF `master` vào `claude/batch-dispatch-l2-siblings`. Không commit lên master trực tiếp.
- KHÔNG force-push, không rebase master.
- KHÔNG merge PR cho tới khi user approve.
- KHÔNG modify env file `/etc/flowengine/flowengine.env` trên production.

### Feature gate (BẮT BUỘC)

Toàn bộ batch path PHẢI env-gated:

```python
if os.environ.get("FLOW_BATCH_DISPATCH", "0").strip() == "1":
    # new batch path
else:
    # legacy dispatch_job (UNCHANGED)
```

Default OFF. Production chỉ enable khi user explicit set env.

### Op refactor: keep legacy callers green

Refactor `extend_video` / `camera_move` / `insert_object` / `remove_object`
thành 3 phase NHƯNG giữ wrapper:

```python
# flow/operations/extend.py
async def submit_extend(client, job_dict) -> dict:
    """Click submit + capture gen_id only. No wait, no download."""
    ...
    return {"gen_id": ..., "submit_ts": ...}

async def wait_for_gen(client, gen_id, timeout=600) -> dict:
    """Poll Flow until generation completes. Resolve final media_id."""
    ...
    return {"media_id": ..., "status": "completed", "edit_url": ...}

async def download_gen(client, media_id, prefix) -> list[str]:
    """Download upscaled 1080p mp4. Return output_files list."""
    ...
    return ["downloads/ext_1080p_xxx.mp4"]

async def extend_video(client, job):  # LEGACY — DO NOT REMOVE
    sub = await submit_extend(client, job)
    res = await wait_for_gen(client, sub["gen_id"])
    files = await download_gen(client, res["media_id"], "ext")
    return {**res, "output_files": files}
```

→ Legacy 1-1-1 path gọi wrapper, behavior không đổi. Batch path gọi 3 phase riêng.

Code chung trong `flow/operations/_base.py:finalize_operation` cần split tương tự.

### Tests phải verify legacy KHÔNG REGRESS

- Existing pytest 681 hiện tại phải vẫn pass.
- Add `tests/test_extend_legacy_wrapper.py` etc — confirm wrapper output identical
  to current monolithic version (use mocked FlowClient + recorded responses).
- Add batch tests separately.

### Live verify constraints

- Live test trên Debian: chạy với `FLOW_BATCH_DISPATCH=1` ở env LOCAL của test
  runner script (export inline), KHÔNG ghi vào `/etc/flowengine/flowengine.env`.
- Sau test, không bật env trên systemd service. User sẽ self-enable khi ready.

### Rollback safety

Nếu có vấn đề: PR revert đơn lẻ + production tự động fall-back legacy
(vì `FLOW_BATCH_DISPATCH` unset = OFF).

### DO NOT

- KHÔNG xóa hoặc rename `dispatch_job`, `extend_video`, `camera_move`,
  `insert_object`, `remove_object`.
- KHÔNG modify `ProjectLock` default cap (giữ 1).
- KHÔNG modify worker `max_concurrent` default (giữ 1).
- KHÔNG modify `FLOW_USE_BASE_PROFILE` default (giữ 1).
- KHÔNG đụng files đang ổn: `flow/client.py`, `flow/login.py`,
  `worker/profile_swapper.py`, `worker/profile_manager.py`.
- KHÔNG amend / force-push / interactive rebase.

## Files cần đụng

### `flow/operations/_batch.py` (NEW)

```python
async def batch_dispatch_l2_siblings(
    client: FlowClient,
    parent_edit_url: str,
    parent_media_id: str,
    siblings: list[dict],  # mỗi dict = 1 L2 job spec (id, type, prompt, direction, bbox)
) -> list[dict]:  # mỗi dict = result {job_id, status, media_id, output_files, error}
    # 1. await client.page.goto(parent_edit_url) — navigate once
    # 2. for each sibling: dispatch tới op-specific submit_X, capture {sibling_id, gen_id}
    #    - Dispatch theo sibling['type']: submit_extend / submit_camera / submit_insert / submit_remove
    # 3. await asyncio.gather(*[wait_for_gen(client, gid) for gid in gen_ids]) — parallel poll
    # 4. for each completed gen: download_gen(media_id, prefix)
    # 5. return list of results in same order as siblings input
```

Mỗi sibling type có submit_X function khác (extend dùng "Extend" mode chip,
camera dùng "Camera" preset, insert/remove dùng bbox draw). Pattern matching theo
`sibling['type']`.

Edge cases:
- 1 sibling fail submit → skip wait/download for it, mark result.status=failed,
  vẫn tiếp tục cho các sibling khác.
- 1 sibling fail wait (timeout / Flow ALL_FAILED) → result.status=failed, skip download.
- Network event capture: dùng `_calls[calls_before:]` pattern hiện có trong
  `flow/submit.py` để tránh cross-contamination giữa các submit liên tiếp.

### `flow/operations/extend.py`, `camera.py`, `insert.py`, `remove.py`

Refactor 3-phase + giữ legacy wrapper (xem sample ở section "Op refactor" trên).

### `worker/dispatcher.py`

Add:
```python
async def dispatch_batch_same_project(
    jobs: list[dict],
    profile_manager,
    project_lock,
) -> list[dict]:
    """Dispatch N siblings sharing parent_job_id in one Chrome.
    
    - Acquire project_lock (1 slot).
    - Get/launch FlowClient.
    - Group jobs theo parent_edit_url (sanity: tất cả siblings phải cùng URL).
    - Call flow.operations._batch.batch_dispatch_l2_siblings.
    - Return list[result] in input order.
    - Release lock.
    """
```

KHÔNG xóa hoặc modify `dispatch_job`. Coexist.

### `worker/main.py` claim loop

```python
if FLOW_BATCH_DISPATCH and just_claimed_job["job_level"] >= 2:
    siblings = await api.list_pending_siblings(
        parent_job_id=just_claimed_job["parent_job_id"],
        profile=just_claimed_job["profile"],
    )
    siblings = [s for s in siblings if s["id"] != just_claimed_job["id"]]
    if siblings:
        # claim siblings + dispatch batch
        batch = [just_claimed_job] + siblings_to_claim
        results = await dispatch_batch_same_project(batch, profile_mgr, project_lock)
        for r in results: await api.update_job(r["job_id"], r)
        return
# else: legacy single-job dispatch
```

### `server/routes/jobs.py` (NEW endpoint)

```python
@router.get("/jobs/siblings")
async def get_pending_siblings(
    parent_job_id: str = Query(...),
    profile: str | None = Query(None),
):
    """List pending L2+ siblings sharing parent_job_id (+ optional profile filter).
    
    Used by worker batch claim path.
    """
    return await job_store.list_pending_siblings(parent_job_id, profile)
```

`server/db/job_store.py`:
```python
async def list_pending_siblings(parent_job_id, profile=None):
    sql = "SELECT * FROM jobs WHERE parent_job_id=? AND status='pending'"
    params = [parent_job_id]
    if profile:
        sql += " AND profile=?"
        params.append(profile)
    sql += " ORDER BY created_at ASC"
    ...
```

### `worker/remote_api.py` (client method)

```python
async def list_pending_siblings(self, parent_job_id, profile=None):
    # GET /api/jobs/siblings?parent_job_id=...&profile=...
    ...
```

### Atomic batch claim

Naive approach: claim 1, then peek siblings, claim each one separately.
Race risk: another worker claims a sibling between peek + claim.

For phase 1 acceptable: single worker process. If 2+ workers in future, need
`POST /api/jobs/batch-claim` endpoint that atomically claims N siblings.

## Tests

### Unit (mocked Playwright/network)

`tests/test_batch_dispatch.py`:
- batch 3 extend siblings → all 3 submit + 3 distinct gen_ids captured
- batch mixed types (1 extend + 1 camera + 1 insert) → parallel poll + per-type
  download dispatched correctly
- batch with 1 sibling fails generation → other 2 still complete; failed one →
  result.status=failed
- ProjectLock single slot enforced (no inflight=N needed)
- Order preserved: results[i].job_id matches siblings[i].id

`tests/test_extend_legacy_wrapper.py` (and similar for camera/insert/remove):
- Mock FlowClient; call legacy `extend_video(client, job)`; assert output identical
  to pre-refactor recorded behavior (snapshot test).

### Server endpoint

`tests/test_jobs_siblings_api.py`:
- GET /api/jobs/siblings filters parent + status + profile correctly
- Empty result when no siblings
- Order ASC by created_at

### Integration

`tests/test_worker_batch_claim.py`:
- Mock api + profile_mgr + project_lock
- 3 pending L2 with same parent → worker batch dispatches all 3
- 1 pending L2 (no siblings) → worker falls back to legacy dispatch_job

## Live verify

Setup:
- 1 L1 t2v completed (use existing or fresh submit)
- 3 L2 children pending: 1 extend + 1 camera-move "Dolly in" + 1 camera-move "Orbit left"
- All children submitted via `/api/chains` (multi-step web mode) hoặc 3 individual
  POST `/api/jobs` with `parent_job_id` cùng L1.

Steps:
- SSH debian-root, write test runner script that submits + polls
- Run worker với env `FLOW_BATCH_DISPATCH=1` (chỉ test runner shell, không systemd):
  ```
  sudo -u flowengine env FLOW_BATCH_DISPATCH=1 \
    /opt/flowengine/.venv/bin/python -c "from worker import main; ..."
  ```
  HOẶC restart `flowengine-worker.service` với env override (then reset env after test).
- Verify in worker.out:
  - Đúng **1** "Launching Chrome CDP" line cho cả batch
  - 3 distinct gen_ids captured
  - Parallel poll happens
- Verify in DB:
  - 3 jobs status=completed
  - 3 distinct media_ids
  - 3 distinct output_files
  - All chain_id match L1's chain_id
  - parent_job_id correct
- Wall-time check: total < 6 phút (vs ~10 phút serial)

## Acceptance criteria

- batch helper hoàn chỉnh, mỗi op refactor thành 3 phase với legacy wrapper
- Worker auto-detect siblings + batch dispatch khi `FLOW_BATCH_DISPATCH=1`
- Default `FLOW_BATCH_DISPATCH=0` → legacy 1-1-1 unchanged
- pytest 700+ pass (existing 681 + new batch tests ~20)
- Live verify 1 L1 + 3 L2 batch completes <6min với 3 distinct media_ids, 1 Chrome
- PR opened against master, body cite this PRD + result evidence
- Memory updated: `feedback_batch_dispatch_l2_siblings.md` ghi nhận pattern

## Reference architecture đã ship trong epic

- PR #195: `ProjectLock` semaphore (env `FLOW_PROJECT_INFLIGHT`, default 1) —
  useful nếu sau này muốn N concurrent. Batch không cần (single Chrome).
- PR #196: `ALLOW_SAME_PROFILE_CONCURRENCY` worker mode — không dùng cho batch.
- PR #197: per-instance CDP port — defensive hardening, không liên quan batch.
- Memory `feedback_l1_siblings_only.md` (legacy convention pre-batch) cần update
  sau batch ship: convention thay đổi, multi-branch L2 trên cùng project OK qua
  batch dispatch.

## Production access (cho live verify)

- SSH: `debian-root` (192.168.86.42) — host alias đã có trong `~/.ssh/config`
- FlowEngine root: `/opt/flowengine/`
- DB: `/opt/flowengine/data/flowengine.db` (sqlite3, read-only OK)
- Login pw: `1` (tested 2026-05-03)
- Profile: `ngoctuandt20` (warmed, đang live)
- Logs: `/opt/flowengine/logs/{server,worker}.out`
- Restart sau deploy: `systemctl restart flowengine-worker`
  (CHỈ làm khi user explicit approve)

## Ước thời gian

- 1-2 ngày dev + test
- Refactor ops là phần nặng nhất (4 ops × 3 phase = 12 functions cần extract)
- Live verify ~30 phút (gồm TOTP login nếu profile burned)

## Hết

Khi xong: open PR vào master với body link tới PRD này + bullet ngắn evidence từ
live verify.
