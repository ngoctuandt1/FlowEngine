# Session Handoff — 2026-04-25

**Supervisor:** Claude (tech lead)
**Profile available:** `ngoctuandt20` (Ultra, English locale)
**Final master commit:** `5b3e508` (docs LOW-items live re-verify, PR #54)
**Test baseline:** `308 passed / 1 skipped / 3 xfailed`
**Open issues on GitHub:** 0

## TL;DR

Repo trunk sạch — tất cả PR-chuỗi của epic flow-bugs + #44/#45/#46/#48/#51/#52 đã merge & close.
Tuy nhiên có **3 gap** từ live-verify 2026-04-24 chưa được file thành issue, **1 retest còn nợ**, và **1 stale doc reference** cần cleanup.

| Gap | Loại | Mức ưu tiên | Đã có issue? |
|---|---|---|---|
| L1 "reuse existing project" (Finding 3) | Bug suspect | ~~HIGH~~ → **CLOSED-NO-REPRO 2026-04-25** | n/a — see [2026-04-25_finding3-no-repro.md](2026-04-25_finding3-no-repro.md) |
| #45/#46 cold-start race retest multi-profile | Verification debt | **BLOCKED** — chờ ≥1 Flow-eligible Google account thứ 2 (ngoctuandt20 hiện là duy nhất; s1324h1450 dead per `feedback_flow_service_not_allowed_account_dead.md`) | n/a |
| REM `Failed to find Remove button` single-shot | Flaky / 1-sample | LOW | ❌ chờ repro lần 2 |
| Stale ref `docs/session-reports/reviews/4_media_id_bug.md` | Doc cleanup | ✅ DONE — PR #55 (commit `a0ee2cf`) | n/a |
| LOW-items 2/5 chưa exercise (`done`-immediate, `failed`-retry, exhausted-fallback của image upscale) | Coverage gap | LOW | n/a (defensive paths) |

---

## 1. Gap chi tiết

### Gap 1 — L1 "reuse existing project" (Finding 3) 🔴

**Nguồn:** [2026-04-24_live_verify_post_45_44.md §Finding 3](2026-04-24_live_verify_post_45_44.md)

**Triệu chứng:** Job `63261fa9` là `text-to-video` với `parent_job_id=null` + `chain_id=null` (đúng nghĩa L1 mới),
nhưng kết quả lại `project_url` = project của job J2 (`…3cecf52a`) thay vì tạo project mới.
Output `t2v_720p_…` thay vì `t2v_1080p_…` của J2 — đường quality cũng khác bất thường.

**Hypothesis chưa loại trừ:**
- (a) `+ New project` click reopen project có sẵn vì lý do UI
- (b) Temp-profile clone mang theo residual editor state
- (c) J3 race với INS, đọc post-INS edit URL như kết quả của mình
- (d) Hai worker process song song (xem "Log coverage note" cùng report) — `MAX_CONCURRENT_JOBS=1` chỉ enforce per-process, không global.

**Action cho session sau:**
1. Kill toàn bộ worker cũ trước khi chạy (`Get-Process python | Where-Object {...}` filter đúng cmdline có `run_worker.py`).
2. Chạy 3 L1 t2v sequential trên cùng 1 profile, fresh worker, MAX_CONCURRENT_JOBS=1.
3. Nếu repro được → file issue với evidence. Nếu không → close-out trong report mới.

**Tham chiếu evidence:** `debug_screens/new_project_btn_missing_20260424_152313.png` (J1 fail screenshot — không phải J3, nhưng cùng run).

---

### Gap 2 — #45/#46 cold-start race chưa retest multi-profile 🔴 BLOCKED

**Nguồn:** [2026-04-24_live_verify_post_45_44.md §"PR #46 verdict — NOT EXERCISED"](2026-04-24_live_verify_post_45_44.md)

**Trạng thái 2026-04-25:** **BLOCKED** — không đủ Flow-eligible accounts để chạy multi-profile.

**Diễn biến trong session 2026-04-25 (sau 14:30):**
- `D:/AI/AI-Engine3-Project/profiles_ultra.txt` (path mặc định `flow.login._load_credentials` đọc) chỉ có 2 entries: `ngoctuandt20` + `s1324h1450`.
- `D:/AI/profiles_ultra.txt` có 14 entries nhưng định dạng/credentials không tương thích — TOTP secret base32 fail trên `thuytuongle15` (`Non-base32 digit found`); chưa kiểm tra hết.
- Warm `s1324h1450` thành công (cookies persisted, exit 0) nhưng landing trên `access.workspace.google.com/ServiceNotAllowed` với body "you do not have access to Flow" — account bị org admin disable, vĩnh viễn không dùng được.
- → Mới gotcha permanent: [feedback_flow_service_not_allowed_account_dead.md](../../../C:/Users/Tuan/.claude/projects/D--AI-FlowEngine/memory/feedback_flow_service_not_allowed_account_dead.md). Dù warm exit 0, phải kiểm URL cuối log; nếu chứa `ServiceNotAllowed` → drop khỏi pool.

**Repro gốc của #45 cần ≥4 profiles. Hiện chỉ có 1 (`ngoctuandt20`). Phase B không thể tiếp tục cho đến khi:**
- User bổ sung ≥1 (lý tưởng ≥3) Google account **không phải Workspace org-restricted** vào canonical credentials file `D:/AI/AI-Engine3-Project/profiles_ultra.txt`.
- Mỗi account: English locale (per `feedback_english_locale.md`), warm xong landing trên Gmail inbox (KHÔNG `ServiceNotAllowed`), TOTP secret base32-valid.

**Action khi có account mới:**
1. Pre-flight từng account qua `warm_profile.py <name>`. Đọc tail log: nếu `Login complete (url=...)` chứa `ServiceNotAllowed` → loại.
2. Cold-clean: kill all chromes có `chrome-profiles` trong cmdline + xóa `SingletonLock` còn sót.
3. Worker env: `WORKER_PROFILES=ngoctuandt20,<acc2>,<acc3>,<acc4>` `MAX_CONCURRENT_JOBS=4`.
4. Submit 4 L1 t2v cùng lúc. Search log cho `DOM media-id scrape recovered` / `Completion via DOM`.

**Cost khi unblock:** 4× t2v 1080p (không 4K).

---

### Gap 3 — REM `Failed to find Remove button` 🟢

**Nguồn:** [2026-04-24_live_verify_post_45_44.md §"REM failure"](2026-04-24_live_verify_post_45_44.md)

Single sample, low confidence. INS đã pass cùng project; có thể DOM mode-bar shift sau INS, hoặc UI glitch.
Chỉ file issue nếu repro lần 2.

**Action gắn vào Gap 2:** trong khi multi-profile run, xen 1 INS + 1 REM trên project của 1 profile để re-verify.

---

### Gap 4 — Stale doc reference 🟢

**File ma:** `docs/session-reports/reviews/4_media_id_bug.md` (directory `reviews/` không tồn tại).

**Reference còn sót:**
- [CLAUDE.md](../../CLAUDE.md) §6 — đoạn "Parked HIGH: L2 `media_id` extraction bug"
- [tests/test_extend.py](../../tests/test_extend.py) — comment ref
- [tests/test_camera_l2.py](../../tests/test_camera_l2.py) — comment ref
- [2026-04-24_live_verify_post_45_44.md:138](2026-04-24_live_verify_post_45_44.md) — "no update needed to `reviews/4_media_id_bug.md`"

Bug đã RESOLVED 2026-04-23 (commit `e79405d` đánh dấu trong PR #53). Tất cả ref nên:
- CLAUDE.md: cập nhật câu "Parked HIGH" → đã resolve, link sang [2026-04-23_l2-media-id-fix-live-verified.md](2026-04-23_l2-media-id-fix-live-verified.md).
- 2 test files: bỏ comment ref hoặc chuyển sang report mới.
- Report 04-24: leave as-is (historical record).

**Action:** 1 PR cleanup nhỏ, không ảnh hưởng code.

---

### Gap 5 — Image upscale defensive branches chưa exercise live 🟢

Theo [2026-04-25_low-items-live-reverify.md §"Branches still NOT live-exercised"](2026-04-25_low-items-live-reverify.md):
- `done`-state-immediately (cached) — không gặp trong 3/3 4K image runs.
- `failed`-state retry — không repro được lỗi natural.
- Exhausted-attempts → API original fallback.

Đều có unit test cover. Không cần action — note để biết coverage stops where.

---

## 2. State worktree

- Branch hiện tại: `claude/admiring-margulis-b44d27`
- Status: clean
- Worker process: KHÔNG có session này tự khởi (đây là review-only session). Nếu user còn worker PID 307377 từ 2026-04-24, nó không có #52 browser-pool / #49 window-geometry.

## 3. Recommended next session

**Phương án A — High-value, ~30 phút:** Tackle Gap 1 (Finding 3 repro). Nếu repro → file issue chi tiết; nếu không → close-out report.

**Phương án B — Hoàn tất debt, ~45 phút:** Gap 2 (multi-profile cold-start) + xen Gap 3 (REM re-verify) trong cùng run.

**Phương án C — Cleanup, ~10 phút:** Gap 4 doc cleanup PR. Có thể delegate cho Codex (self-contained, không cần live verify).

Đề xuất thứ tự: **C → A → B**. C trước cho gọn ref; A có khả năng tìm bug thật; B tốn cost cao nhất nên để cuối, sau khi rõ Gap 1.

## 4. Locked / không chạm

- Tất cả memory entries trong [MEMORY.md](../../../C:/Users/Tuan/.claude/projects/D--AI-FlowEngine/memory/MEMORY.md) — đặc biệt locale, Chrome launch, marketing landing bypass logic, image upscale env-gating.
- INV-1..5 (CLAUDE.md §4 chain invariants).
- R-CODE-* (datetime.now(UTC), no VI/EN hardcoded selectors).

---

_Sign-off: review-only session, không sửa code. Sẵn sàng cho session kế tiếp._
