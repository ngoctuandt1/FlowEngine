# FlowEngine — Bản Thiết Kế Hoàn Chỉnh

> Created: 2026-04-17
> Purpose: Master design document. Dùng làm kim chỉ nam cho mọi công việc tiếp theo (test, fix, feature mới).
> Phương pháp: 1 (Mục đích) + 2 (Có gì) + 3 (Làm được gì) → Thiết kế → 4 (Test + Fix).

---

## PHẦN 1 — MỤC ĐÍCH DỰ ÁN

### 1.1 Problem Statement

Google Flow (`labs.google/fx/tools/flow`) là công cụ tạo video AI (Veo 3.1) của Google nhưng:
- **Không có API công khai** — chỉ có UI web
- **Ràng buộc theo account** — project tạo trên account A chỉ account A truy cập được
- **Tốn credits** — mô hình bình thường tốn 5 credits/video; chỉ LP (Lower Priority) mới 0 credits
- **Thao tác thủ công tốn thời gian** — 1 video thành phẩm có thể cần 4-5 bước (generate → extend → insert → remove → camera)

**FlowEngine giải bài toán này bằng cách:**
1. Tự động hoá Google Flow UI bằng Playwright (giả lập user thật trên Chrome)
2. Quản lý nhiều Google account song song (mỗi profile Chrome = 1 account)
3. Cho phép xâu chuỗi (chain) nhiều bước thành 1 pipeline — tất cả các bước tự động trên cùng account
4. Ưu tiên dùng LP model để tạo video miễn phí
5. Giao diện web để user compose pipeline mà không cần viết code

### 1.2 Users & Use Cases

**User chính:** người cần sản xuất video AI số lượng lớn với chi phí thấp.

**Use cases:**
- **Single shot:** text-to-video đơn lẻ từ prompt
- **Multi-step production:** text-to-video → extend scene → insert object → camera move (4 bước, tự động, cùng account)
- **Batch farming:** chạy N job song song trên N Google account khác nhau
- **Recovery:** job fail giữa chừng có thể restart từ bước đúng

### 1.3 Key Invariants (Bất biến bắt buộc)

1. **Account binding** — mọi job trong 1 chain phải chạy trên CÙNG 1 Chrome profile (= cùng Google account)
2. **Navigate by `/edit/{media_id}`** — không bao giờ target video bằng vị trí DOM card
3. **Store everything** — sau mỗi operation phải lưu: `project_url`, `media_id`, `profile`, `generation_id`, `output_files`
4. **Serial per project** — 2 job trên cùng `project_url` KHÔNG chạy song song
5. **`media_id` re-extract per op** — extend/insert/remove preserve media_id (Flow in-place); camera-move mints NEW uuid (Tier 2 Run 10 2026-04-19). Engine re-extract post-op via `finalize_operation`; chain propagates parent's FINAL media_id qua B22 claim-time inherit. Mỗi op vẫn thêm 1 history entry. Xem SPEC §A.1 INV-5.

### 1.4 Non-Goals

- Không tự host Google account / không giải captcha tự động (user tự đăng nhập lần đầu)
- Không edit video sau khi download (video đầu ra = Flow xuất ra)
- Không tự động trả phí credit (chỉ dùng LP free-tier)
- Không cạnh tranh với các video editor chuyên nghiệp

---

## PHẦN 2 — CHÚNG TA CÓ GÌ (Current State Inventory)

### 2.1 Kiến trúc thực tế (verified by code audit)

```
┌─────────────────────────────────────────────────┐
│ frontend/   — Vanilla JS SPA (5 pages hoạt động)│
└──────────────────┬──────────────────────────────┘
                   │ REST + WS
┌──────────────────┴──────────────────────────────┐
│ server/     — FastAPI + SQLite (18 endpoints)   │
└──────────────────┬──────────────────────────────┘
                   │ HTTP claim/update
┌──────────────────┴──────────────────────────────┐
│ worker/     — Poll loop + dispatcher            │
└──────────────────┬──────────────────────────────┘
                   │ Playwright
┌──────────────────┴──────────────────────────────┐
│ flow/       — FlowClient + 5 operations         │
└─────────────────────────────────────────────────┘
```

