# Session Report — `B10` Pydantic default_factory=datetime.utcnow

---

## 1. Metadata

| Field | Value |
|---|---|
| Task ID | `B10` |
| Task type | bug-fix / refactor (P2 — deprecation cleanup, residual from B8) |
| Session started | 2026-04-18 |
| Session ended | 2026-04-18 |
| Duration actual | ~15m |
| Duration estimate | `15m` (SPEC §D.4 B10 + WORKPLAN §8 B10) |
| Worker | Claude Opus 4.7 (executor session, spawned by supervisor) |
| Branch | `claude/romantic-grothendieck-351942` (worktree) |
| Parallel sessions | B16 (flow/submit.py) + B17 (flow/model_selector.py) running in separate worktrees — B10 scope entirely disjoint |

---

## 2. Commits landed

```
<B10-COMMIT>  fix(models): migrate default_factory=datetime.utcnow to tz-aware (B10)
```

1 commit duy nhất (tuân §1.5 + §1.6: code + tests + SPEC + WORKPLAN + report trong cùng commit).
Hash thực = tip của branch sau commit. Placeholder `<B10-COMMIT>` trong SPEC.md §D.4 B10 +
WORKPLAN.md §8 B10 + metadata §2 báo cáo này sẽ được backfill bằng commit thứ hai
(precedent: B14 `72e056b` → `a4e9092`, B15 `caef3e9` → `d11500f`).

---

## 3. Files changed

```
server/models/job.py                                           +2 / -2    (import UTC; 2 lambda)
server/models/profile.py                                       +2 / -2    (import UTC; 1 lambda)
tests/test_datetime_migration.py                               +19 / -7   (extend scan + docstring)
docs/SPEC.md                                                   +4 / -2    (§D.4 B10 strike-through + resolution)
docs/WORKPLAN.md                                               +1 / -1    (§8 B10 strike-through)
docs/session-reports/2026-04-18_B10_pydantic-default-factory.md +~220/-0  (NEW — báo cáo session này)
```

Tổng: `6 files, ~+248 / -14 lines` (approx).

`server/models/_utils.py` được đề xuất trong whitelist (OPTIONAL) **không tạo** — xem §7 Q1
rationale.

---

## 4. Tests

Command chạy:
```
pytest tests/test_datetime_migration.py -v   # RED → GREEN proof (B10 extension)
pytest tests/ -v                              # full suite, no regression
pytest tests/ -W error::DeprecationWarning    # no utcnow warning leaks
python -W error::DeprecationWarning -c "..."  # live-factory trigger sanity check
```

### RED → GREEN proof (extended B10 test)

**Before** fix (test extended, production code chưa sửa):
```
tests/test_datetime_migration.py::test_no_utcnow_in_code FAILED
  AssertionError: datetime.utcnow is deprecated. Found in:
  ['server\\models\\job.py (default_factory)',
   'server\\models\\profile.py (default_factory)'].
tests/test_datetime_migration.py::test_utc_timestamps_have_timezone PASSED
========================= 1 failed, 1 passed in 0.63s =========================
```

**After** migrating 3 sites:
```
tests/test_datetime_migration.py::test_no_utcnow_in_code PASSED
tests/test_datetime_migration.py::test_utc_timestamps_have_timezone PASSED
============================== 2 passed in 0.12s ==============================
```

### Full-suite run

```
============================= test session starts =============================
platform win32 -- Python 3.13.5, pytest-9.0.2, pluggy-1.6.0
configfile: pytest.ini
plugins: anyio-4.9.0, langsmith-0.7.12, asyncio-1.3.0
collected 48 items
...
============================= 48 passed in 6.23s ==============================
```

### DeprecationWarning-as-error run

```
pytest tests/ -W error::DeprecationWarning
tests\test_bbox.py ......                                                [ 33%]
tests\test_camera.py .......                                             [ 47%]
tests\test_config.py ..                                                  [ 52%]
tests\test_datetime_migration.py ..                                      [ 56%]
tests\test_extend.py ............                                        [ 81%]
tests\test_job_store.py ....                                             [ 89%]
tests\test_profile_store.py ...                                          [ 95%]
tests\test_smoke.py ..                                                   [100%]
============================= 48 passed in 6.15s ==============================
```

