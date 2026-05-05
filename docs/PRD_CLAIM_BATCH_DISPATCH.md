# PRD: Claim-batch multi-tab (1 profile, 3 tab //)

**Status:** draft v2, đang chờ anh review
**Branch:** `claude/claim-batch-dispatch` (off `master @1c1cdb8`)
**Created:** 2026-05-05

---

## 1. Mục tiêu

1 profile = 1 Chrome chạy tới **3 tab song song**, thay vì 1 tab/lần như hiện tại. Tận dụng việc CPU đã giảm ~100× sau commit `7434e3e`.

## 2. Scope

| Loại batch | Hành vi | Trạng thái |
|---|---|---|
| **3 job toàn L1 t2v** (chưa có `project_url`) | Inflate-batch 1 tab, N composer cycles trên cùng project | **Đã có** (`dispatch_batch_l1_same_project`) — giữ nguyên |
| **3 job L2+** (đã có `parent_edit_url` + `parent_media_id`, bất kể parent / project_url) | Mở 3 tab //, mỗi tab navigate `/edit/{parent_media_id}` riêng, chạy 1 op | **PHẦN MỚI** |
| **Mix L1 + L2** trong cùng claim | KHÔNG xảy ra — claim algo ưu tiên L2+ trước, hết L2+ mới claim L1 | Đảm bảo bởi server |

L1 chain `→ L2 → L3` (1 chain đa tầng) đã có `batch_dispatch_chains` rồi, không đụng.

## 3. Server — atomic batch claim

### 3.1 API

```
POST /api/worker/claim
{ "worker_id": ..., "profiles": [...], "batch_size": 3 }
```

`batch_size` optional, default 1, clamp `[1, FLOW_CLAIM_BATCH_MAX=3]`.

Response:
- `batch_size == 1` → bare `Job` hoặc `204` (back-compat, schema không đổi)
- `batch_size > 1` → `{ "jobs": [Job, ...] }` (length 1..N) hoặc `204`

### 3.2 Algorithm — `claim_next_batch`

1 transaction `BEGIN IMMEDIATE`:

1. Chạy SELECT L2+ ready-parent hiện tại (`server/db/job_store.py:644-668`), `LIMIT N`. UPDATE từng row → `claimed`, bind `parent_edit_url` + `parent_media_id` từ parent (logic đã có).
2. Nếu chưa đủ N: chạy SELECT L1 fresh, `LIMIT N - đã_claim`.
3. **Profile-coherent**: job đầu tiên claim được khoá `profile`. Row tiếp theo profile khác → skip (để pending), không fail.
4. **Project-inflight cap**: bộ đếm in-transaction để 3 L2 cùng `project_url` không vượt `FLOW_PROJECT_INFLIGHT`.
5. Commit, return list theo thứ tự selection.

L1 và L2+ không bao giờ mix trong 1 batch (step 1 ưu tiên L2+; nếu step 1 trả về ≥1 row, step 2 skip).

## 4. Worker — dispatch routing

### 4.1 Config

```
FLOW_CLAIM_BATCH=1            # bật path mới (default 0)
FLOW_CLAIM_BATCH_MAX=3        # max job/claim
```

Khi `FLOW_CLAIM_BATCH=1`:
- Worker request `batch_size=FLOW_CLAIM_BATCH_MAX` mỗi cycle.
- Bỏ peek-claim cũ (`_maybe_claim_*_siblings`) — server đã trả batch.
- `MAX_CONCURRENT_JOBS` = số **batch** in-flight, không phải số job.

### 4.2 Routing trong `dispatch_batch(jobs)`

```
N = len(jobs)
N == 1                       → dispatch_job (legacy, giữ burn-recovery requeue)
all L1 t2v, project_url=""   → dispatch_batch_l1_same_project (đã có)
all L2+                      → dispatch_batch_multitab (MỚI)
```

### 4.3 `dispatch_batch_multitab` — primitive mới

Wrap mỏng quanh `flow.operations._multitab.batch_dispatch_ops_multitab` (đã có & verified):

- Verify all jobs cùng `profile` (else fail-all `"batch profile mismatch"`).
- Verify mỗi job có `parent_edit_url` + `parent_media_id` (server claim đã bind sẵn). Job thiếu → fail riêng job đó, không poison batch.
- Acquire `ProjectLock` cho từng `project_url` distinct (sort theo url để tránh deadlock). 2 job cùng `project_url` chia chung 1 slot, 2 tab // — proven OK ở Case C.
- 1 `_client_lease` cho cả batch.
- `RecaptchaError` → burn-and-replace như các batch helper hiện có (`_handle_burned_profile_for_batch`).

