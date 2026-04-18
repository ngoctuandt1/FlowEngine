# FlowEngine — Master Specification

> **Đây là document master. Mọi công việc trên codebase phải đọc file này trước.**
> Created: 2026-04-17
> Status: AUTHORITATIVE — nếu file khác mâu thuẫn với file này, file này thắng.

---

## MỤC LỤC

- [PHẦN A — RULES (quy tắc bắt buộc)](#phần-a--rules-quy-tắc-bắt-buộc)
  - [A.1 — Core Invariants (5 bất biến)](#a1--core-invariants-5-bất-biến)
  - [A.2 — Code Rules](#a2--code-rules)
  - [A.3 — Git & Commit Rules](#a3--git--commit-rules)
  - [A.4 — Test Rules](#a4--test-rules)
  - [A.5 — Change Control Rules](#a5--change-control-rules)
- [PHẦN B — PIPELINE (luồng xử lý đầy đủ)](#phần-b--pipeline-luồng-xử-lý-đầy-đủ)
  - [B.1 — Toàn cảnh](#b1--toàn-cảnh-level-0)
  - [B.2 — Job Creation Pipeline](#b2--job-creation-pipeline-ui--db)
  - [B.3 — Claim Pipeline](#b3--claim-pipeline-worker--server)
  - [B.4 — Operation Pipeline — text-to-video (L1)](#b4--operation-pipeline--text-to-video-l1)
  - [B.5 — Operation Pipeline — extend-video (L2)](#b5--operation-pipeline--extend-video-l2)
  - [B.6 — Operation Pipeline — insert-object (L2)](#b6--operation-pipeline--insert-object-l2)
  - [B.7 — Operation Pipeline — remove-object (L2)](#b7--operation-pipeline--remove-object-l2)
  - [B.8 — Operation Pipeline — camera-move (L2)](#b8--operation-pipeline--camera-move-l2)
  - [B.9 — Completion & Broadcast Pipeline](#b9--completion--broadcast-pipeline)
  - [B.10 — Chain Pipeline (multi-level)](#b10--chain-pipeline-multi-level)
  - [B.11 — Error / Recovery Pipeline](#b11--error--recovery-pipeline)
- [PHẦN C — DATA CONTRACTS](#phần-c--data-contracts)
  - [C.1 — Job Schema](#c1--job-schema-đầy-đủ)
  - [C.2 — Profile Schema](#c2--profile-schema)
  - [C.3 — API Contracts](#c3--api-contracts-đầy-đủ)
  - [C.4 — WebSocket Events](#c4--websocket-events)
- [PHẦN D — NOTES & GOTCHAS](#phần-d--notes--gotchas)
  - [D.1 — Google Flow UI gotchas](#d1--google-flow-ui-gotchas)
  - [D.2 — Playwright / Chrome gotchas](#d2--playwright--chrome-gotchas)
  - [D.3 — Server / DB gotchas](#d3--server--db-gotchas)
  - [D.4 — Known bugs trong code hiện tại (B1-B17)](#d4--known-bugs-trong-code-hiện-tại-b1-b17)
- [PHẦN E — DEBUG PLAYBOOK](#phần-e--debug-playbook)
- [PHẦN F — GLOSSARY](#phần-f--glossary)
- [PHẦN G — EXTERNAL REFERENCES](#g--external-references)
  - [G.1 — flowkit (crisng95/flowkit)](#g1--flowkit-crisng95flowkit)

---

# PHẦN A — RULES (quy tắc bắt buộc)

## A.1 — Core Invariants (5 bất biến)

Đây là 5 bất biến tuyệt đối. Nếu code VI PHẠM bất kỳ bất biến nào, code SAI, không cần bàn cãi.

### INV-1: Account Binding
> **1 video project thuộc về 1 Google account duy nhất.**
> Mọi job trong 1 chain PHẢI chạy trên cùng Chrome profile (= cùng Google account).

**Hệ quả:**
- L2+ job KHÔNG BAO GIỜ được claim bởi worker không có profile của parent
- Nếu không có worker nào có profile phù hợp → job PHẢI wait, không được assign bừa
- Profile đổi = account đổi = project 404

**Kiểm tra:** `server/db/job_store.py:claim_next_job` phải có điều kiện `parent.profile IN worker.profiles`.

### INV-2: Navigate by `edit_url` only
> **Để target 1 video cụ thể, CHỈ được navigate đến `/edit/{media_id}` URL trực tiếp.**
> Cấm tuyệt đối: đếm DOM card, dùng `video_index`, click theo vị trí trong grid.

**Lý do:** Grid card order thay đổi khi có video mới. video_index=0 hôm nay ≠ hôm mai.

**Verify:** live test 2026-04-16 đã confirm 4 operations liên tiếp navigate-away-and-back, luôn đúng video. Xem `docs/FLOW_MULTILEVEL_JOBS.md` §10.

**Kiểm tra:** mọi L2 operation (`flow/operations/extend.py`, `insert.py`, `remove.py`, `camera.py`) phải gọi `navigate_to_edit()` trong `_base.py` — KHÔNG được gọi grid-scan.

### INV-3: Store Everything After Every Operation
> **Sau mỗi operation hoàn thành, PHẢI ghi trở lại DB các field:**
> `project_url`, `media_id`, `profile`, `generation_id`, `output_files`, `status=completed`, `completed_at`.

**Lý do:** Chain tiếp theo cần đọc các field này từ parent. Thiếu 1 field = chain break.

**Kiểm tra:** `flow/operations/_base.py:finalize_operation` phải return dict có đủ 6 field trên. Worker dispatcher attach vào PUT `/api/worker/jobs/{id}`.

### INV-4: Serial per Project
> **2 job trên cùng `project_url` KHÔNG BAO GIỜ chạy song song.**

**Lý do:** Flow UI không support concurrent edit trên cùng project → conflict state.

**Implementation:** `worker/project_lock.py` + điều kiện `NO active claim on project_url` trong `claim_next_job`.

### INV-5: `media_id` is Stable Across Operations
> **extend / insert / remove / camera đều CẬP NHẬT video IN-PLACE — `media_id` KHÔNG đổi.**

**Hệ quả:**
- Chain 4 bước trên 1 video → 4 job cùng chia sẻ 1 `media_id`
- URL sau operation = URL trước operation
- Mỗi operation chỉ thêm 1 entry vào history panel

**Verify:** docs/FLOW_MULTILEVEL_JOBS.md §10 — 4 ops liên tiếp cùng 1 media_id `1eb6fea7-f1d4-4fcc-a25f-7ca3e06470be`.

---

## A.2 — Code Rules

### R-CODE-1: Separation of Concerns
| Layer | Được làm gì | Cấm làm gì |
|---|---|---|
| `server/` | API, DB CRUD, WS broadcast | **Không touch Playwright/Chrome** |
| `worker/` | Poll, claim, dispatch, profile/lock management | Không chứa Flow UI logic chi tiết |
| `flow/` | Playwright automation, DOM selectors | Không gọi server API trực tiếp (dùng worker/remote_api) |
| `frontend/` | UI rendering, API calls, WS listen | Không embed business logic |

### R-CODE-2: Single Entry Point per Operation
Mỗi operation có 1 và chỉ 1 handler:
- `run_generate()` trong `flow/operations/generate.py`
- `run_extend()` trong `flow/operations/extend.py`
- `run_insert()` trong `flow/operations/insert.py`
- `run_remove()` trong `flow/operations/remove.py`
- `run_camera()` trong `flow/operations/camera.py`

Dispatcher (`worker/dispatcher.py`) route `job.type` → handler đúng. **Không tạo thêm handler variant.**

### R-CODE-3: Locale-Independent Selectors
Khi tương tác Google Flow UI, **ưu tiên icon class names** (same EN + VI):
```python
# GOOD — locale independent
page.locator('[class*="keyboard_double_arrow_right"]')  # Extend
page.locator('[class*="add_box"]')                       # Insert
page.locator('[class*="ink_eraser"]')                    # Remove
page.locator('[class*="videocam"]')                      # Camera
page.locator('[class*="arrow_forward"]')                 # Submit

# ACCEPTABLE — text với cả 2 locale
for text in ("Extend", "Mở rộng"):
    page.locator("button").filter(has_text=text)

# AVOID — chỉ 1 locale
page.locator("button").filter(has_text="Extend")  # fail trên VI profile
```

### R-CODE-4: Model Panel Dismiss — Click Outside, NOT Escape
Trong `flow/model_selector.py`, sau khi chọn model:
- ✅ Click outside panel (trên chip hiện model) để đóng
- ❌ **CẤM dùng Escape** — Escape đóng cả edit dialog → mất state

### R-CODE-5: Submit Confirmation = 4 Signals, Any One Wins
`flow/submit.py` phải confirm submit bằng **1 trong 4 signal**:
1. `client._gen_id` changed (network intercept)
2. New `operations/` API call in `client._calls`
3. Card count increased
4. Progress indicator visible (% or "Generating" text)

KHÔNG được block wait cho network response complete — chỉ detect initiation.

### R-CODE-6: Completion Detection = 3 Parallel Methods
`flow/wait.py` check parallel:
1. Reverse API (`operations/` response với `done: true`)
2. Network video URLs (MP4/WebM in `client._video_urls`)
3. DOM observer (injected JS tracks progress %)

Timeout per type (chỉnh qua env):
- `text-to-video`: 900s
- `extend-video`: 600s
- `insert/remove/camera`: 300s
- No-signal timeout: 180-300s

### R-CODE-7: Download Fallback Chain 4 Tiers
`flow/download.py` fallback theo thứ tự:
1. API: `getMediaUrlRedirect?name={id}_upsampled` → 1080p
2. API: `?name={id}` → 720p
3. UI: right-click card → Download → 1080p menu
4. Blob: extract blob URL, fetch from browser

File size min: 100 KB (reject smaller).

### R-CODE-8: Always Use LP Model for Free Mode
Khi `free_mode=True`:
- Model DEFAULT = `veo-3.1-fast-lp`
- Verify "0 credits" / "0 tín dụng" trong footer TRƯỚC khi submit
- Nếu footer hiện số credit > 0 → ABORT submit, log lỗi, không submit

### R-CODE-9: Pydantic Models for All API I/O
`server/models/job.py` + `server/models/profile.py` là single source of schema. Routes dùng Pydantic validate input. KHÔNG parse dict raw.

### R-CODE-10: No datetime.utcnow() (Python 3.12+ deprecated)
```python
# ❌ CŨ
from datetime import datetime
now = datetime.utcnow()

# ✅ MỚI
from datetime import datetime, UTC
now = datetime.now(UTC)
```

---

## A.3 — Git & Commit Rules

### R-GIT-1: Branch Naming
```
claude/bug-N-slug        # Bug fix cho issue #N
claude/feature-slug      # Feature mới
claude/refactor-slug     # Refactor
claude/<auto-name>       # Exploratory worktree
```

### R-GIT-2: Commit Message
```
fix(scope): short description
feat(scope): short description
refactor(scope): short description
test(scope): short description
docs: short description

# Body (optional) — giải thích WHY, không phải WHAT
# Closes #N (nếu PR đóng issue)
```

### R-GIT-3: Co-author
Mọi commit Claude tạo phải có:
```
Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
```

### R-GIT-4: Cấm
- `git push --force` lên `master` / `main`
- `--no-verify` / skip hooks
- `git commit --amend` (luôn tạo commit mới)
- Commit file secrets (`.env`, credentials)

---

## A.4 — Test Rules

### R-TEST-1: Mọi PR đóng bug / thêm feature PHẢI có test
- Bug fix → 1 test reproduce bug trước khi fix (TDD)
- Feature → ≥ 1 happy path + ≥ 1 error path

### R-TEST-2: Test location = `tests/`
```
tests/
  test_job_store.py
  test_chain_logic.py
  test_profile_pinning.py
  test_api.py
  test_navigation.py
  test_e2e.py         # (optional, requires Chrome)
```

### R-TEST-3: Không test browser trong unit test
`flow/` module chỉ test qua integration test với Playwright mock hoặc manual E2E.

### R-TEST-4: Coverage Target
- `server/` + `worker/` ≥ 70%
- `flow/` — manual E2E acceptable (browser-dependent)

### R-TEST-5: Test commands (post-B9)

Dev deps: `pip install -r requirements-dev.txt` (pytest, pytest-asyncio, httpx).
Config: `pytest.ini` (asyncio_mode=auto, function-scoped loops, testpaths=tests).

Run full suite:
```bash
pytest tests/ -v
```

Run with coverage (requires `pip install pytest-cov`):
```bash
pytest tests/ --cov=server --cov=worker --cov-report=term-missing
```

Run a single file / test:
```bash
pytest tests/test_smoke.py -v
pytest tests/test_config.py::test_server_port_default_is_8080 -v
```

Fail on DeprecationWarning (pre-release sanity — used by B8):
```bash
pytest tests/ -W error::DeprecationWarning
```

Fixtures exposed by `tests/conftest.py`:
- `temp_db_path` — fresh SQLite file under a tempdir; patches both env var and
  already-imported `DATABASE_PATH` bindings. Prevents tests hitting the dev DB.
- `db` — runs `init_db()` on the temp DB so schema exists before the test body.
- `api_client` — httpx `AsyncClient` bound to the FastAPI app via `ASGITransport`
  (no real socket). Depends on `db` so routes have schema when they run.
- `sample_job_payload`, `sample_profile` — ready-to-use factories.

---

## A.5 — Change Control Rules

### R-CC-1: KHÔNG restructure kiến trúc
Kiến trúc 4-tầng đã verify hoạt động. Cấm:
- Đổi layer boundaries
- Thêm layer mới
- Merge `server/` + `worker/` thành 1 process
- Đổi SQLite sang DB khác

### R-CC-2: KHÔNG thêm job type mới cho đến khi 9 gaps (B1-B9) close
Xem §D.4 — focus vào correctness trước.

### R-CC-3: KHÔNG optimize performance khi chưa có baseline
Đo trước, optimize sau. Hiện tại chưa có benchmark nào.

### R-CC-4: Mọi thay đổi chạm 1 trong 5 invariants (§A.1) phải có design review
- Đăng issue GitHub mô tả change + impact
- Update `docs/SPEC.md` với decision log
- Mới được code

### R-CC-5: Docs update cùng commit
Nếu commit thay đổi API / schema / pipeline → cùng commit update:
- `docs/SPEC.md` (nếu affect rules/pipeline)
- `CLAUDE.md` (nếu affect Claude context)
- Relevant docstring

---

# PHẦN B — PIPELINE (luồng xử lý đầy đủ)

## B.1 — Toàn cảnh (Level 0)

```
[User] → [Frontend] → [Server API] → [SQLite jobs table]
                         ↑
                   [WS broadcast]
                         ↑
[Worker poll loop] ← [Server /api/worker/claim]
        ↓
[Dispatcher]
        ↓
[FlowClient (Playwright + Chrome profile)]
        ↓
[Google Flow UI]
        ↓
[Video file in downloads/]
        ↓
[PUT /api/worker/jobs/{id} với project_url, media_id, output_files]
        ↓
[WS broadcast job_completed] → [Frontend cập nhật UI]
```

---

## B.2 — Job Creation Pipeline (UI → DB)

### B.2.1 — Single Job

```
1. User mở Create Job page (frontend/js/pages/create-job.js)
2. User chọn type (text-to-video / extend-video / insert-object / remove-object / camera-move)
3. Form render field tương ứng theo type (xem §C.1 để biết field required)
4. User submit form → POST /api/jobs
   Body: {type, prompt?, model?, aspect_ratio?, bbox?, direction?, parent_job_id?, project_url?, media_id?}
5. Server route jobs.py:38 (create_job):
   a. Validate Pydantic (JobCreate)
   b. Nếu có parent_job_id:
      - Fetch parent từ DB
      - Nếu parent.status == completed → inherit project_url, media_id, profile
      - job_level = parent.job_level + 1
   c. Sinh UUID cho job.id
   d. status = "pending"
   e. INSERT INTO jobs
   f. WS broadcast "job_created"
6. Response: Job object (200)
7. Frontend:
   - Toast "Job created: {id}"
   - Dashboard nhận WS event → append card
```

### B.2.2 — Chain (Multi-Job)

```
1. User mở Chain Builder page
2. Bước 1 PHẢI là text-to-video (enforce ở frontend)
3. Các bước sau: extend / insert / remove / camera
4. Mỗi bước config prompt / bbox / direction riêng
5. User submit → POST /api/chains
   Body: {jobs: [JobCreate1, JobCreate2, ...], profile?: "optional pin"}
6. Server route jobs.py:67 (create_chain):
   a. Sinh chain_id (UUID)
   b. Loop qua jobs:
      - jobs[0]: job_level=1, no parent_job_id
      - jobs[i] (i>0): job_level=i+1, parent_job_id=jobs[i-1].id
      - Tất cả cùng chain_id
      - Tất cả status="pending"
      - Nếu body.profile set → assign cho job[0] (L2+ inherit khi claim)
   c. INSERT từng job
   d. WS broadcast "job_created" cho mỗi
7. Response: {chain_id, jobs: [Job1, Job2, ...]}
```

### B.2.3 — Validation Rules per Type

| Type | Required fields | Inherited from parent (nếu có) |
|---|---|---|
| text-to-video | `prompt`, `type` | — |
| extend-video | `type`, (prompt optional) | `project_url`, `media_id`, `profile` |
| insert-object | `type`, `prompt`, (bbox optional) | `project_url`, `media_id`, `profile` |
| remove-object | `type`, `bbox` (REQUIRED) | `project_url`, `media_id`, `profile` |
| camera-move | `type`, `direction` | `project_url`, `media_id`, `profile` |

---

## B.3 — Claim Pipeline (Worker → Server)

### B.3.1 — Worker Poll Loop

```
worker/main.py mỗi POLL_INTERVAL_SEC (5s):
1. Check available profiles (not busy, not quarantined) — profile_manager
2. POST /api/worker/claim với {worker_id, profiles: [list]}
3. Server response:
   a. 204 No Content → sleep, retry
   b. 200 + Job object → dispatch
4. Nếu 200: lock profile (profile_manager.mark_busy)
5. await dispatcher.dispatch_job(job)
6. Release profile (mark_available)
7. PUT /api/worker/jobs/{id} với result
```

### B.3.2 — Server Claim Algorithm (atomic)

`server/db/job_store.py:claim_next_job` — WRAP trong `BEGIN IMMEDIATE`:

```
INPUT: worker_id, available_profiles: List[str]

PRIORITY 1 — L2+ jobs (profile-pinned):
  SELECT * FROM jobs
  WHERE status = 'pending'
    AND job_level >= 2
    AND parent_job_id IS NOT NULL
  ORDER BY created_at ASC
  
  For each candidate:
    parent = fetch_job(candidate.parent_job_id)
    IF parent.status != 'completed': SKIP
    IF parent.profile NOT IN available_profiles: SKIP
    IF EXISTS (job WHERE status IN ('claimed','running') AND project_url = parent.project_url): SKIP
    → CLAIM này:
       UPDATE jobs SET 
         status='claimed', 
         worker_id=worker_id, 
         claimed_at=now(),
         profile=parent.profile,
         project_url=parent.project_url,
         media_id=parent.media_id
       WHERE id=candidate.id
       RETURN Job

PRIORITY 2 — L1 jobs (any available profile):
  SELECT * FROM jobs
  WHERE status = 'pending'
    AND job_level = 1
    AND (profile IS NULL OR profile IN available_profiles)
  ORDER BY created_at ASC
  LIMIT 1
  
  If found:
    assign_profile = job.profile OR available_profiles[0]
    UPDATE jobs SET
      status='claimed',
      worker_id=worker_id,
      claimed_at=now(),
      profile=assign_profile
    RETURN Job

ELSE: RETURN None (204 No Content)

COMMIT
```

**Key invariant:** `BEGIN IMMEDIATE` ngăn 2 worker claim cùng 1 job. SQLite lock level = RESERVED.

---

## B.4 — Operation Pipeline — text-to-video (L1)

```
INPUT: job = {id, type="text-to-video", prompt, model="veo-3.1-fast-lp", aspect_ratio="16:9", profile}
OUTPUT: {project_url, media_id, edit_url, output_files, generation_id, profile}
```

### Steps

```
1. Dispatcher (worker/dispatcher.py):
   a. Nếu profile chưa có credentials → trigger AIgglog login
   b. Launch FlowClient(profile_name=job.profile)
   c. Call run_generate(client, prompt, model, aspect_ratio)

2. flow/operations/generate.py:run_generate:
   a. client.page.goto("https://labs.google/fx/tools/flow")
   b. Wait for homepage loaded
   c. check_account() — verify ULTRA tier (hoặc skip nếu LP)
   d. Click "+ New project" button
   e. Wait for empty project canvas
   f. extract_project_url() từ URL hiện tại → lưu project_url
   g. click composer textarea
   h. type prompt
   i. select_model(model) — open model dropdown, click LP model, verify "0 credits"
   j. _set_aspect_ratio(aspect_ratio) — ⚠️ HIỆN TẠI STUB (B1)
   k. submit_with_confirmation():
      - Click submit button ([class*="arrow_forward"])
      - Wait 4 confirmation signals (§A.2 R-CODE-5)
   l. wait_for_completion(timeout=900):
      - Parallel 3 detection methods (§A.2 R-CODE-6)
   m. extract_media_id from URL /edit/{uuid} → lưu media_id
   n. download_video(media_id):
      - API 1080p → 720p → UI right-click → blob (§A.2 R-CODE-7)
      - Save to downloads/{profile}_{timestamp}.mp4
   o. return {
        project_url, 
        media_id, 
        edit_url=f"{project_url}/edit/{media_id}",
        output_files=[path], 
        generation_id, 
        profile
      }

3. Dispatcher attach status="completed", completed_at=now()
4. remote_api.update_job(job_id, result) → PUT /api/worker/jobs/{id}
5. FlowClient teardown (close browser)
6. Release profile
```

### Failure modes

| Triệu chứng | Lý do có thể | File debug |
|---|---|---|
| "Credits required" popup | Không phải LP model hoặc LP slot hết | `model_selector.py`, check footer text |
| reCAPTCHA block | Google flag account | `recaptcha.py` — wait 120s manual |
| No project canvas | Homepage render chậm | `generate.py` — tăng `wait_for_selector` timeout |
| Submit không confirm | 4 signals đều miss | `submit.py` — check selector cập nhật |
| Wait timeout 900s | Gen bị stuck trên Flow server | `wait.py` — check DOM observer log |

---

## B.5 — Operation Pipeline — extend-video (L2)

```
INPUT: job = {id, type="extend-video", project_url, media_id, profile, prompt?, model}
OUTPUT: {project_url, media_id (SAME), edit_url, output_files, generation_id, profile}
```

### Steps

```
1. Dispatcher → run_extend(client, job, prompt, model)

2. flow/operations/_base.py:navigate_to_edit(client, job):
   a. Build edit_url = f"{project_url}/edit/{media_id}"
   b. page.goto(edit_url)
   c. Wait for <video> element visible (wait_for_video_loaded)
   d. VERIFY: media_id trong page.url — nếu không khớp → FAIL
   e. Nếu redirect login → trigger AIgglog, retry 1 lần
   f. Nếu URL 404 → fallback click tile theo media_id match (last resort)

3. flow/operations/extend.py:run_extend:
   a. click_action_button("Extend" / "Mở rộng" / icon [class*="keyboard_double_arrow_right"])
   b. Wait for composer placeholder change to "What happens next?" / "Tiếp theo là gì?"
   c. Nếu prompt truthy: click composer → type prompt
   d. select_model(LP) — verify "0 credits"
   e. submit_with_confirmation()
   f. wait_for_completion(timeout=600)
   g. Extract new media_id from URL — verify SAME as input (INV-5)
   h. download_video(media_id)
   i. return finalize_operation(result)

4. Dispatcher → update_job(status=completed, project_url, media_id, output_files, ...)
```

### Failure modes

| Triệu chứng | Lý do | Fix |
|---|---|---|
| URL redirect về homepage | Profile chưa login account của project | Check profile = parent.profile (INV-1) |
| media_id changed sau extend | Flow đã tạo version mới (bất thường) | ⚠️ LOG rõ, update job với media_id mới |
| `navigate_to_edit` 404 | project_url sai hoặc project bị xoá | FAIL job, log error |
| Extend panel không mở | Click sai button | Check selector icon class |

---

## B.6 — Operation Pipeline — insert-object (L2)

```
INPUT: job = {type="insert-object", project_url, media_id, profile, prompt (REQUIRED), bbox?: {x,y,w,h}}
OUTPUT: same shape as extend
```

### Steps

```
1. navigate_to_edit(job)  — như B.5
2. click_action_button("Insert" / "Chèn" / [class*="add_box"])
3. Wait composer placeholder: "Describe what you'd like to add..."
4. Nếu bbox: gọi `flow.operations._base.draw_bbox_on_video(page, bbox)` (shared với remove). Helper validate range [0,1], clamp overflow, drag, verify overlay rect visible. Return False → log WARNING, continue (Flow fallback default region).
5. Nếu bbox null → Flow dùng vùng default (thường toàn video)
6. Click composer → type prompt
7. submit_with_confirmation()
8. wait_for_completion(timeout=300)
9. Extract media_id — SAME as input (INV-5)
10. download_video(media_id)
11. return finalize_operation(result)
```

### Bbox Coordinate System
- Normalized 0-1:
  - `x` = left edge of bbox / video_width
  - `y` = top edge / video_height
  - `w` = width / video_width
  - `h` = height / video_height
- Convert sang pixel theo `page.locator("video").bounding_box()`:
  ```
  rect = video.bounding_box()
  px_start = (rect.x + bbox.x * rect.width, rect.y + bbox.y * rect.height)
  px_end = (px_start.x + bbox.w * rect.width, px_start.y + bbox.h * rect.height)
  ```

---

## B.7 — Operation Pipeline — remove-object (L2)

```
INPUT: job = {type="remove-object", project_url, media_id, profile, bbox (REQUIRED)}
OUTPUT: same shape
```

### Steps

```
1. navigate_to_edit(job)
2. click_action_button("Remove" / "Xoá" / [class*="ink_eraser"])
3. Wait composer placeholder: "Click-and-drag to fully select..."
4. bbox PHẢI có (nếu null → default 0.25-0.75 center)
5. Draw bbox (same logic as B.6)
6. KHÔNG type prompt (chế độ remove không cần)
7. submit_with_confirmation()
8. wait_for_completion(timeout=300)
9. Extract media_id — SAME
10. download_video
11. return finalize_operation
```

### Lưu ý
- Remove quality phụ thuộc AI model, có thể "partial removal" (xoá không sạch)
- Không có prompt → nếu muốn hint Flow bỏ gì, dùng insert với prompt "empty background" thay vì remove

---

## B.8 — Operation Pipeline — camera-move (L2)

```
INPUT: job = {type="camera-move", project_url, media_id, profile, direction: str}
OUTPUT: same shape
```

### Available Directions
**Camera motion tab:**
- "Dolly in", "Dolly out"
- "Orbit left", "Orbit right", "Orbit up", "Orbit low"
- "Dolly in zoom out", "Dolly out zoom in"

**Camera position tab:**
- "Center", "Left", "Right", "High", "Low", "Closer", "Further"

### Steps

```
1. navigate_to_edit(job)
2. click_action_button("Camera" / [class*="videocam"])
3. Wait for preset grid visible (composer REPLACED by preset picker)
4. Determine tab from direction:
   - Motion direction → click tab "Camera motion"
   - Position direction → click tab "Camera position"
5. `_click_preset(page, direction)` — 3 exact-match strategies (aria-label → role=button+anchored-regex → get_by_text exact=True). Each strategy clicks then `_verify_preset_selected` checks active-state signal (aria-pressed / aria-selected / class keyword / parent keyword). Fall through to next strategy if clicked-but-unverified; return False + log ERROR after all miss (caller raises RuntimeError — NOT silent-submit with default). See `docs/FLOW_UI_REFERENCE.md` §Camera Preset Selection & Active State.
6. Submit (KHÁC các op khác — camera dùng):
   - generic "See how many credits this generation will use" + generic "Create"
   - KHÔNG phải arrow_forward
7. wait_for_completion(timeout=300)
8. Extract media_id — SAME
9. download_video
10. return finalize_operation
```

### Lưu ý riêng của Camera
- KHÔNG có model selector (dùng model mặc định của Flow)
- KHÔNG có prompt (dùng preset only)
- Submit button DOM khác 4 op còn lại

---

## B.9 — Completion & Broadcast Pipeline

```
1. Dispatcher sau khi op handler return:
   result = {
     status: "completed",      ← dispatcher thêm
     completed_at: now(),      ← auto-stamped by job_store.update_job (B5)
     project_url, media_id, edit_url, profile,
     output_files, generation_id, error: None
   }

2. remote_api.update_job(job_id, result):
   PUT /api/worker/jobs/{job_id}
   Body: JobUpdate(status="completed", project_url=..., media_id=..., ...)

3. Server route worker.py:50 (update_job):
   a. Validate JobUpdate
   b. job_store.update_job(job_id, fields):
      - UPDATE jobs SET ... WHERE id=?
      - updated_at = now()
   c. WS broadcast "job_completed" với job object

4. Frontend ws.js nhận event:
   a. Dispatch "job_completed" event
   b. Dashboard listener cập nhật card status → green checkmark
   c. Nếu có chain_id → Dashboard highlight next job

5. Worker side:
   a. profile_manager.mark_available(profile)
   b. project_lock.release(project_url) ← nhưng server đã check status nên optional
   c. Nếu job.chain_id và còn job pending cùng chain → poll loop sẽ claim tiếp
```

---

## B.10 — Chain Pipeline (multi-level)

### Ví dụ chain 4 bước

```
Input: Chain = {
  jobs: [
    {type: "text-to-video", prompt: "golden sunset ocean waves"},
    {type: "extend-video", prompt: "camera zooms out to reveal coastline"},
    {type: "insert-object", prompt: "flock of seagulls", bbox: {x:0.7,y:0.1,w:0.2,h:0.2}},
    {type: "camera-move", direction: "Dolly in"}
  ]
}

POST /api/chains → tạo 4 job:
  Job A: id=ja, type=t2v, level=1, parent=null, chain_id=C1, status=pending
  Job B: id=jb, type=extend, level=2, parent=ja, chain_id=C1, status=pending
  Job C: id=jc, type=insert, level=3, parent=jb, chain_id=C1, status=pending
  Job D: id=jd, type=camera, level=4, parent=jc, chain_id=C1, status=pending

Tại thời điểm T=0:
  - Worker W1 (profile=alpha) poll → claim được Job A (L1, any profile)
  - W1 chạy t2v → complete → Job A: project_url=P1, media_id=M1, profile=alpha

Tại T=1:
  - W1 poll → claim Job B (L2, parent profile=alpha, W1 có alpha → OK)
  - W1 chạy extend → complete → Job B: project_url=P1, media_id=M1 (SAME), profile=alpha

Tại T=2:
  - W1 poll → claim Job C (L2, parent profile=alpha → OK)
  - W1 chạy insert → complete → Job C: same project_url, same media_id

Tại T=3:
  - W1 poll → claim Job D (L2, parent profile=alpha → OK)
  - W1 chạy camera → complete → Job D: same project_url, same media_id

Chain complete. Output: 4 file trong downloads/, all versions trong history panel Flow.
```

### Điều gì chặn chain?

| Tình huống | Kết quả |
|---|---|
| Worker W2 có profile=beta poll lúc T=1 | KHÔNG claim Job B (profile mismatch) — wait |
| Project_url P1 đang có Job B running, user tạo Job X extend cùng P1 | Job X wait cho Job B xong |
| Job A fail | Chain DỪNG — Job B,C,D vẫn pending, không claim được (parent không completed) |
| User delete Job B giữa chừng | Job C,D trở thành orphan — parent_job_id refer đến deleted job |

---

## B.11 — Error / Recovery Pipeline

### B.11.1 — Worker Crash

```
Worker đang chạy Job X → crash (process killed)
→ Job X kẹt status="running", worker_id=W1

Recovery:
1. User click "Recover Stale" ở Settings page
2. Frontend → POST /api/jobs/recover
3. Server reset tất cả job:
   UPDATE jobs SET status='pending', worker_id=NULL, claimed_at=NULL
   WHERE status IN ('claimed', 'running') AND claimed_at < now() - 30min
4. Return count of jobs reset
5. Dashboard WS update, jobs trở lại pending
```

### B.11.2 — Login Session Expired

```
FlowClient.goto(flow_url) → redirect về login page
→ run_generate/extend/... detect redirect
→ Raise `NeedAutoLogin` exception

Worker dispatcher:
1. Catch `NeedAutoLogin`
2. _kill_chrome_for_profile(profile) — ⚠️ chỉ work trên Windows (wmic)
3. Trigger AIgglog subprocess:
   - Launch separate login flow
   - User manually auth nếu chưa
   - AIgglog cache cookies vào profile dir
4. Retry job (1 lần)
5. Nếu retry fail → mark profile quarantined, job fail
```

### B.11.3 — reCAPTCHA Detected

```
Mid-operation detect reCAPTCHA:
1. flow/recaptcha.py:check_for_recaptcha phát hiện iframe/text/network 403
2. Pause automation, log "reCAPTCHA detected — waiting for manual solve"
3. Poll mỗi 10s, max 120s:
   - Check recaptcha iframe còn không
   - Check body text còn "verify you're human" không
4. Nếu solved → continue operation
5. Nếu timeout → raise RecaptchaError → job fail
```

### B.11.4 — Download Fail All 4 Tiers

```
1. API 1080p fail × 3 rounds (10s interval)
2. API 720p fail × 3 rounds
3. UI right-click fail
4. Blob extract fail

→ return empty output_files list
→ Job complete (status=completed) nhưng output_files=[]
→ Frontend show warning icon

⚠️ Cần decide: nên fail job hay complete với empty output?
Hiện tại: complete với empty (silent fail). Nên đổi thành FAIL.
```

### B.11.5 — Generation Failed on Flow

```
Flow server trả "Generation failed" card:
1. wait.py DOM observer detect failed card
2. Retry logic trong flow_retry_steps.py (nếu có)
3. Hiện tại: không retry tự động, fail job ngay
4. status="failed", error="Flow generation failed"
```

---

# PHẦN C — DATA CONTRACTS

## C.1 — Job Schema (đầy đủ)

```python
# server/models/job.py

class Job(BaseModel):
    # Identity
    id: str                           # UUID
    type: JobType                     # text-to-video | extend-video | insert-object | remove-object | camera-move
    status: JobStatus                 # pending | claimed | running | completed | failed | cancelled
    
    # Chain
    job_level: int = 1                # 1 = standalone L1, 2+ = chain member
    parent_job_id: Optional[str]      # Link to parent (null for L1)
    chain_id: Optional[str]           # Group jobs in same chain
    
    # Account binding (CRITICAL — INV-1)
    profile: Optional[str]            # Chrome profile dir name = Google account
    project_url: Optional[str]        # Flow project URL
    media_id: Optional[str]           # UUID of target video
    edit_url: Optional[str]           # Computed: project_url + /edit/ + media_id
    
    # Operation params
    prompt: Optional[str]             # Required for t2v, insert; optional for extend
    model: Optional[str]              # e.g. "veo-3.1-fast-lp"
    aspect_ratio: Optional[str]       # "16:9" | "9:16" | "1:1"
    bbox: Optional[BBox]              # {x, y, w, h} normalized 0-1 — insert/remove
    direction: Optional[str]          # Camera preset name
    free_mode: bool = True            # Force LP model
    
    # Output (stored after completion — INV-3)
    output_files: List[str] = []      # Downloaded MP4 paths
    generation_id: Optional[str]      # Flow gen UUID
    
    # Worker tracking
    worker_id: Optional[str]          # Which worker claimed
    claimed_at: Optional[datetime]
    completed_at: Optional[datetime]  # auto-set by update_job on terminal status (B5)
    error: Optional[str]
    
    # Timestamps
    created_at: datetime
    updated_at: datetime


class BBox(BaseModel):
    x: float  # 0-1
    y: float  # 0-1
    w: float  # 0-1
    h: float  # 0-1
```

### Status Transitions

```
pending ──claim──▶ claimed ──start──▶ running ──success──▶ completed
   │                  │                   │                     
   │                  │                   └──fail──▶ failed     
   │                  │                                          
   │                  └──abort──▶ pending (recover stale)        
   │                                                             
   └──user cancel──▶ cancelled                                   
```

### Required fields per type (re-check khi create)

| Type | id | type | prompt | bbox | direction | parent_job_id | project_url | media_id |
|---|---|---|---|---|---|---|---|---|
| text-to-video | ✅ | ✅ | ✅ | — | — | — | — | — |
| extend-video | ✅ | ✅ | opt | — | — | ✅* | ✅* | ✅* |
| insert-object | ✅ | ✅ | ✅ | opt | — | ✅* | ✅* | ✅* |
| remove-object | ✅ | ✅ | — | ✅ | — | ✅* | ✅* | ✅* |
| camera-move | ✅ | ✅ | — | — | ✅ | ✅* | ✅* | ✅* |

`*` = cần 1 trong 3 (parent_job_id OR (project_url + media_id))

---

## C.2 — Profile Schema

```python
class Profile(BaseModel):
    name: str                         # Chrome profile dir name (= identity)
    google_account: Optional[str]     # Google email
    locale: str = "en"                # "en" | "vi"
    tier: str = "ultra"               # "ultra" | "free"   (legacy: internal LP-availability hint)
    status: str = "available"         # available | busy | quarantined
    current_job_id: Optional[str]     # set on claim, cleared on terminal (B6)
    worker_id: Optional[str]          # Worker process that owns
    created_at: datetime
    updated_at: datetime

    # ↓ POST-PHASE-A FEATURES (chưa implement — xem §D.2.7)
    # paygate_tier: Optional[str]    # "PAYGATE_TIER_ONE" | "PAYGATE_TIER_TWO"
    #                                # Capture từ /v1/credits response. Phân biệt
    #                                # tier-one (free, no LP) vs tier-two (paid + LP)
    # lp_slots_remaining: Optional[int]  # Parse từ DOM "leaving X/Y" khi select model
    # last_tier_check_at: Optional[datetime]
```

---

## C.3 — API Contracts (đầy đủ)

### Job Endpoints

```
POST /api/jobs
  Body: JobCreate (fields per §C.1)
  Response: 200 Job

POST /api/chains
  Body: ChainCreate {jobs: JobCreate[], profile?: str}
  Response: 200 {chain_id: str, jobs: Job[]}

GET /api/jobs/counts
  Response: 200 {pending, claimed, running, completed, failed, cancelled}

POST /api/jobs/recover
  Response: 200 {reset_count: int}

GET /api/jobs?status=&type=&profile=&chain_id=&limit=&offset=
  Response: 200 Job[]

GET /api/jobs/{id}
  Response: 200 Job | 404

GET /api/jobs/{id}/children
  Response: 200 Job[]

DELETE /api/jobs/{id}
  Response: 204 | 404
```

### Worker Endpoints

```
POST /api/worker/claim
  Body: ClaimRequest {worker_id: str, profiles: str[]}
  Response: 200 Job | 204 No Content

PUT /api/worker/jobs/{id}
  Body: JobUpdate {status?, project_url?, media_id?, profile?, output_files?, generation_id?, error?, completed_at?}
  Response: 200 Job

POST /api/worker/heartbeat
  Body: {worker_id: str, profiles: str[]}
  Response: 200 {}

GET /api/worker/workers
  Response: 200 Worker[]
```

### Profile Endpoints

```
GET /api/profiles
  Response: 200 Profile[]

POST /api/profiles
  Body: ProfileCreate {name, google_account?, locale?, tier?}
  Response: 200 Profile

GET /api/profiles/{name}
  Response: 200 Profile | 404

PUT /api/profiles/{name}
  Body: ProfileUpdate {google_account?, locale?, tier?, status?}
  Response: 200 Profile

GET /api/profiles/{name}/jobs
  Response: 200 Job[]
```

### WebSocket

```
WS /ws/jobs
  Server → Client events:
    {event: "job_created", job: Job}
    {event: "job_updated", job: Job}
    {event: "job_completed", job: Job}
    {event: "job_failed", job: Job}
    {event: "job_deleted", job_id: str}
  
  Client → Server: (none — one-way push)
```

---

## C.4 — WebSocket Events

| Event | Trigger | Payload |
|---|---|---|
| `job_created` | POST /api/jobs or /api/chains | full Job |
| `job_updated` | PUT /api/worker/jobs/{id} (any field change) | full Job |
| `job_completed` | PUT với status="completed" | full Job |
| `job_failed` | PUT với status="failed" | full Job |
| `job_deleted` | DELETE /api/jobs/{id} | `{job_id: str}` |

Frontend subscribe tất cả events → update dashboard state.

---

# PHẦN D — NOTES & GOTCHAS

## D.1 — Google Flow UI gotchas

### D.1.1 — Locale Detection
- URL path chứa `/vi/` → Vietnamese profile
- URL không có locale segment → English profile
- KHÔNG có language switcher trong Flow UI — locale bám chặt vào Google account language

### D.1.2 — `cards=0` after Extend click
Log thấy `cards=0` sau khi click Extend — đây là **NORMAL**, nghĩa là đã navigate vào edit view (không còn grid).

### D.1.3 — Extend không tạo modal
Extend không mở popup — chỉ toggle highlight toolbar + đổi composer placeholder.

### D.1.4 — Model selector biến mất trong Insert/Remove/Camera
3 mode này dùng model mặc định của Flow. KHÔNG gọi `select_model()` trong các op này.

### D.1.5 — media_id CHỈ có trong URL
Info panel (ⓘ) không hiển thị media_id. Phải parse từ URL.

### D.1.6 — Operations do NOT create new media_id
Extend/Insert/Remove/Camera → same media_id, same URL, +1 history entry. Nếu media_id đổi sau op → bất thường, log rõ.

### D.1.7 — Camera submit UI khác
Camera mode:
- Composer bị REPLACE bằng preset grid
- Submit DOM: `generic "See how many credits..."` + `generic "Create"` — KHÔNG phải `arrow_forward`

### D.1.8 — LP slot count
Model text hiển thị: `"Veo 3.1 - Fast [Lower Priority] (leaving 5/10)"` — con số giảm theo thời gian. Nếu = 0 → không submit được. Engine nên detect và fail gracefully.

### D.1.9 — All Veo models có audio
`volume_up` icon trên mọi model chip. Không phải flag audio on/off — chỉ là visual.

### D.1.10 — History panel = version count
Mỗi op xong thêm 1 entry. Có thể poll `history.count` để confirm completion (fallback khi 3 detection methods miss).

### D.1.11 — Generation loading visual
Blurry gradient + % counter góc trên phải. Download button grayed out. Khi hết blur + %=100% → done.

### D.1.12 — "+" button ≠ file upload
"+" ở top bar = attachment/ingredient picker (project media + upload option). Không phải upload-only.

### D.1.13 — LP model = internal key `veo_3_1_i2v_s_fast*`
Nguồn: xác minh từ [flowkit](https://github.com/crisng95/flowkit) `agent/models.json` (API-level automation, đã reverse-engineer Veo 3.1 backend).

Mapping tier ↔ internal model key dùng trong `videoModelKey` field của `batchAsyncGenerateVideoStartImage` API:

| Tier (`userPaygateTier`) | i2v (start frame only) | i2v+end (chain/extend API) | r2v (reference images) |
|---|---|---|---|
| `PAYGATE_TIER_ONE` (free) | `veo_3_1_i2v_s_fast` / `veo_3_1_i2v_s_fast_portrait` | `veo_3_1_i2v_s_fast_fl` / `_portrait_fl` | `veo_3_1_r2v_fast` / `_portrait` |
| `PAYGATE_TIER_TWO` (paid + LP free slots) | `veo_3_1_i2v_s_fast_ultra_relaxed` | `veo_3_1_i2v_s_fast_ultra_relaxed` | `veo_3_1_r2v_fast_landscape_ultra_relaxed` |

Upscale: `veo_3_1_upsampler_1080p` / `veo_3_1_upsampler_4k`.

**Ý nghĩa với FlowEngine:**
- UI label **"Veo 3.1 - Fast [Lower Priority]"** tương ứng với **TIER_TWO + `_ultra_relaxed`** (0 credit).
- UI label **"Veo 3.1 - Fast"** (không có `[Lower Priority]`) tương ứng với **TIER_ONE + `veo_3_1_i2v_s_fast`** (trả credit).
- Hàm `_select_lp_model()` của ta có thể verify bằng cách intercept network request `/v1/video:batchAsyncGenerateVideoStartImage` và assert `requests[0].videoModelKey` chứa substring `ultra_relaxed`. Nếu không → đã chọn sai model (paid).
- Suffix `_fl` = "first-last" (chain mode dùng start+end image). Đây là API-level của FlowEngine's "extend" nhưng ngữ nghĩa khác — xem §D.1.14.

### D.1.14 — "Extend" của FlowEngine (UI) ≠ "start+end frame" API (flowkit)
Hai thao tác NHÌN GIỐNG NHAU nhưng khác bản chất:

| Khía cạnh | FlowEngine `extend-video` | flowkit chain (start_end_frame_2_video) |
|---|---|---|
| Driver | UI click button "Extend" | API call with `endImage.mediaId` |
| Input | 1 video hiện có | 1 ảnh start + 1 ảnh end |
| Output media_id | **SAME** (update in-place — INV-5) | **NEW** uuid |
| Dùng được cho | Kéo dài từ frame cuối hiện tại | Morph giữa 2 frame bất kỳ |
| Chain kiểu L2/L3/L4 | ✅ | ❌ (mỗi lần phải tạo ảnh start mới) |

→ KHÔNG dùng flowkit approach thay thế được FlowEngine extend. Giữ UI automation cho extend.

---

## D.2 — Playwright / Chrome gotchas

### D.2.1 — CDP vs Playwright mode
- **CDP mode** (Windows default, `FLOW_REAL_CHROME=1`): launch Chrome thật qua DevTools Protocol, giữ extension + cookies real.
- **Playwright persistent**: Docker/headless, dùng persistent context.
- `FlowClient` detect env var → chọn mode.

### D.2.2 — Profile cloning
Chrome không cho 2 instance share 1 profile dir → FlowClient clone profile vào temp dir trước khi launch. Clone rẻ (chỉ cookies + storage).

### D.2.3 — Lock file cleanup
Chrome crash → để lại `SingletonLock` file. FlowClient xoá trước khi launch.

### D.2.4 — `_kill_chrome_for_profile` Windows-only
Dùng `wmic` → fail silent trên Linux/Mac. Cần thay bằng cross-platform `psutil` nếu deploy Linux.

### D.2.5 — Network hooks capacity
`client._calls` max 500, `client._video_urls` max 400, `client._media_id_events` max 600. Sau đó FIFO drop — long-running session có thể miss events.

### D.2.6 — Credits 404 = LP only account
`/v1/credits` 404 thường nghĩa là account không có credit (free tier) — CHỈ LP model dùng được. OK cho FlowEngine purpose.

### D.2.7 — Tier detection qua `/v1/credits` response body
Nguồn: flowkit `agent/services/flow_client.py::_sync_tier()`.

Khi 200, response body chứa field `userPaygateTier`:
```json
{
  "credits": { ... },
  "userPaygateTier": "PAYGATE_TIER_ONE"   // hoặc "PAYGATE_TIER_TWO"
}
```

→ FlowEngine có thể intercept network khi Playwright load Flow (`page.on("response", ...)` match `/v1/credits`), parse JSON, lưu vào `Profile.paygate_tier`. Giúp:
- Worker biết trước account nào có LP free slot (INV-3 + R-CODE-8).
- Debug khi LP job fail với "no credit" — phân biệt (a) LP slots = 0, (b) account là TIER_ONE (không có LP).
- Chưa implement — feature cho sau Phase A.

### D.2.8 — Fresh signed URL refresh qua TRPC intercept
Nguồn: flowkit `extension/injected.js` (monkey-patch `window.fetch`).

Vấn đề: signed URL `storage.googleapis.com/ai-sandbox-videofx/*` có TTL (~vài giờ). Nếu muốn re-download video cũ → URL cũ đã 403.

**Pattern của flowkit:**
1. Monkey-patch `window.fetch` (MAIN world của page labs.google)
2. Với mọi response từ `/fx/api/trpc/*` → nếu body chứa `storage.googleapis.com/ai-sandbox-videofx/` → capture
3. Forward pair `(mediaId, freshUrl)` về agent qua WebSocket
4. Agent match theo mediaId và update DB

**Pattern tương đương cho FlowEngine (Playwright):**
```python
page.on("response", async lambda r:
    await _capture_trpc_urls(r) if "/fx/api/trpc/" in r.url else None)
```
- Trong `_capture_trpc_urls`: đọc `r.text()`, regex `storage.googleapis.com/ai-sandbox-videofx/[^"\s]+`, pair với media_id context trong URL path.
- Gọi API `/v1/media/{media_id}?key=...&clientContext.tool=PINHOLE` → response `data.fifeUrl` / `data.servingUri` cũng chứa fresh URL (alternative, không cần intercept).

→ Chưa implement. Feature cho sau Phase A nếu có user case "xem lại video cũ".

---

## D.3 — Server / DB gotchas

### D.3.1 — SQLite `BEGIN IMMEDIATE` lock
Claim atomic dùng `BEGIN IMMEDIATE` → lock RESERVED. 2 worker gọi đồng thời → 1 succeed, 1 retry sau 100ms.

### D.3.2 — `aiosqlite` không hỗ trợ `WITH cte`
Dùng raw query hoặc sub-select. Check library version trước khi viết query phức tạp.

### D.3.3 — ~~Port default mismatch (B7)~~ ✅ FIXED (commit 336ba75..HEAD)
- ~~`server/config.py:19` → default SERVER_PORT=8000~~ → **đã sửa = 8080**
- `worker/main.py:29` → default SERVER_URL=http://localhost:8080 (đúng rồi)
- Docker override = 8080 (đúng rồi)
- Test regression: `tests/test_config.py::test_server_port_default_is_8080`

### D.3.4 — WebSocket auth
Hiện tại không có auth trên WS. LAN/localhost OK, nhưng deploy public phải thêm token.

### D.3.5 — CORS
`server/app.py` CORS allow `*` → DEV only. Deploy cần siết.

### D.3.6 — Chains table unused (B4)
`chains` table tạo nhưng không insert. Nếu muốn chain status aggregated → cần implement insert/update, hoặc compute on-demand từ `SELECT chain_id, status FROM jobs GROUP BY chain_id`.

---

## D.4 — Known bugs trong code hiện tại (B1-B17)

> Các bug này là **gap** sau khi 7 bug cũ (#2-#8) đã fix. Trong Phase A sẽ đóng. B10-B13 là residual discoveries (B10 từ B8, B11-B13 từ Tier1 DOM validation 2026-04-17). B14-B17 là stash-triage cherry-picks (2026-04-17 / 2026-04-18).

### ~~B1 — Aspect ratio stub (P0)~~ ✅ FIXED (commit `b359c84`, Tier1 MCP-verified live 2026-04-17)
- File: `flow/operations/generate.py` → `_set_aspect_ratio()`
- Triệu chứng: job có `aspect_ratio="9:16"` nhưng video output luôn 16:9 — stub chỉ tìm `button:has-text('9:16')` (không tồn tại trong DOM Flow) và fallback im lặng.
- Fix: rewrite theo Radix chip panel flow (B1a research). Mở `button[aria-haspopup="menu"]` chip → đợi `[role="menu"][data-state="open"]` → đảm bảo Video tab active → click `[id$="-trigger-PORTRAIT|LANDSCAPE"]` bằng `Locator.click` (real pointer event — JS `el.click()` KHÔNG trigger Radix state) → wait `data-state="active"` → close bằng click-outside (`page.mouse.click(10, 10)` — Escape sẽ đóng luôn composer per B8 lesson) → verify chip `innerText` chứa `crop_9_16` / `crop_16_9`. Video mode chỉ support 9:16 / 16:9; `1:1` là image-only → log warning + fallback default. Guard: `tests/test_aspect_ratio.py` (3 cases: default early-return, 1:1 warning, 9:16 full flow with mocked Locator chain). Selector reference: `docs/FLOW_UI_REFERENCE.md` §Aspect Ratio UI.

### ~~B2 — Bbox không verify (P0)~~ ✅ FIXED via B11 (initial fix `a165105` targeted wrong element → superseded by B11 commit `ce6683a`)
- File: `flow/operations/_base.py` (new `draw_bbox_on_video`), `flow/operations/insert.py`, `flow/operations/remove.py`
- Triệu chứng: `_draw_bbox()` trong insert/remove drag chuột trên video canvas nhưng không validate input range, không clamp overflow, không verify overlay rect xuất hiện → bbox nằm ngoài canvas hoặc drag miss sẽ silent-fallback về vùng default của Flow, user không biết.
- Initial fix (commit `a165105`): extract shared helper `draw_bbox_on_video(page, bbox) -> bool` vào `flow/operations/_base.py`. Helper: (1) đọc video `getBoundingClientRect` — reject nếu `width/height < 50`; (2) validate `x/y/w/h ∈ [0,1]` — out-of-range → log ERROR + return False; (3) clamp overflow (`x+w>1 → w = 1-x`); (4) mouse drag với 5 interpolation steps; (5) verify overlay via union selector `svg rect, [class*="bbox" i], [class*="selection" i], [class*="region" i], [class*="mask" i]` (bounding rect ≥ 20×20, display/visibility visible). Returns False → caller `insert.py`/`remove.py` log WARNING và continue (Flow tolerates missing bbox). Guard: `tests/test_bbox.py` (5 cases: out-of-range reject, overflow clamp, missing video, success+overlay, no-overlay warning).
- **Tier1 retest (2026-04-17) verdict on initial fix: ❌ SELECTOR MISMATCH.** Two problems against live Flow DOM: (i) `document.querySelector('video')` returns a 105×60 card-strip thumbnail — NOT the main preview; the preview is a `<canvas width=598 height=336>` CSS-sized ~479×269. The drag therefore happens on the wrong element. (ii) Flow paints the bbox onto the canvas 2D bitmap, not a DOM overlay — the union selector `svg rect, [class*="bbox" i], …` returns 0 matches regardless of drag success. Runtime effect: bbox never lands on the canvas → Flow uses default region silently. Defense-layer union selector is fundamentally insufficient against canvas-painted UI. Live evidence: `docs/session-reports/2026-04-17_Tier1_dom-validation.md` §7 B2.
- **Resolution (B11, commit `ce6683a`):** `draw_bbox_on_video` rewritten to target the largest visible `<canvas>` with `width ≥ 300` (the preview; card-strip canvases are excluded by the threshold). Drag coordinates derive from `canvas.getBoundingClientRect()`. Post-drag DOM verify REMOVED — pointer-trust model: canvas found + drag executed on its rect = Flow accepts the region. Pixel-sampling alternative rejected because the preview plays video frames continuously (`getImageData` deltas are noisy without a hand-tuned per-project threshold) and WebGL/CORS concerns could throw `SecurityError`. Input validation (0-1 range + overflow clamp) preserved from B2. Guard: `tests/test_bbox.py` rewritten to 6 cases (out-of-range reject, no-canvas reject, largest-canvas-rect drag coords, JS-contract trip-wire, overflow clamp, returns-True-no-post-verify trip-wire). Reference updated: `docs/FLOW_UI_REFERENCE.md` §Bbox Overlay UI. Session report: `docs/session-reports/2026-04-17_B11_bbox-canvas-fix.md`.
- **Tier1 R2 (2026-04-17) verdict: ✅ VERIFIED LIVE.** Re-probed on same `785d2255-…/edit/f1994aba-…` project after B11 landed. Exact JS from `_base.py:255-267` returns `{left: 144.14, top: 162, width: 478.91, height: 269.39}` — the preview canvas. `elementFromPoint(canvas_center) === CANVAS` (pointer-trust model sound). Pre-B11 `querySelector('video')` still returns the 105.6×59.8 `flow_camera/Dolly_in.mp4` thumbnail (passes the old `< 50` filter) — confirms B11's `≥ 300×200` threshold is load-bearing. Subtle finding: Insert mode mounts 2 canvases at identical rect (display + overlay layer); B11 selector picks first-by-order on area tie — drag coords identical either way. Evidence: `docs/session-reports/2026-04-17_Tier1r2_revalidation.md` §7 B11.

### ~~B3 — Camera preset không verify (P0)~~ ✅ FIXED via B12 (initial fix `58937d4` regressed → superseded by B12 commit `78d3e40`)
- File: `flow/operations/camera.py` — rewrote `_click_preset` + added `_verify_preset_selected`.
- Triệu chứng: 3 fuzzy-match strategies (`*:visible` + case-insensitive regex, partial `has-text`, `get_by_text(exact=False)`) có thể hit button sai (direction="Low" match "Lower"); không verify preset active sau click → submit với preset default, user không biết.
- Initial fix (commit `58937d4`): 3-strategy exact-match chain + union verify signal (aria-pressed | aria-selected | className keyword | parent className keyword). Guard: `tests/test_camera.py` (5 cases).
- **Tier1 retest (2026-04-17) verdict on initial fix: ❌ SELECTOR MISMATCH — REGRESSION.** All 15 presets (8 motion + 7 position) have zero `aria-label` → strategy #1 finds 0. No element on the page has explicit `role="button"` attribute (presets are `<BUTTON>` tags; Playwright's CSS `[role='button']` is strict-attr, misses implicit roles) → strategy #2 finds 0. Strategy #3 `get_by_text(exact=True)` works and clicks correctly, but all 4 verify signals return false on the selected button (styled-components hash-only classes, no `aria-pressed|aria-selected|active|selected|pressed` anywhere). `_verify_preset_selected` → False → strategy #3 falls through → all exhausted → `camera_move` raises `RuntimeError` on every call. **Every camera-move job failed hard** under `58937d4`. Live evidence: `docs/session-reports/2026-04-17_Tier1_dom-validation.md` §7 B3.
- **Resolution (B12, commit `78d3e40`):** `_click_preset` pruned to the only strategy that matches real Flow DOM (`get_by_text(direction, exact=True)` — Playwright's native `exact=True` preserves the partial-match defense without an anchored regex). `_verify_preset_selected` rewritten to read `getComputedStyle(labelDiv).color` on the inner label DIV inside the preset BUTTON — the only semantic, release-stable selection signal on Flow's DOM (selected = `rgb(48,48,48)` sum 144; unselected = `rgb(255,255,255)` sum 765; threshold R+G+B < 400). Guard: `tests/test_camera.py` rewritten to 7 cases (verify true/false/exception/JS-contract + click succeeds/absent/unverified). Reference updated: `docs/FLOW_UI_REFERENCE.md` §Camera Preset Selection & Active State.
- **Tier1 R2 (2026-04-17) verdict: ✅ VERIFIED LIVE.** Re-probed on same `785d2255-…/edit/f1994aba-…` project after B12 landed. Exact JS from `camera.py:186-202` executed verbatim. Baseline: all 13 preset labels bright `rgb(255,255,255)` sum 765, `threshold_passes: false`. After real-pointer click on "Dolly in": selected label `rgb(48,48,48)` sum 144, `threshold_passes: true`; other 5 motion presets remain unselected. Flip test — click "Dolly out": "Dolly in" returns to sum 765 `false`, "Dolly out" becomes sum 144 `true`. Threshold 400 sits cleanly between ground-truth sums (256-margin each side). Evidence: `docs/session-reports/2026-04-17_Tier1r2_revalidation.md` §7 B12.

### B4 — Chains table không dùng (P2, defer)
- File: `server/db/database.py:12-20`
- Không ảnh hưởng correctness
- Defer đến khi cần chain-level analytics

### ~~B5 — `completed_at` không set (P1)~~ ✅ FIXED (commit `4d24c10`)
- File: `server/db/job_store.py:update_job`
- Triệu chứng: cột NULL sau khi complete — không caller nào từng set timestamp này
- Fix: `update_job` tự stamp `completed_at = _now_iso()` khi `status` ∈ {completed, failed, cancelled} và caller không set explicit. `JobUpdate` mở rộng thêm field `completed_at` (optional) để caller vẫn có thể override. Guard: `tests/test_job_store.py` (4 cases: auto-set on completed / failed, explicit wins, non-terminal no-op).

### ~~B6 — Profile.current_job_id không reset (P1)~~ ✅ FIXED (commit `0118e6d`)
- File: `server/db/job_store.py:claim_next_job` + `update_job`
- Triệu chứng: cột `profiles.current_job_id` NULL vĩnh viễn — không caller server-side nào từng set/clear field này. Worker-side `ProfileManager.mark_available` có clear in-memory nhưng không sync ra DB → dashboard không biết profile nào đang chạy job gì.
- Fix: `claim_next_job` stamp `profiles.current_job_id = <job.id>` (kèm `worker_id`, `last_used_at`) trong cùng transaction với UPDATE jobs — áp dụng cho cả 2 priority branch (L2+ parent-bound và L1 available-pool). `update_job` clear `current_job_id = NULL WHERE current_job_id = job_id` khi status ∈ TERMINAL_STATES (module-level constant hoisted from B5 inline set). Non-terminal transition (running) không đụng pointer. Guard: `tests/test_profile_store.py` (3 cases: set-on-claim, cleared-on-completion, not-cleared-on-running).

### ~~B7 — Port mismatch (P0)~~ ✅ FIXED
- Files: `server/config.py:19`, `worker/main.py:29`
- Fix: thống nhất 8080 everywhere

### ~~B8 — datetime.utcnow deprecated (P1)~~ ✅ FIXED (commit `573cffd`)
- Triệu chứng: worker_err.log DeprecationWarning
- Fix: replaced 7 `datetime.utcnow()` call-sites in `worker/main.py`, `server/db/job_store.py`, `server/db/profile_store.py`, `server/routes/worker.py` with `datetime.now(UTC)` (tz-aware). Guard: `tests/test_datetime_migration.py` (source scan + round-trip).
- Out of scope (deferred → B10): 3 `default_factory=datetime.utcnow` references in `server/models/job.py:96-97` and `server/models/profile.py:25` still emit DeprecationWarning if default is triggered.

### ~~B9 — Zero test coverage (P0)~~ ✅ FIXED (commit `adca116`)
- `tests/` rỗng → no TDD infra
- Fix: added `tests/conftest.py` (temp DB + api_client fixtures), `tests/test_smoke.py`, `requirements-dev.txt`, `pytest.ini`. Coverage target ≥70% deferred to post-phase-A; B9 provides the *foundation* only.

### ~~B10 — Pydantic `default_factory=datetime.utcnow` residual (P2)~~ ✅ FIXED (commit `fe13870`)
- Discovered during B8 (see session report 2026-04-17_B8_datetime-utcnow.md §7).
- 3 sites: `server/models/job.py:96`, `server/models/job.py:97`, `server/models/profile.py:25` — `Field(default_factory=datetime.utcnow)`.
- Factory triggers when caller omits `created_at` / `updated_at` (e.g. `server/routes/jobs.py:_build_job`) → DeprecationWarning on Python 3.12+, potential AttributeError on 3.13+.
- **Resolution (commit `fe13870`):** replaced with `default_factory=lambda: datetime.now(UTC)` at all 3 sites (Choice 1 — inline lambda, not shared helper; rationale in report §7 Q1: only 3 call sites and single-module scope make `_utils.py` premature abstraction). `from datetime import UTC, datetime` added to `server/models/job.py` + `server/models/profile.py`. Live-instantiate sanity check confirms factory produces tz-aware datetime (`tzinfo=UTC`) with zero DeprecationWarning under `python -W error::DeprecationWarning`.
- Guard (extended): `tests/test_datetime_migration.py::test_no_utcnow_in_code` now also forbids `default_factory=datetime.utcnow` substring — trip-wire prevents silent reintroduction of the reference-form (no parens) pattern that B8's call-form scan missed.
- Session report: `docs/session-reports/2026-04-18_B10_pydantic-default-factory.md`.

### ~~B11 — Bbox drag targets wrong element + overlay is canvas-painted (P0)~~ ✅ FIXED (commit `ce6683a`)
- Discovered during Tier1 DOM validation (`docs/session-reports/2026-04-17_Tier1_dom-validation.md` §7 B2) on live L1 project `785d2255-…`.
- File: `flow/operations/_base.py:236` (`draw_bbox_on_video`) — supersedes B2 fix commit `a165105`.
- Two independent problems:
  1. **Wrong target.** `document.querySelector('video')` returns a 105×60 card-strip thumbnail, not the main preview. The main preview is a `<canvas width=598 height=336>` CSS-sized ~479×269. All `elementFromPoint` samples inside the visible bbox return `<CANVAS>`. Drag currently lands on the thumbnail → never reaches the actual frame.
  2. **Bbox overlay is canvas-painted, not DOM.** Flow renders the bbox rectangle onto the canvas 2D bitmap. The B2 verify union selector `svg rect, [class*="bbox" i], [class*="selection" i], [class*="region" i], [class*="mask" i]` returns 0 matches regardless of whether the drag succeeded. Verify step provides no real confidence.
- Runtime effect (pre-fix): every `insert-object` / `remove-object` job silently fell back to Flow's default region, masked by "Bbox drawing failed or unverified — Flow may fall back to default region" warning (always emitted).
- **Resolution (commit `ce6683a`):**
  - `draw_bbox_on_video` targets **the largest visible `<canvas>` with `width ≥ 300`**. A single `page.evaluate` walks `document.querySelectorAll('canvas')`, filters by `getBoundingClientRect().width ≥ 300 && height ≥ 200` (excludes ~105 px card-strip canvases), and picks the one with the largest area. Drag coordinates derive from that canvas rect.
  - Post-drag DOM verify **removed entirely** — pointer-trust model. Option A (pixel sampling) was rejected because (i) the preview canvas plays video frames continuously → `getImageData` has natural delta noise that defeats threshold-based verification without per-project tuning, and (ii) CORS-tainted or WebGL-backed canvases throw `SecurityError` on `getImageData`. Option B (pointer-trust) has zero failure modes from either concern. Full rationale: `docs/session-reports/2026-04-17_B11_bbox-canvas-fix.md` §7.
  - Input validation preserved from B2 — `x/y/w/h ∈ [0,1]` rejected at ERROR; overflow (`x+w>1`) clamped to fit canvas.
  - Return contract preserved: `False` on pre-drag failure (no canvas ≥ 300×200 or input out of range), `True` after drag executes on the canvas. Caller `insert.py` / `remove.py` warning block still meaningful (fires on genuine pre-drag failures only).
- Guard (rewritten): `tests/test_bbox.py` — 6 cases:
  - `test_bbox_rejects_out_of_range` — `x=1.5` → False + ERROR, no drag.
  - `test_bbox_rejects_no_canvas` — JS returns None → False + ERROR, no drag.
  - `test_bbox_targets_largest_canvas_rect` — drag start/end derived from the 600×400 canvas rect (not viewport or video rect).
  - `test_bbox_evaluate_script_targets_canvas` — contract trip-wire: JS source contains `canvas` + `300`, does NOT contain `querySelector('video')`. Prevents silent regression to the B2 target.
  - `test_bbox_clamps_overflow` — `x=0.7, w=0.5` → `w` clamped to `0.3`, drag end caps at canvas edge.
  - `test_bbox_returns_true_after_drag_no_post_verify` — contract trip-wire: `page.evaluate.await_count == 1` (one call: canvas-find). Prevents silent reintroduction of a post-drag verify step.
- Reference updated: `docs/FLOW_UI_REFERENCE.md` §Bbox Overlay UI (canvas target + pointer-trust rationale + pitfall list + updated coordinate system).
- Session report: `docs/session-reports/2026-04-17_B11_bbox-canvas-fix.md`.

### ~~B12 — Camera `_verify_preset_selected` uses wrong state signals → every camera job fails (P0, regression)~~ ✅ FIXED (commit `78d3e40`)
- Discovered during Tier1 DOM validation (`docs/session-reports/2026-04-17_Tier1_dom-validation.md` §7 B3) on live L1 project `785d2255-…`.
- File: `flow/operations/camera.py:133` (`_click_preset` + `_verify_preset_selected`) — regression introduced by commit `58937d4`.
- Live DOM reality vs code assumptions:
  - Presets have **no `aria-label`** → strategy #1 finds 0.
  - Presets are `<button>` tags with **no explicit `role="button"` attribute** (Playwright CSS `[role='button']` requires the attribute, it does not match implicit roles) → strategy #2 finds 0.
  - Strategy #3 `get_by_text(exact=True)` finds + clicks the preset successfully (Flow accepts the click — preview animates, submit enables).
  - But `_verify_preset_selected` then checks four signals that **all return false on the actual selected preset**: `aria-pressed` absent, `aria-selected` absent, button className = `sc-16c4830a-1 hxjMEo … byyZkY` (pure styled-components hashes — no `active|selected|pressed` keyword), parent className = `sc-2384ceab-7 jrdoRH` (no keyword).
  - `_verify_preset_selected` → False → strategy #3 falls through → all exhausted → `_click_preset` returns False → `camera_move` raises `RuntimeError("Failed to find camera preset: {direction}")`.
- Runtime effect under `58937d4`: **every camera-move job failed hard.** Pre-`58937d4` code (no verify) would have clicked and submitted successfully with the same DOM.
- The actual semantic selection marker is `getComputedStyle(labelDivInsideButton).color`:
  - Selected: `rgb(48, 48, 48)` (dim — inverted because thumbnail is highlighted), R+G+B = 144.
  - Unselected: `rgb(255, 255, 255)` (bright), R+G+B = 765.
  - Styled-components token on the label DIV also differs (`jYmHac` selected vs `hkGUbO` unselected) but tokens may rotate per Flow release — color is more stable.
- **Resolution (commit `78d3e40`):**
  - `_click_preset` pruned to a single strategy: `page.get_by_text(direction, exact=True).first`. Playwright's native `exact=True` preserves the partial-match defense (direction "Low" cannot match a hypothetical "Lower" button) without a separate anchored regex. Strategies #1 (`[aria-label=…]`) and #2 (`[role='button']` CSS + regex) were removed — Tier1 confirmed both match 0 elements on Flow DOM, so they were dead defensive code per spec §1.3.
  - `_verify_preset_selected` rewritten to run a single `page.evaluate` that walks `<button>` elements, finds the descendant DIV whose text equals direction, reads `getComputedStyle(lbl).color`, parses `rgb(r, g, b)`, and returns true when `r + g + b < 400` (threshold sits halfway between the selected sum 144 and unselected sum 765). Returns false on missing label DIV, unparseable color, or bright-color result. Exceptions from `page.evaluate` are swallowed → False.
- Guard (rewritten): `tests/test_camera.py` — 7 cases:
  - `test_verify_returns_true_on_dim_color` — evaluate=True → verify True + INFO.
  - `test_verify_returns_false_on_bright_color` — evaluate=False (bright or missing label) → verify False + WARNING.
  - `test_verify_returns_false_on_evaluate_exception` — exception swallowed → verify False + WARNING.
  - `test_verify_script_uses_computed_color_signal` — contract: JS text contains `getComputedStyle`, `color`, `rgb`. Prevents silent regression to attribute-based checks.
  - `test_click_preset_get_by_text_succeeds` — happy path + asserts `page.locator` NEVER called (pruning contract) + `exact=True` passed.
  - `test_click_preset_returns_false_when_preset_absent` — not-visible → ERROR, no click.
  - `test_click_preset_clicked_but_color_verify_fails` — clicked+verify False → ERROR (no strategy fallthrough since only one remains).
- Reference updated: `docs/FLOW_UI_REFERENCE.md` §Camera Preset Selection & Active State (active-state signal table + post-B12 click-strategy reduction + pruning rationale).
- Session report: `docs/session-reports/2026-04-17_B12_camera-verify-fix.md`.

### B13 — Docs: replace "Known unknowns" in FLOW_UI_REFERENCE with ground-truth after B11/B12 (P2)
- Tier1 retest produced ground truth that obsoletes `docs/FLOW_UI_REFERENCE.md` §Bbox Overlay UI and §Camera Preset Selection & Active State "Known unknowns" paragraphs.
- After B11 ships: document canvas element selection + pixel-sampling verify pattern in §Bbox Overlay UI.
- After B12 ships: document `getComputedStyle(label).color` as the semantic state signal in §Camera Preset Selection & Active State; note that strategy #1 (`aria-label`) and #2 (CSS `[role='button']`) find 0 elements on the live DOM and exist only as defense layers.
- Queue position: bundled with each respective code fix (not standalone).

### ~~B14 — L2+ nav can click wrong tile + enter edit mode silently on wrong media (P1)~~ ✅ FIXED (commit `72e056b`)
- Cherry-picked from `stash@{0}` via triage `docs/session-reports/2026-04-17_stash-triage_flow-refinements.md` §7 KEEP-2 + KEEP-3. Two orthogonal hardening hunks on `flow/operations/_base.py`.
- File: `flow/operations/_base.py` — `navigate_to_edit` (post-nav verify block added) + `_click_video_tile` (body rewritten).
- Two independent problems:
  1. **Silent nav failure.** Master's `navigate_to_edit` returns normally even if the last-resort `page.goto(edit_url)` leaves the page on `/project/...` (not `/edit/...`) — the caller then submits an op against the project grid, which clicks the wrong button (e.g. "New video" instead of "Extend"). No exception, no log.
  2. **Wrong-tile click in multi-video projects.** `_click_video_tile` iterated generic selectors (`video`, `[data-tile-id]`, `[class*='tile']`, `[class*='thumbnail']`, `img[src*='googleusercontent']`) and clicked `.first`. In a project with ≥ 2 videos the "first" match is DOM-order dependent, not media_id-targeted → clicking an unrelated video enters edit mode for the wrong media_id, silently violating INV-5 (media_id stable across the chain).
- Runtime effect (pre-fix): L2+ jobs on multi-video projects could extend/insert/remove/camera against a sibling video; chain state (parent.media_id) and page state diverge with no warning.
- **Resolution (commit `72e056b`):**
  - **KEEP-2 (post-nav verify).** After the tile-click-or-goto attempts, `navigate_to_edit` now reads `page.url` and raises `RuntimeError("Failed to enter edit mode")` if `/edit/` is absent. If `/edit/` is present but the URL's media_id differs from the requested `job["media_id"]`, log a WARNING and proceed — Flow's SPA sometimes redirects to a sibling video in the same project, which is acceptable (the important invariant is being in edit mode for some video in the correct project; `finalize_operation` will re-extract the actual media_id from the final URL).
  - **KEEP-3 (media_id-aware tile click).** `_click_video_tile` body rewritten to a 3-priority chain:
    1. If `media_id` is given, `page.evaluate` walks `a[href*="/edit/"]`, `[data-tile-id]`, and `[data-media-id], [data-id]` in turn, clicks the first element whose href/attribute contains the target `media_id`, and returns a short debug tag (`link:…` / `tile:…` / `data-id:…`).
    2. If JS finds no match (or `media_id` is empty), click `page.locator("[data-tile-id]").first`.
    3. Otherwise click `page.locator("video").first`.
  - Explicit rejections from the stash (supervisor decision per triage §7):
    - **H1 (nav strategy reversal)** — REJECTED. Master's "project URL first, tile click, then direct edit URL as last resort" strategy has a written rationale ("Direct /edit/ URLs often fail because the Flow SPA needs the project context loaded first") and passed Phase A validation. Stash's reversal lacked counter-evidence.
    - **H4 (`_click_storyboard_video` helper)** — REJECTED. Defined but never called anywhere in the stash — dead code.
  - `draw_bbox_on_video` and the bbox canvas logic (B11, commit `ce6683a`) are in the same file but untouched.
- Guard: `tests/test_base.py` — 7 cases (separate file from `test_bbox.py` which covers the orthogonal `draw_bbox_on_video`):
  - `test_navigate_warns_on_media_id_mismatch` — requested A, landed on B → WARNING contains both ids, function returns normally.
  - `test_navigate_no_warning_on_media_id_match` — URL media_id = requested → no mismatch WARNING.
  - `test_navigate_raises_when_not_in_edit_mode` — tile click fails, last-resort goto doesn't change URL → `RuntimeError("Failed to enter edit mode")`.
  - `test_click_tile_priority1_js_receives_media_id` — `page.evaluate` receives the media_id as its second arg (proves media_id-filter, not generic `.first` click).
  - `test_click_tile_js_script_matches_media_id_selectors` — contract trip-wire: JS source contains `a[href*="/edit/"]`, `data-tile-id`, `data-media-id`. Prevents silent regression to a generic-first click.
  - `test_click_tile_priority2_falls_back_to_data_tile_id` — JS returns None → `[data-tile-id].first` clicked, not `video.first`.
  - `test_click_tile_no_media_id_skips_js_priority` — no media_id → `page.evaluate` never called; goes straight to locator fallback. Keeps legacy L1-only call sites working.
- Session report: `docs/session-reports/2026-04-17_B14_base-nav-verify.md`.

### ~~B15 — Extend panel silent fail + thin submit diagnostics + DOM-order-only Slate selector (P1)~~ ✅ FIXED (commit `caef3e9`)
- Cherry-picked from `stash@{0}` via triage `docs/session-reports/2026-04-17_stash-triage_flow-refinements.md` §7 KEEP-4 + KEEP-5 + KEEP-6. Three small hunks on `flow/operations/extend.py`.
- File: `flow/operations/extend.py` — `extend_video` (Step 3.5 call + submit diagnostic block) + new `_verify_extend_panel` helper + `_type_extend_prompt` (Method 1 prepended).
- Three independent problems:
  1. **Silent panel-open failure.** Master's `extend_video` clicks Extend, then goes straight to typing the prompt. If the panel fails to open (UI race, Flow SPA loading state, click landed on disabled button, stale action-bar render), `_type_extend_prompt` finds only the main composer's Slate editor, types into it (contaminating the main prompt), and the flow eventually times out at submit — 15s later, with no diagnostic of what went wrong.
  2. **Thin submit failure diagnostics.** When `submit_with_confirmation` returns False, master raises `RuntimeError("Extend submit not confirmed")` with no page state. Post-mortem has to re-run the job with full logging; the raise itself doesn't capture the URL or editor count at timeout.
  3. **DOM-order-only Slate selector.** Master's `_type_extend_prompt` identifies the extend panel's editor purely by position (last `[data-slate-editor='true']` in DOM). This works when DOM order is stable but depends on Flow rendering the extend panel AFTER the main composer. A more precise selector (`[data-scroll-state='START'] [data-slate-editor='true']`) targets the panel by an extend-specific attribute rather than ordering.
- Runtime effect (pre-fix): extend jobs hitting a panel-open race fail silently through prompt-typing into wrong editor, then submit times out 15s later with only "Extend submit not confirmed". Operator has no signal whether the issue is the click, the panel, the prompt, or the submit itself.
- **Resolution (commit `caef3e9`):**
  - **KEEP-4 (panel verify).** New helper `_verify_extend_panel(page, timeout_sec=5.0)` polls `[data-slate-editor='true']` count and `[data-scroll-state='START']` count every 0.5s for up to 5s. Returns True on `editors >= 2` OR `panels >= 1` (either signal confirms the extend panel mounted); False on timeout (with ERROR log of final editor count for diagnosis). Called between Step 3 (click Extend) and Step 4 (type prompt) as "Step 3.5". On False, `extend_video` raises `RuntimeError("Extend panel did not open after clicking Extend button")` — fail-fast with a specific message, not a 15s submit timeout.
  - **KEEP-5 (submit diagnostics).** When `submit_with_confirmation` returns False, log ERROR with `url=%s editors=%d` (URL truncated to 100 chars, editor count for panel-still-open check) BEFORE raising. Raise message updated to `"Extend submit not confirmed — generation did not start"` (more specific than master's bare wording; clarifies the failure is at confirmation, not click).
  - **KEEP-6 (scroll-state-aware Slate selector — partial).** `_type_extend_prompt` now tries a NEW Method 1 (`page.locator("[data-scroll-state='START'] [data-slate-editor='true']")`) BEFORE the existing Method 2 (last Slate editor in DOM). Method 1 is extend-panel-specific; Method 2 is the master heuristic, unchanged. If both fail, the 4 master placeholder/aria-label fallbacks still run (see rejection below).
  - Explicit rejection from the stash (supervisor decision per triage §7):
    - **H5 (placeholder fallback removal)** — REJECTED. Stash removes the 4 `[placeholder*='next' i]`, `[placeholder*='tiếp' i]`, `[placeholder*='tiep' i]`, `[aria-label*='extend' i]` fallbacks after the two Slate methods. Supervisor preserves them as defense-in-depth: if both Slate selectors miss (unknown Flow DOM refactor), the placeholder-based fallbacks are a last chance before the flow proceeds without a prompt. Cost is 4 extra selector probes on the rare Slate-miss path — acceptable.
  - `finalize_operation` and downstream state-storage (B11 canvas bbox, B14 tile click) untouched.
- Guard: `tests/test_extend.py` — 12 cases:
  - `test_verify_returns_true_on_two_slate_editors` — `editors=2` → True + INFO.
  - `test_verify_returns_true_via_scroll_state` — `editors=1, panels=1` → True via scroll-state signal + INFO.
  - `test_verify_returns_false_on_timeout` — `editors=1, panels=0` persistent → False + ERROR.
  - `test_verify_checks_both_selectors` — contract trip-wire: helper probes both `[data-slate-editor='true']` AND `[data-scroll-state='START']`. Prevents regression to a single-signal check.
  - `test_extend_raises_when_panel_not_open` — Step 3.5: `_verify_extend_panel` False → `RuntimeError("Extend panel did not open")`; `submit_with_confirmation` / `finalize_operation` NOT called (fail-fast).
  - `test_extend_proceeds_when_panel_open` — panel open → type + select + submit + finalize all reached; result passes through.
  - `test_extend_submit_failure_logs_diagnostics` — submit False → ERROR with `url=...` AND `editors=...`; raises `"generation did not start"`.
  - `test_extend_submit_success_skips_diagnostic_log` — negative contract: success path leaves no `"not confirmed"` ERROR.
  - `test_type_extend_prompt_method1_uses_scroll_state` — Method 1 selector found + visible → click + type via it; Method 2 NOT consulted.
  - `test_type_extend_prompt_method1_contract_selector` — contract trip-wire: Method 1 compound selector probed BEFORE Method 2's bare `[data-slate-editor='true']`. Prevents silent regression to a Method-2-only implementation.
  - `test_type_extend_prompt_falls_back_to_last_slate` — Method 1 count=0 → Method 2 clicks `editors.nth(count-1)` (master's "last Slate" behavior preserved); log contains "slate editor".
  - `test_type_extend_prompt_preserves_placeholder_fallbacks` — H5 REJECTED contract: the 4 placeholder/aria-label selectors MUST still be probed when both Methods 1+2 miss. Prevents silent drift toward stash's removal.
- Session report: `docs/session-reports/2026-04-17_B15_extend-panel-verify.md`.

### ~~B16 — `click_submit` gives up on selector if `.first` is disabled (P1)~~ ✅ FIXED (commit `004d8fb`)
- Cherry-picked from `stash@{0}` via triage `docs/session-reports/2026-04-17_stash-triage_flow-refinements.md` §7.4 KEEP-7. One hunk on `flow/submit.py::click_submit`.
- File: `flow/submit.py` — `click_submit` (selector-iteration loop body only; the module-level `SUBMIT_SELECTORS` list, `_SKIP_PATTERN` regex, keyboard fallback, and `submit_with_confirmation` wrapper are all untouched).
- Triệu chứng: Master's `click_submit` calls `page.locator(selector).first` for each of the 6 submit selectors. If `.first` is a disabled match — loading state during generation, stale duplicate DOM node from a previous composer render, hidden shadow tree leftover from a closed dialog — master either clicks it anyway with `force=True` (silent no-op; user sees no generation) or skips on `is_visible=False` and falls through to the next selector. An ENABLED sibling matching the *same* selector is never probed. When all 6 selectors all hit the same stale `.first`, the flow falls to `Ctrl+Enter` which only works on some composer variants. Net effect: intermittent "submit click not registered" bugs with no log breadcrumb (master logs only `"Submit clicked via: <selector>"` on success, nothing on per-selector skip).
- Runtime effect (pre-fix): job submits silently fail — generation never starts, `submit_with_confirmation` times out at 15s with the four confirmation signals (gen_id, operations API, card count, progress indicator) all negative. Post-mortem has no button-state trail. Stash's docstring cites duplicate DOM nodes as the trigger; common in the extend composer where the main composer's submit button and the extend panel's submit button can co-exist briefly during panel mount.
- **Resolution (commit `004d8fb`):**
  - **Iterate all matches, not `.first`.** Per selector, call `page.locator(selector).count()` and iterate `for i in range(count)` via `locator.nth(i)`. For each button: probe `is_visible(timeout=500)`, then `is_enabled(timeout=300)` only if visible (short-circuit), then `inner_text()` for the `_SKIP_PATTERN` noise filter. Click the first match that is `visible AND enabled AND not _SKIP_PATTERN`. `_SKIP_PATTERN` (master's regex against `image|video|frames|ingredients|reference|9:16|16:9|x1-4|veo|lower priority`) is preserved *inside* the loop — the filter still runs per button, not replaced.
  - **Per-button debug log.** `logger.debug("  btn[%d]: vis=%s ena=%s skip=%s text=%s", i, vis, ena, skip, text.strip()[:30])` — gives post-mortem visibility into every probed button's state. Plus a selector-entry log `logger.debug("Submit selector %s: count=%d", selector, count)`. Plus a selector-error log `logger.debug("Submit selector %s error: %s", selector, e)`. All at DEBUG level (silent at INFO baseline; opt-in via `logging.getLogger('flow.submit').setLevel(logging.DEBUG)`).
  - **Success log includes the winning index.** `logger.info("Submit clicked via: %s [%d] text=%s", selector, i, text.strip()[:30])` — distinguishes a `.nth(3)` win from `.nth(0)` so operators can see whether the fix is doing real work in production.
  - **Fall-through unchanged.** If every selector iterates to exhaustion with no clickable match, `click_submit` falls to the existing `page.keyboard.press("Control+Enter")` branch. The cherry-pick does NOT touch this path.
  - **`submit_with_confirmation` untouched.** Phase A commit `5c7d625` (timeout-returns-False + NEW-api-calls delta snapshot) is orthogonal — the cherry-pick only modifies the inner loop of `click_submit`, not its caller. `git diff flow/submit.py` confirms zero lines changed outside the `for selector in SUBMIT_SELECTORS:` body.
- Guard: `tests/test_submit.py` — 8 cases:
  - `test_click_submit_iterates_all_buttons` — selector matches 3 buttons (disabled + invisible + enabled) → code iterates, clicks only the enabled one, INFO log includes `[2]` index.
  - `test_click_submit_skip_disabled_first` — `.nth(0)` disabled + `.nth(1)` enabled → click `.nth(1)`; only ONE selector probed (no fall-through). Core contract trip-wire.
  - `test_click_submit_skip_pattern_preserved` — btn[0] text `"Generate video"` matches `_SKIP_PATTERN` → skipped; btn[1] text `"Create"` clicked. Confirms the master noise filter still runs inside the per-button loop.
  - `test_click_submit_no_enabled_button` — selector A has 2 disabled → fall through to selector B's enabled match. Selector-level fall-through (master's only strategy) still works on top of per-selector iteration.
  - `test_click_submit_debug_log_per_button` — DEBUG records include `btn[0]`, `btn[1]`, and selector-level `count=`. Prevents silent drift to a single aggregate log.
  - `test_click_submit_all_disabled_falls_back_to_keyboard` — every selector returns 1 disabled button → Ctrl+Enter fallback reached; INFO log mentions `"Ctrl+Enter"`. Guards against accidentally short-circuiting the fallback.
  - `test_click_submit_zero_count_falls_through` — `count()` returns 0 → skip iteration, probe next selector.
  - `test_click_submit_per_button_exception_does_not_abort` — `is_visible` raises on btn[0] → loop moves to btn[1] and clicks. Guards against a future refactor that flattens the per-button try/except into the outer `except Exception:`.
- Session report: `docs/session-reports/2026-04-18_B16_submit-iterate.md`.

### ~~B17 — `select_model` can toggle-close LP panel in extend mode by always opening dropdown (P1)~~ ✅ FIXED (commit `f5dab42`)
- Cherry-picked from `stash@{0}` via triage `docs/session-reports/2026-04-17_stash-triage_flow-refinements.md` §7 KEEP-1. Single standalone hunk on `flow/model_selector.py`.
- File: `flow/model_selector.py` — `select_model` (Step 2.7 rewritten: LP items pre-check before `_open_model_dropdown`).
- Problem: in extend mode, the Flow model panel may ALREADY surface LP options directly after the Video-tab switch (no inner dropdown click needed). Master's `select_model` unconditionally calls `_open_model_dropdown(page)` which clicks the "Veo … arrow_drop_down" chip — and clicking it when the panel is already showing LP items TOGGLES the panel CLOSED, hiding the very items the retry loop is about to search for. The retry loop then sees 0 LP items across all 3 attempts, the JS fallback also finds nothing (panel dismissed), and `select_model` returns False.
- Runtime effect (pre-fix): LP model selection in extend mode can fall through 3 retry attempts (4.5s of `asyncio.sleep(1.5)`) + JS fallback + close → all returning empty. The caller (`extend_video` → `select_model` returns False) proceeds with the remembered model — which may NOT be LP if the account's default is a paid tier → non-free submit → credit leak in the worst case (same class as B8 LP credit leak, different trigger: B8 closed by dismiss-misfire, B17 by open-double-click).
- **Resolution (commit `f5dab42`):**
  - **KEEP-1 (LP pre-check).** Before calling `_open_model_dropdown`, `select_model` now counts `page.locator(MODEL_ITEM_SELECTORS).filter(has_text=re.compile(r"Lower Priority", re.IGNORECASE))` — the SAME filter the retry loop uses. If count > 0, log `"LP items already visible (N) — skipping dropdown open"` and skip the open call (leaving `dropdown_opened=False`, since the panel state matches the "not toggled by us" branch). If count == 0 OR the target isn't LP OR the locator raises, fall through to `dropdown_opened = await _open_model_dropdown(page)` — master's unconditional behavior is preserved for the common case.
  - `is_lp` + `base_name` + `MODEL_ITEM_SELECTORS` are computed BEFORE the pre-check (hoisted up from their original position inside the retry loop). No semantic change for the retry loop — it still reads the same three locals.
  - Explicit rejections from the stash (user decision per triage §7.1 CONFLICT row + supervisor prompt):
    - **H1 (capture `chip_handle` before click)** — REJECTED. Dependency of H4; no standalone value.
    - **H3 (thread `chip_handle` + `chip_tagged_js` through the 4 call sites of `_close_model_panel`)** — REJECTED. Signature change required only for H4.
    - **H4 (rewrite `_close_model_panel` to re-click the captured chip)** — REJECTED. User preserves master's click-outside (Slate editor click) + single Escape fallback from B8 commit `7245ae8`, which passed Phase A validation (LP credit leak fix). Stash's "toggle-close by re-clicking chip" philosophy was not adopted.
  - `_close_model_panel(page, dropdown_was_opened)` signature UNCHANGED. Master's click-outside body UNCHANGED. The 4 existing call sites still pass `dropdown_opened`; the semantics of `dropdown_opened` (True if we opened it, False if we did not) are preserved.
- Guard: `tests/test_model_selector.py` — 7 cases:
  - `test_lp_precheck_skips_open_when_items_already_visible` — RED→GREEN core contract: mock page with lp_count=2 + monkeypatched `_open_model_dropdown` spy → `select_model("veo-3.1-fast-lp")` returns True AND spy NOT called AND INFO log contains "already visible". Master (unconditional open) fails the `assert_not_called()`.
  - `test_lp_precheck_opens_when_items_not_visible` — regression guard: lp_count=0 → pre-check else-branch → `_open_model_dropdown` called once. Preserves master's common-case behavior.
  - `test_non_lp_model_skips_precheck_and_opens_directly` — else-branch contract: non-LP target (`veo-3.1-quality`, `free_mode=False`) → `is_lp=False` → outer else → `_open_model_dropdown` called without pre-check. Pre-check is LP-specific.
  - `test_precheck_exception_falls_back_to_open` — resilience contract: `.count()` side_effect raises RuntimeError on first call (pre-check), returns 2 thereafter → except branch calls `_open_model_dropdown`, retry loop still finds the LP item, `select_model` returns True. Pre-check is an optimization, never a blocker.
  - `test_precheck_source_uses_lp_regex_and_skip_message` — source-level trip-wire (RED→GREEN): `select_model` body contains "already visible" + "skipping dropdown open" log strings AND ≥ 2 occurrences of `re.compile(r"Lower Priority"` (pre-check + retry loop — same selector, same filter). Prevents silent drift back to master "always open".
  - `test_close_model_panel_signature_unchanged` — H3 REJECTED contract: `inspect.signature(_close_model_panel).parameters` keys == `["page", "dropdown_was_opened"]`. Guards against accidentally adopting H1/H3 chip-handle threading.
  - `test_close_model_panel_preserves_click_outside_approach` — H4 REJECTED contract: `_close_model_panel` body contains `[data-slate-editor='true']` (master's click-outside target); does NOT contain `chip_handle`, `chip_tagged_js`, or `data-flow-chip`. Guards against stash's toggle-close rewrite leaking in via a later cherry-pick.
- Session report: `docs/session-reports/2026-04-18_B17_lp-precheck.md`.

---

# PHẦN E — DEBUG PLAYBOOK

## E.1 — Theo triệu chứng

| Triệu chứng | Nghi ngờ | File cần check đầu tiên | Log cần bật |
|---|---|---|---|
| Job stuck ở `pending` mãi | Worker không poll hoặc profile mismatch | `worker/main.py`, `server/db/job_store.py:claim_next_job` | worker_out.log |
| Job stuck ở `claimed` > 30 phút | Worker crashed | Settings → Recover Stale | — |
| Browser không mở | FlowClient config sai | `flow/client.py`, check `FLOW_REAL_CHROME` env | stderr |
| Chrome launch lỗi profile lock | Chrome cũ chưa close | `flow/client.py` profile clone logic | — |
| Không login được | AIgglog chưa cache cookies | `flow/login.py`, profile dir | login log |
| Homepage không load | Network issue / redirect | `flow/operations/generate.py:51` | page console |
| Submit không nhận | 4 signals đều miss | `flow/submit.py` | `client._calls` dump |
| Progress stuck 0% | Gen không start | `flow/wait.py` DOM observer | injected JS log |
| Progress stuck > 0% nhưng không xong | Flow server slow / stuck | `flow/wait.py` no-signal timeout | — |
| Video không tải | 4-tier fallback fail hết | `flow/download.py` | network capture |
| Media_id wrong sau op | URL parsing sai | `flow/media_id.py:normalize_media_id` | URL log |
| Wrong account | Profile pinning break (INV-1) | `server/db/job_store.py:claim_next_job:231` | claim log |
| Project 404 | profile không match account của project | `job.profile` vs parent.profile | — |
| reCAPTCHA | Google flag account | `flow/recaptcha.py` — manual solve | screen |
| Model selector đóng sớm | Dùng Escape thay vì click-outside | `flow/model_selector.py` | — |
| Credits > 0 khi submit | Không phải LP model | `flow/model_selector.py:238` footer check | — |
| Job completed nhưng output_files=[] | Download 4 tier fail | `flow/download.py` | download log |
| UI không update realtime | WebSocket disconnect | `frontend/js/ws.js` reconnect logic | browser console |
| Port connection refused | B7 mismatch | `server/config.py` vs `worker/main.py` | — |

## E.2 — Quick commands

### Start dev local
```cmd
cd D:\AI\FlowEngine
scripts\start_all.cmd
```

### Check server health
```bash
curl http://localhost:8080/health
```

### Check job counts
```bash
curl http://localhost:8080/api/jobs/counts
```

### Recover stale jobs
```bash
curl -X POST http://localhost:8080/api/jobs/recover
```

### Check worker logs
```bash
tail -f worker_out.log
tail -f worker_err.log
```

### Dump DB
```bash
sqlite3 data/flowengine.db ".schema"
sqlite3 data/flowengine.db "SELECT id, type, status, profile FROM jobs ORDER BY created_at DESC LIMIT 20;"
```

### Kill stuck Chrome (Windows)
```cmd
taskkill /F /IM chrome.exe
```

---

# PHẦN F — GLOSSARY

| Term | Định nghĩa |
|---|---|
| **FlowEngine** | Hệ thống này — browser automation cho Google Flow |
| **Google Flow** | Công cụ AI video của Google (labs.google/fx/tools/flow) |
| **Profile** | Chrome user-data directory, 1-1 map với 1 Google account |
| **Project** | Flow project — canvas chứa nhiều media item |
| **Media item / media_id** | 1 video (hoặc image) trong project, có UUID riêng |
| **edit_url** | `{project_url}/edit/{media_id}` — URL đi thẳng đến 1 video |
| **L1 (Level 1)** | Job standalone — text-to-video (tạo project mới) |
| **L2 (Level 2+)** | Job phụ thuộc parent — extend/insert/remove/camera |
| **Chain** | Chuỗi N job liên tiếp chia sẻ cùng chain_id, cùng profile, cùng project |
| **LP model** | "Lower Priority" — Veo 3.1 Fast/Lite với 0 credits |
| **Bbox** | Bounding box `{x,y,w,h}` normalized 0-1, dùng cho insert/remove |
| **Direction** | Tên preset camera (e.g. "Dolly in", "Orbit left") |
| **ULTRA tier** | Google Flow tier có nhiều LP slots |
| **AIgglog** | External tool auto-login Google account vào Chrome profile |
| **CDP** | Chrome DevTools Protocol — launch mode dùng Chrome real (Windows) |
| **Playwright persistent** | Launch mode dùng Chromium embedded (Docker) |
| **INV-x** | Invariant (bất biến) — rule tuyệt đối không được vi phạm |
| **R-xxx-y** | Rule cụ thể theo category (CODE / GIT / TEST / CC) |
| **B1-B9** | 9 bugs hiện tại cần fix trong Phase A (xem §D.4) |
| **Bug #2-#8** | 7 bugs cũ đã fix và merged (xem CLAUDE.md §6) |
| **Phase A** | Fix 9 gaps B1-B9 |
| **Phase B** | Viết test coverage |
| **Phase C** | Manual E2E smoke test |

---

## G — External references

### G.1 — flowkit (crisng95/flowkit)
URL: https://github.com/crisng95/flowkit
License: MIT
Approach: Chrome Extension (MV3) + Python agent (WebSocket bridge) — thao tác Google Flow ở **API level** thay vì UI level.

**Đọc ngày:** 2026-04-17

**Value cho FlowEngine:**
- ✅ Xác minh **LP model internal key** (§D.1.13) — giúp `_select_lp_model()` verify bằng network intercept.
- ✅ Cung cấp pattern **tier detection qua `/v1/credits`** (§D.2.7) — dùng cho LP model routing post-Phase-A.
- ✅ Cung cấp pattern **fresh URL refresh qua TRPC intercept** (§D.2.8) — fallback khi signed URL hết hạn.
- ✅ Xác nhận rằng FlowEngine UI automation là **con đường duy nhất** cho insert/remove/camera (flowkit không support 3 op này — chỉ Google Flow UI mới có).

**KHÔNG áp dụng:**
- ❌ Không thay thế được Playwright approach của ta (4/5 operations là UI-only).
- ❌ flowkit's chain (start+end frame) ≠ FlowEngine extend (§D.1.14) — semantics khác, media_id khác.
- ❌ Pipeline cao hơn của flowkit (TTS, YouTube, Suno music) ngoài scope FlowEngine.

**Key files đã đọc reference:**
- `agent/services/flow_client.py` — full API client với endpoint map
- `agent/models.json` — model key catalog (Veo 3.1 tiers)
- `extension/background.js` — token capture + API proxy pattern
- `extension/injected.js` — grecaptcha solver + TRPC intercept
- `agent/services/scene_chain.py` — chain semantics (different from ours)
- `agent/worker/processor.py` — rate limit + polling pattern
- `agent/sdk/services/operations.py` — r2v reference image logic

---

## ĐỌC TIẾP

- `docs/DESIGN.md` — high-level design summary
- `docs/FLOW_UI_REFERENCE.md` — UI labels + DOM selectors (VI + EN)
- `docs/FLOW_PIPELINE_KNOWLEDGE.md` — technical pipeline reference
- `docs/FLOW_MULTILEVEL_JOBS.md` — multi-level design + live test results
- `CLAUDE.md` — Claude Code context + bug history
- `PLAN.md` — historical planning (Phase 1-5 checklist)

## KHI NÀO UPDATE FILE NÀY

1. Khi đóng 1 bug B1-B9 → update §D.4 (strike-through + commit hash)
2. Khi thêm rule mới → update §A
3. Khi đổi pipeline → update §B
4. Khi đổi API contract → update §C
5. Khi phát hiện gotcha mới → update §D
6. Khi đọc/tham khảo dự án mở khác → update §G

**Mọi thay đổi file này = commit riêng với message `docs(spec): ...`**