Zero DeprecationWarning leaks. Pre-B10 already clean vì default factory chưa bị trigger
trong test suite (no test instantiates `Job`/`Profile` without timestamps — xem B8 report
§4 note). Post-B10 còn clean **hơn** về ngữ nghĩa: nếu Phase B thêm test omit timestamps,
factory sẽ KHÔNG emit warning.

### Live-factory trigger sanity check

```
$ python -W error::DeprecationWarning -c "
from server.models.job import Job, JobType
from server.models.profile import Profile
j = Job(type=JobType.TEXT_TO_VIDEO)
p = Profile(name='test')
print('Job.created_at:', j.created_at, 'tzinfo:', j.created_at.tzinfo)
print('Job.updated_at:', j.updated_at, 'tzinfo:', j.updated_at.tzinfo)
print('Profile.created_at:', p.created_at, 'tzinfo:', p.created_at.tzinfo)
print('OK: no DeprecationWarning raised')
"
Job.created_at: 2026-04-18 03:19:27.351597+00:00 tzinfo: UTC
Job.updated_at: 2026-04-18 03:19:27.351600+00:00 tzinfo: UTC
Profile.created_at: 2026-04-18 03:19:27.351612+00:00 tzinfo: UTC
OK: no DeprecationWarning raised
```

Đây là **proof khó thay thế**: instantiate không có explicit timestamp → factory
bị gọi → datetime returned có `tzinfo=UTC` (tz-aware, không naive) → `-W error`
không raise → deprecation gone end-to-end. Mô phỏng exactly
`server/routes/jobs.py:_build_job` runtime behavior (POST `/api/jobs`).

| Test | Result | Notes |
|---|---|---|
| `test_datetime_migration.py::test_no_utcnow_in_code` | ✅ pass | B10 — extended scan catches reference-form |
| `test_datetime_migration.py::test_utc_timestamps_have_timezone` | ✅ pass | B8 — tz round-trip, unchanged |
| `test_bbox.py` (6) | ✅ pass | B11 regression intact |
| `test_camera.py` (7) | ✅ pass | B12 regression intact |
| `test_config.py` (2) | ✅ pass | B7 regression intact |
| `test_extend.py` (12) | ✅ pass | B15 regression intact |
| `test_job_store.py` (4) | ✅ pass | B5 regression intact |
| `test_profile_store.py` (3) | ✅ pass | B6 regression intact |
| `test_smoke.py` (2) | ✅ pass | B9 fixture regression intact |

- Tổng: `48 pass / 0 fail / 0 skipped`.
- Coverage delta: không đo.

---

## 5. SPEC.md + WORKPLAN.md update

- [x] `docs/SPEC.md §D.4 B10` — strike-through (`~~...~~`) + commit hash placeholder
  `<B10-COMMIT>` + resolution block (fix approach, test extension, rationale pointer).
- [x] `docs/WORKPLAN.md §8 B10` — strike-through + `<B10-COMMIT>` placeholder + 1-line
  resolution summary.
- [ ] `docs/DESIGN.md` — N/A (B10 không chạm architectural decisions).

Commit hash cho các file này: cùng commit với code fix (theo rule §1.5). Backfill commit
sẽ replace placeholder `<B10-COMMIT>` → real hash sau khi primary commit landed.

---

## 6. Invariants & rules verified

- [x] INV-1 Account Binding — **N/A** (không chạm profile-claim / chain logic)
- [x] INV-2 Navigate by `edit_url` — **N/A** (không chạm flow/navigation)
- [x] INV-3 Store Everything — **N/A** (không chạm worker update_job)
- [x] INV-4 Serial per Project — **N/A** (không chạm project_lock)
- [x] INV-5 media_id stable — **N/A** (không chạm media_id logic)
- [x] R-CODE-3 Locale-Independent — **N/A** (không có UI selector)
- [x] **R-CODE-10 No `datetime.utcnow()`** — ✅ đây là rule B10 enforce. Phần call-form
  đã do B8 close (7/7 sites); phần reference-form (3 sites trong `default_factory=`)
  nay do B10 close. Scan test giờ catch **cả hai pattern** — trip-wire chống regression
  cho cả B8 lẫn B10.