### 2.2 Server Layer — **~90% hoàn chỉnh**

**File chính:**
- `server/app.py` — FastAPI app, lifespan, CORS, mount frontend static
- `server/config.py` — env vars loading
- `server/routes/jobs.py` — 8 endpoints (create, list, get, children, delete, counts, recover, chains)
- `server/routes/worker.py` — 4 endpoints (claim, update, heartbeat, list workers)
- `server/routes/profiles.py` — 5 endpoints (list, create, get, update, jobs)
- `server/routes/ws.py` — WebSocket hub `/ws/jobs`
- `server/db/database.py` — SQLite schema init
- `server/db/job_store.py` — CRUD + `claim_next_job()` atomic với `BEGIN IMMEDIATE`
- `server/db/profile_store.py` — Profile CRUD
- `server/models/job.py`, `server/models/profile.py` — Pydantic models

**Database schema:**
- `jobs` (60+ columns) — ✅ dùng đầy đủ
- `profiles` (8 columns) — ✅ dùng đầy đủ
- `chains` (7 columns) — ⚠️ **đã tạo nhưng KHÔNG được insert** (chain context chỉ lưu trong `job.chain_id`)

**Claim algorithm (job_store.py:182-296)** — ✅ đúng design:
- Priority 1: L2+ jobs where parent completed + parent.profile in worker.profiles + no lock on project_url
- Priority 2: L1 jobs any available profile
- Atomic transaction ngăn 2 worker claim cùng 1 job

### 2.3 Worker Layer — **~95% hoàn chỉnh**

**File chính:**
- `worker/main.py` — poll loop (POLL_INTERVAL_SEC=5), claim loop
- `worker/dispatcher.py` — routes job.type → `run_generate/extend/insert/remove/camera`
- `worker/profile_manager.py` — tracks available/busy profiles
- `worker/project_lock.py` — in-memory lock per project_url
- `worker/remote_api.py` — HTTP client → server

**Dead code phát hiện:**
- `ProfileManager.get_current_job()` — không ai gọi
- `ProjectLock.get_lock_holder()` — không ai gọi
- `RemoteAPI.list_profiles()` — không ai gọi
- `_kill_chrome_for_profile()` dùng `wmic` Windows-only — fail silent trên Linux

### 2.4 Flow Layer — **~85% hoàn chỉnh**