## 5. Edge cases

| Tình huống | Hành vi |
|---|---|
| Server trả 1 job dù request 3 | Routing N=1 → `dispatch_job` legacy |
| Server trả 3 L2+ khác parent / khác project_url | `dispatch_batch_multitab`, 3 tab // |
| Server trả 2 L2 cùng project + 1 L2 project khác | `dispatch_batch_multitab`, 3 tab //, 2 ProjectLock distinct |
| reCAPTCHA giữa batch | Cả batch fail, profile burn → swap/wipe (như cũ) |
| 1 tab fail non-recaptcha | Tab khác vẫn chạy, chỉ job đó fail (`batch_dispatch_ops_multitab` đã handle) |
| `FLOW_PROJECT_INFLIGHT=1` + 3 L2 cùng project | Claim trả 1, 2 còn lại để cycle sau |
| `FLOW_CLAIM_BATCH=0` (default) | Path cũ chạy y nguyên — zero regression |

## 6. Test plan

### 6.1 Unit (`pytest tests/`)

- `tests/server/test_claim_batch.py` — atomic claim, profile-coherence, in-transaction project-inflight, back-compat khi `batch_size=1`
- `tests/worker/test_dispatch_batch_routing.py` — N=1 → `dispatch_job`, all-L1 → inflate, all-L2+ → multitab, profile mismatch fail-all
- `tests/worker/test_dispatch_batch_multitab.py` — distinct project_url locks acquired/released, parent_edit_url validation, recaptcha propagation

### 6.2 Live verify (gate "done")

Profile: `ngoctuandt20`. Server: `http://127.0.0.1:8899` (Debian, `/opt/flowengine`).

| Case | Setup | Pass criteria |
|---|---|---|
| L1 batch | 3 L1 t2v, không project_url | 3 media_ids distinct cùng project, 1 tab inflate |
| L2 multitab khác parent | 1 L2 ext trên parent A + 1 L2 cam trên parent B + 1 L3 insert trên parent C | 3 media_ids distinct, **3 tab //** trong 1 Chrome |
| L2 multitab cùng project | 3 L2 ext cùng L1 parent | 3 media_ids distinct, regression check vs Case C cũ |
| Singleton | 1 job, request batch_size=3 | Server trả 1, dispatch qua `dispatch_job` |
| Default off | `FLOW_CLAIM_BATCH=0` | Behaviour identical commit `1c1cdb8` |

CPU: `ps -C chrome -o %cpu=` averaged 60s trong Case "L2 multitab khác parent" < 200% steady-state.

Mỗi run kèm:
- Screenshot Flow project + tab list (→ `/opt/flowengine/error-captures/manual/<ts>_<case>.png`)
- Worker log excerpt show `dispatch_batch_multitab` entry
- Credit tally per job

## 7. Rollout

1. PR này: ship cả server + worker behind `FLOW_CLAIM_BATCH=0` default.
2. Live-verify 5 case trên Debian với `FLOW_CLAIM_BATCH=1`.
3. PR sau: flip default `=1`, deprecate `FLOW_BATCH_DISPATCH` cũ + xoá `_maybe_claim_*_siblings`.

## 8. Acceptance checklist

- [ ] Server trả ≤ N job per claim, atomic, profile-coherent
- [ ] Worker dùng 1 FlowClient cho cả batch
- [ ] Live: 3 L1 cùng project (regression)
- [ ] Live: 3 L2+ khác parent → 3 tab //, 3 media_ids distinct (path mới)
- [ ] Live: 3 L2 cùng parent → unchanged (regression)
- [ ] `batch_size=1` default still works
- [ ] `pytest tests/` xanh
- [ ] CPU < 200% steady-state khi 3 tab
- [ ] Session report kèm credit tally + screenshots

## 9. Deliverables

- PR `claude/claim-batch-dispatch` → `master` (base=master EXPLICIT)
- `docs/PRD_CLAIM_BATCH_DISPATCH.md` (file này)
- `docs/session-reports/2026-05-XX_claim-batch-dispatch.md` với evidence