- [x] R-CC-1 KHÔNG restructure — chỉ sửa `from` import + 3 default_factory expression;
  zero API / schema / persistence / behavior change (factory output type = `datetime`
  trước & sau; chỉ timezone aware vs naive).
- [x] File whitelist tuân thủ — diff scope khớp 6 file trong whitelist:
  `server/models/job.py`, `server/models/profile.py`, `tests/test_datetime_migration.py`,
  `docs/SPEC.md`, `docs/WORKPLAN.md`, `docs/session-reports/2026-04-18_B10_*.md`. Zero
  touch vào `flow/*` (B16/B17 parallel), `server/db/*` + `server/routes/*` (B8 domain),
  `stash@{0}` (untouched), `.claude/*`, `docs/DESIGN.md`.

---

## 7. Issues / Decisions

### Vấn đề phát sinh

Không có blocker. Task pattern cực đơn giản (3 × one-line substitution + 2 import).
B8 report §7 đã telegraph toàn bộ fix — B10 thuần cherry-pick-forward.

Một observation: B8 test scan dùng literal `"datetime.utcnow()"` (có parens) vì ở thời
điểm đó guardrail cho call-form là đủ (4 production files, 7 call sites). Pattern
`default_factory=datetime.utcnow` (không parens) lọt qua filter vì đó là function
**reference**, không phải call. B10 test giờ check **cả hai substring** trong cùng scan
loop — không tách thành test riêng vì:
1. Shared dir traversal (đơn giản hơn).
2. Cùng root cause (naive-UTC deprecation), cùng rule (R-CODE-10), không cần rule mới.
3. Offense message ghi rõ `(default_factory)` suffix → dev debug nhanh.

### Quyết định đã đưa (judgment calls)

#### Q1: Choice 1 (inline lambda) vs Choice 2 (shared `_now_utc` helper in `_utils.py`)?

**Choice 1 (inline lambda).** Lý do:

1. **Chỉ 3 sites trong 2 module.** Tạo `server/models/_utils.py` + `from server.models._utils import _now_utc` ở 2 file = thêm 2 import line + 1 file mới — lớn hơn bản thân fix (5 substitution lines). Ratio abstraction:overhead không justify.
2. **YAGNI.** SPEC §A.5 guidance "don't add abstractions beyond what the task requires. Three similar lines is better than a premature abstraction." 3 identical lambda còn dưới threshold.
3. **Symmetry với precedent.** `server/models/job.py:63` đã có `id: str = Field(default_factory=lambda: str(uuid.uuid4()))` — pattern lambda-in-Field là established convention trong cùng file. Extract `_now_utc` khi mà `_uuid4` vẫn inline = inconsistent.
4. **Phase B khả năng nhỏ.** Nếu Phase B thêm model (e.g. `Chain`, `GenerationLog`) dùng cùng default_factory, lúc đó extract là hợp lý (rule of three). Bây giờ tạo trước = đoán tương lai, và rule §A.5 là "three similar lines is better than a premature abstraction".
5. **Rollback cost cân bằng.** Nếu sau đó cần `_utils.py` thật, refactor `lambda: datetime.now(UTC)` → `_now_utc` là 1 sed pass — risk không tăng vì không đi.

Trade-off được cân nhắc nhưng bỏ: Choice 2 cleaner nếu `_utils.py` có **nhiều** helper khác (serialization, TZ conversion, …). Nhưng hiện tại không có nhu cầu đó.

#### Q2: Commit hash placeholder `<B10-COMMIT>`

Giữ placeholder, backfill bằng commit thứ hai (precedent: B14 `72e056b` → docs commit
`a4e9092`/`a2293bf`, B15 `caef3e9` → docs commit `d11500f`). Self-reference không fix
trong-commit vì mỗi `git commit --amend` sinh hash mới (loop vô hạn). Placeholder-form
tương thích với pattern đã thiết lập ở 2 session trước (B14, B15).

#### Q3: Test scan — substring match vs AST parse?

Giữ substring match (`if "default_factory=datetime.utcnow" in text`). Lý do:

- B8 đã chọn substring (`"datetime.utcnow()"`) cho call-form. Consistency.
- AST parse phức tạp hơn nhiều (walk `ast.Call` / `ast.Attribute`), và vẫn có thể bị đánh
  lừa nếu ai đó viết `default_factory = datetime.utcnow` với space — cực edge.
- False-positive risk: duy nhất nếu ai viết `default_factory=datetime.utcnow_something`
  (không tồn tại) hoặc comment có string literal "default_factory=datetime.utcnow" —
  cực thấp và có action clear (rename variable / strip comment).
- Pattern substring caught **đúng** cả 3 site Pydantic hiện tại (RED proof ở §4).

#### Q4: Scope strictness — có "tiện thể" rà reference-form khác?

Đã grep:
```
git grep "default_factory=datetime\.utcnow" -- server/ worker/ flow/
# → 3 matches (job.py:96, job.py:97, profile.py:25) — đúng spec
git grep "=datetime\.utcnow" -- server/ worker/ flow/
# → 3 matches giống trên (0 reference-form nào khác)
```

Không có reference-form khác ngoài `default_factory=`. Scope B10 khớp spec, không cần
mở rộng, không tạo thêm B-discovery.

### Bug candidates phát hiện NHƯNG KHÔNG fix (out of scope)

Không. B10 scope rất hẹp (3 lines) và grep toàn repo xác nhận không có residual pattern
nào khác. Quay lại khi có Phase B model mới.

---

## 8. Handoff notes

- **Workdir state cuối session**:
  - `git status` (trong scope B10 trước commit): 3 modified files + 3 docs/test touched.
    Sau commit: clean.
  - `git stash list` trước session: `stash@{0}: "WIP: flow refinements — direct edit-url nav, chip re-click model close, verify extend panel, iterate enabled submit buttons"`.
    **Vẫn còn nguyên** — không `stash pop`, không `stash drop`. Stash scope = `flow/*`
    (untouched bởi B10), safe.
  - `flow/*` zero changes — verify bằng `git diff --name-only master...HEAD` sẽ không
    chứa `flow/` — tương thích với B16 (flow/submit.py) + B17 (flow/model_selector.py)
    parallel sessions.
- **Env set**: không set gì mới. Python 3.13.5 + pytest 9.0.2 + pytest-asyncio 1.3 đã
  có từ B9 session.
- **Supervisor next step**: merge B10 commit → backfill `<B10-COMMIT>` → merge B16/B17
  khi parallel session xong. B10 không block B16/B17 (đã verify whitelist disjoint).

---

## 9. Done criteria checklist (từ supervisor prompt)

- [x] **3 sites migrated** — `server/models/job.py:96` + `:97` + `server/models/profile.py:25`
      (verify §3 diff + §4 RED→GREEN).
- [x] **Choice 1 or 2 documented §7** — Q1 chọn Choice 1 (inline lambda), 5 lý do.
- [x] **Extended test catches default_factory pattern** — §4 RED trace shows
      `['server\\models\\job.py (default_factory)', 'server\\models\\profile.py (default_factory)']`
      caught; GREEN after fix.
- [x] **Full suite pass, no regression** — 48 pass (§4), same as pre-B10 baseline.
- [x] **`-W error::DeprecationWarning` clean** — 48 pass (§4) + live factory trigger
      check (§4 sanity) confirms no warning from factory-triggered path.
- [x] **SPEC §D.4 B10 strike + WORKPLAN §8 B10 strike** — §5 done với `<B10-COMMIT>`
      placeholder.
- [x] **Zero diff ngoài whitelist** — `git diff --name-only` returns exactly
      `server/models/job.py`, `server/models/profile.py`, `tests/test_datetime_migration.py`
      (+ SPEC/WORKPLAN/report on commit). Zero `flow/*` / `server/db/*` / `server/routes/*`.
- [x] **Stash@{0} còn** — §8 confirmed `git stash list` unchanged.
- [x] **Report 9 section** — file này theo `_TEMPLATE.md` 9-section layout.

---

_Sign-off: ✅ Ready for supervisor review. B10 DONE — commit `<B10-COMMIT>`._