**File chính:**
- `flow/client.py` (20KB) — `FlowClient` với 2 launch modes: CDP (real Chrome, Windows) + Playwright persistent (Docker). Profile cloning để tránh lock conflict. Passive network hooks (captures video URLs, API calls, media IDs, generation IDs).
- `flow/operations/generate.py` — text-to-video
- `flow/operations/extend.py` — extend-video (✅ nav by `/edit/{media_id}`)
- `flow/operations/insert.py` — insert-object + bbox drawing
- `flow/operations/remove.py` — remove-object + bbox drawing
- `flow/operations/camera.py` — camera-move với preset picker
- `flow/operations/_base.py` — `navigate_to_edit()`, `finalize_operation()`, tile click fallback
- `flow/submit.py` — 6 selector patterns + Ctrl+Enter fallback; 4 confirmation signals
- `flow/wait.py` — 3 parallel detection methods (reverse API + network + DOM observer)
- `flow/download.py` — 4-tier fallback (API 1080p → API 720p → UI right-click → blob)
- `flow/model_selector.py` (24KB) — LP model pick, click-to-close (bug #8)
- `flow/media_id.py` — extract từ URL / network / DOM
- `flow/recaptcha.py` — detect iframe/text/network; 120s manual-solve wait
- `flow/login.py` — AIgglog integration cho auto-login
- `flow/account.py`, `flow/navigation.py`, `flow/retry.py`

**Stubs/incomplete:**
- `_set_aspect_ratio()` trong generate.py — chỉ log rồi return (không thực sự set)
- Bbox drawing không verify đã vẽ đúng canvas
- Camera preset không verify preset đúng được chọn
- `retry.py` có `@retryable` decorator nhưng không ai dùng

### 2.5 Frontend Layer — **~85% hoàn chỉnh**

**Pages (tất cả hoạt động):**
- `dashboard.js` — job counts + recent jobs + WS realtime + "Recover Stale" button
- `create-job.js` — form động cho 5 job types
- `chain-builder.js` — visual step sequencer (step 1 phải là t2v)
- `profiles.js` — CRUD profiles + quarantine/activate
- `settings.js` — server health + admin actions

**API client (`api.js`):** tất cả endpoints match server routes.
**WebSocket (`ws.js`):** subscribe `job_created/updated/completed/failed/deleted`, auto-reconnect exponential backoff.

**Missing:**
- `components/` folder tồn tại nhưng **trống** — pages dùng inline templates
- CSS 22KB nhưng audit báo "chỉ có 11 rules" — cần kiểm tra lại

### 2.6 Tests — **~0% hoàn chỉnh** ⚠️

`tests/__init__.py` rỗng. KHÔNG có test nào cả.
PLAN.md §4 liệt kê 4 test files cần có: `test_job_store.py`, `test_chain_logic.py`, `test_profile_pinning.py`, `test_api.py`.

### 2.7 Deployment — **~90% hoàn chỉnh**

- `docker-compose.yml` — server + worker
- `Dockerfile.server`, `Dockerfile.worker` — Python 3.11-slim
- `scripts/setup.cmd`, `start_all.cmd`, `start_server.cmd`, `start_worker.cmd` — hoạt động
- `.env.example`, `requirements.txt` — có
- Worker logs (`worker_out.log`, `worker_err.log`) cho thấy đã chạy được end-to-end (generate job thành công sau khi AIgglog login)

### 2.8 7 Bugs đã fix và merged to master

| Bug | Branch | Verified |
|---|---|---|
| #2 store media_id after ops | `claude/bug-2-store-media-id` | ✅ `finalize_operation` in _base.py |
| #3 L2 store project_url back | `claude/bug-3-store-project-url` | ✅ dispatcher attaches to result |
| #4 profile pinning | `claude/bug-4-profile-pinning` | ✅ `claim_next_job` line 231 |
| #5 nav by edit_url | `claude/bug-5-nav-media-id` | ✅ `navigate_to_edit` in _base.py |
| #6 camera-move handler | `claude/bug-6-camera-move` | ✅ `run_camera()` + dispatcher routing |
| #7 project lock | `claude/bug-7-project-lock` | ✅ `ProjectLock` + claim check |
| #8 LP credit leak | `claude/bug-8-lp-credit-leak` | ✅ click-to-close in model_selector.py |

---

## PHẦN 3 — CHÚNG TA LÀM ĐƯỢC GÌ (Capabilities)

### 3.1 End-to-End Flows HOẠT ĐỘNG

✅ **F1 — Single text-to-video job:**
User → web UI → Create Job → Dashboard hiện job pending → Worker claim → Chrome launch với profile → navigate Flow homepage → click New Project → type prompt → pick LP model → submit → wait completion → download 1080p → update job status completed → WS push lên UI.

✅ **F2 — Multi-level chain:**
User → Chain Builder → thêm step t2v + extend + insert + camera → submit → server tạo N jobs liên kết parent_job_id, cùng chain_id → worker claim L1 → xong → worker claim L2 với cùng profile → navigate `/edit/{media_id}` → click Extend/Insert/... → submit → download → chain tiếp tục.

✅ **F3 — Profile-aware claim:**
Worker A (profile=alpha) và Worker B (profile=beta). Chain bắt đầu trên alpha. L2 jobs PIN vào alpha — B không bao giờ claim được. A xong L1 → A claim L2 tiếp.

✅ **F4 — Project locking:**
2 L2 jobs cùng `project_url` → chỉ 1 claim được tại 1 thời điểm.

✅ **F5 — Real-time dashboard:**
WebSocket push mọi job state change lên tất cả client đang mở.

✅ **F6 — Stale recovery:**
Job stuck claimed/running > 30 phút → Settings → Recover Stale → reset về pending.

✅ **F7 — Auto-login fallback:**
Nếu profile chưa login → trigger AIgglog → login xong → restart job.

### 3.2 End-to-End Flows CHƯA HOẠT ĐỘNG / RỦI RO

❌ **B1 — Aspect ratio không set thực sự** — `_set_aspect_ratio()` chỉ log, Flow sẽ dùng default của UI (thường là 16:9).

⚠️ **B2 — Bbox không verify** — insert/remove có thể "nghĩ" đã vẽ bbox nhưng thực tế drag ra ngoài canvas → Flow dùng bbox mặc định.

⚠️ **B3 — Camera preset không verify** — click preset nhưng không check active state → có thể click trật.

⚠️ **B4 — Chain table không dùng** — chain metadata (status, progress) không được tracking ở chain level, chỉ có thể derive bằng query job.chain_id. Không chặn end-to-end nhưng UX chain view bị hạn chế.

⚠️ **B5 — completed_at không set** — cột tồn tại trong DB nhưng không ai update. Ảnh hưởng đến "Recover Stale" logic (dùng claimed_at thay thế).

⚠️ **B6 — Profile.current_job_id không reset** — sau khi job complete, `current_job_id` vẫn giữ giá trị cũ → UI profiles hiển thị sai.

⚠️ **B7 — Port default mismatch** — `server/config.py` default 8000, `worker/main.py` default 8080. Docker override bằng env var nên OK, nhưng chạy local không set SERVER_URL sẽ lỗi.

⚠️ **B8 — datetime.utcnow() deprecated** — Python 3.12+ sẽ warning, 3.13 có thể bỏ. Nên migrate sang `datetime.now(datetime.UTC)`.

❓ **B9 — Zero test coverage** — không biết regression xảy ra khi nào.

### 3.3 Capabilities Matrix

| Feature | Designed | Implemented | Tested | Production-ready |
|---|---|---|---|---|
| text-to-video | ✅ | ✅ | ❌ | ⚠️ manual-tested only |
| extend-video | ✅ | ✅ | ❌ | ⚠️ |
| insert-object | ✅ | ✅ | ❌ | ⚠️ bbox unverified |
| remove-object | ✅ | ✅ | ❌ | ⚠️ bbox unverified |
| camera-move | ✅ | ✅ | ❌ | ⚠️ preset unverified |
| Multi-level chain | ✅ | ✅ | ❌ | ⚠️ |
| Profile pinning | ✅ | ✅ | ❌ | ✅ (code review đúng) |
| Project locking | ✅ | ✅ | ❌ | ✅ |
| Real-time WS | ✅ | ✅ | ❌ | ✅ |
| Auto-login | — | ✅ (AIgglog) | ❌ | ⚠️ |
| Aspect ratio | ✅ | ❌ stub | — | ❌ |
| Docker deploy | ✅ | ✅ | ❌ | ⚠️ untested prod |
| Unit tests | ✅ | ❌ | — | ❌ |

---

## PHẦN 4 — THIẾT KẾ HOÀN CHỈNH (Locked-in Design)

### 4.1 Quyết định: KHÔNG restructure kiến trúc

Kiến trúc 4 tầng (frontend / server / worker / flow) **đã đúng và hoạt động được**. Không viết lại.

Lý do:
- 7 bugs đã fix cho thấy team hiểu đúng kiến trúc
- Logs cho thấy end-to-end chạy được (generate job succeeded)
- Database schema + claim algorithm + WS hub đều đúng pattern
- Restructure = tạo regression mới

### 4.2 Hoàn thiện = Đóng 9 gaps (B1-B9) + viết test

**Không thêm feature mới** cho đến khi B1-B9 đóng hết và test pass.

### 4.3 Target State (sau khi đóng gaps)

```
Capabilities Matrix (target):

Feature              | Implemented | Tested | Production |
---------------------+-------------+--------+------------+
text-to-video        |      ✅     |   ✅   |     ✅     |
extend-video         |      ✅     |   ✅   |     ✅     |
insert-object        |      ✅     |   ✅   |     ✅     |
remove-object        |      ✅     |   ✅   |     ✅     |
camera-move          |      ✅     |   ✅   |     ✅     |
Multi-level chain    |      ✅     |   ✅   |     ✅     |
Aspect ratio         |      ✅     |   ✅   |     ✅     |
Bbox verified        |      ✅     |   ✅   |     ✅     |
Port unified         |      ✅     |   ✅   |     ✅     |
```

### 4.4 Không-làm-nữa list (debt to ignore for now)

- `chains` table (B4) — để đấy, derive từ `job.chain_id` đủ dùng
- Dead code (ProfileManager.get_current_job etc.) — để đấy, không hại
- `components/` folder trống — để đấy, pages inline template ổn
- Chrome kill trên Linux — để đấy, Docker không cần kill

Mục đích: **tập trung vào correctness, không chạy theo code hygiene.**

---

## PHẦN 5 — TEST + FIX PLAN

### 5.1 Phase A: Fix 9 gaps (B1-B9) theo priority

**P0 — Blocker cho production:**
1. **B7 — Port mismatch** (2h) — chuẩn hoá PORT=8080 ở server/config.py; update PLAN.md
2. **B1 — Aspect ratio** (4h) — implement thực sự `_set_aspect_ratio()` trong generate.py
3. **B2, B3 — Bbox + preset verify** (6h) — thêm assertion sau khi drag/click

**P1 — Quan trọng:**
4. **B5 — completed_at** (1h) — set trong `update_job()` khi status='completed'
5. **B6 — Profile.current_job_id reset** (1h) — reset trong worker sau khi job done
6. **B8 — datetime.utcnow** (2h) — sed replace toàn bộ

**P2 — Nice to have:**
7. **B4 — chains table** — defer (không ảnh hưởng correctness)

### 5.2 Phase B: Viết test coverage (B9)

**Priority test files (theo PLAN.md §4 + audit findings):**

1. `tests/test_job_store.py` — SQLite CRUD + `claim_next_job` atomic
   - Test: create, update, get_children, list with filters
   - Test: claim priority L2+ trước L1
   - Test: claim pin profile đúng parent
   - Test: claim block project_url đã locked
   - Test: concurrent claim không cấp 2 worker cùng job
   
2. `tests/test_chain_logic.py` — Chain creation + inheritance
   - Test: POST /api/chains tạo N jobs với parent_job_id đúng
   - Test: job_level tăng dần 1→2→3
   - Test: chain_id share giữa các job
   - Test: L2 inherit profile từ L1 sau khi L1 xong
   
3. `tests/test_profile_pinning.py` — Profile pinning correctness
   - Test: L2 job không claim được nếu worker không có profile phù hợp
   - Test: L1 job claim được any profile
   - Test: quarantined profile không claim được
   
4. `tests/test_api.py` — HTTP endpoints smoke test
   - Test: POST /api/jobs create + GET lại
   - Test: POST /api/worker/claim trả job đúng
   - Test: PUT /api/worker/jobs/{id} update fields
   - Test: WS /ws/jobs nhận event khi job update
   
5. `tests/test_navigation.py` (mới) — `edit_url()` helper + media_id extraction
   - Test: extract UUID từ URL `/edit/{uuid}`
   - Test: normalize `_upsampled`, `_720p` suffixes
   
6. `tests/test_e2e.py` (tuỳ chọn, mock Flow) — chain 4 bước với Playwright mock

**Target coverage:** ≥ 70% cho server + worker (flow/ skip vì cần browser thật)

### 5.3 Phase C: Manual E2E smoke test

Sau khi Phase A + B xong, chạy thử:
1. Clean DB → start server + worker
2. Tạo chain: t2v "golden sunset" → extend "zoom out" → insert "seagulls" bbox → camera "dolly in"
3. Verify 4 video files trong `downloads/`
4. Verify history panel trên Flow UI có 4 entries
5. Verify tất cả cùng media_id trong URL
6. Verify tất cả jobs completed trong dashboard

### 5.4 Success Criteria

Dự án coi là "done" (có thể chuyển sang feature mới) khi:
- [ ] 9 gaps (B1-B9) đóng hết (trừ B4 defer)
- [ ] Test coverage ≥ 70% trên server/worker
- [ ] 1 manual E2E chain 4-bước thành công
- [ ] Docker compose up chạy được clean
- [ ] README.md có hướng dẫn quick start
- [ ] CLAUDE.md update với trạng thái mới

### 5.5 Non-goals trong phase này

- Không thêm job type mới
- Không refactor kiến trúc
- Không build UI mới
- Không optimize performance (chưa có baseline để đo)

---

## PHẦN 6 — DECISION LOG

| # | Quyết định | Lý do |
|---|---|---|
| D1 | Giữ nguyên kiến trúc 4-tầng | Đã verify hoạt động, 7 bugs cũ đã fix đúng |
| D2 | Không dùng `chains` table | Chain metadata derive từ job đủ dùng, giảm surface |
| D3 | LP model only (0 credits) | Tiết kiệm credits, user target là free tier |
| D4 | Vanilla JS frontend | Đơn giản, đã có, không cần framework |
| D5 | SQLite thay vì Postgres | 1 worker đủ dùng, không cần concurrent DB |
| D6 | Windows CDP + Docker Playwright | Tận dụng Chrome thật khi dev, Playwright khi deploy |
| D7 | Port unify = 8080 | Docker đã dùng 8080, worker default 8080 |
| D8 | Navigate by `/edit/{media_id}` only | Bug #5, verified stable qua live test 4-bước |

---

## PHẦN 7 — QUICK REFERENCE

### 7.1 Start local dev
```cmd
cd D:\AI\FlowEngine
scripts\start_all.cmd
```
Server: `http://localhost:8080` — UI + API

### 7.2 Key env vars
```env
SERVER_HOST=0.0.0.0
SERVER_PORT=8080
DATABASE_PATH=./data/flowengine.db
CHROME_USER_DATA_DIR=./profiles
SERVER_URL=http://localhost:8080
WORKER_PROFILES=profile1,profile2
```

### 7.3 Key files để debug
| Triệu chứng | File cần xem |
|---|---|
| Job không claim | `server/db/job_store.py:claim_next_job` |
| Job sai profile | `worker/dispatcher.py` + `server/db/job_store.py:231` |
| Browser không mở | `flow/client.py` (CDP vs Playwright mode) |
| Submit không nhận | `flow/submit.py` (4 confirmation signals) |
| Wait timeout | `flow/wait.py` (3 detection methods) |
| Download fail | `flow/download.py` (4-tier fallback) |
| Model chọn sai | `flow/model_selector.py` |
| reCAPTCHA | `flow/recaptcha.py` (120s manual wait) |

### 7.4 Key docs
- `docs/FLOW_UI_REFERENCE.md` — UI labels VI+EN + DOM selectors
- `docs/FLOW_PIPELINE_KNOWLEDGE.md` — technical pipeline reference
- `docs/FLOW_MULTILEVEL_JOBS.md` — multi-level design + live test
- `docs/DESIGN.md` — file này
- `CLAUDE.md` — context cho Claude Code
- `PLAN.md` — historical (planning phase)
