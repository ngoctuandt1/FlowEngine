# FlowEngine — Phase A Workplan (Bug Fix Plan)

> Created: 2026-04-17
> Purpose: Tactical work plan cho Phase A — fix 9 gaps (B1-B9). **MỌI CHI TIẾT phải có ở đây trước khi động code.**
> Prerequisite: `docs/SPEC.md` đã duyệt
> Execution: TUẦN TỰ theo thứ tự §2 — không song song, không đổi thứ tự

---

## MỤC LỤC

- [§1 — Nguyên tắc execution](#1--nguyên-tắc-execution)
- [§2 — Thứ tự sequential (bắt buộc)](#2--thứ-tự-sequential-bắt-buộc)
- [§3 — Chi tiết từng bug](#3--chi-tiết-từng-bug)
  - [B7 — Port mismatch](#b7--port-mismatch-p0-đầu-tiên)
  - [B9 — Test foundation](#b9--test-foundation-p0-viết-trước-khi-fix-b1-b6)
  - [B8 — datetime.utcnow deprecated](#b8--datetimeutcnow-deprecated-p1)
  - [B5 — completed_at không set](#b5--completed_at-không-set-p1)
  - [B6 — Profile.current_job_id không reset](#b6--profilecurrent_job_id-không-reset-p1)
  - [B1 — Aspect ratio stub](#b1--aspect-ratio-stub-p0)
  - [B2 — Bbox không verify](#b2--bbox-không-verify-p0)
  - [B3 — Camera preset không verify](#b3--camera-preset-không-verify-p0)
  - [B4 — Chains table unused](#b4--chains-table-unused-p2-fixed)
- [§4 — Test harness setup](#4--test-harness-setup)
- [§5 — Manual E2E verification protocol](#5--manual-e2e-verification-protocol)
- [§6 — Rollback plan](#6--rollback-plan)
- [§7 — Done-done checklist](#7--done-done-checklist)

---

## §1 — Nguyên tắc execution

### 1.1 One bug at a time
- Mỗi bug = 1 branch = 1 PR = 1 merge
- KHÔNG mở branch thứ 2 khi branch thứ nhất chưa merged
- Exception: tests infra (B9) setup TRƯỚC các bug khác vì các bug sau cần test

### 1.2 TDD strict cho mọi bug
Thứ tự bắt buộc mỗi bug:
1. **Write failing test** trước (reproduce bug)
2. Run test → confirm fail như mô tả
3. Write fix
4. Run test → confirm pass
5. Run ALL previous tests → confirm không regression
6. Update SPEC.md §D.4 (strike-through bug + commit hash)
7. Commit + PR

### 1.3 Không có "tiện thể fix luôn"
Trong 1 PR:
- ✅ Fix đúng 1 bug
- ✅ Test cho bug đó
- ✅ Update docs liên quan
- ❌ KHÔNG fix bug khác "tiện tay"
- ❌ KHÔNG refactor code không liên quan
- ❌ KHÔNG thêm feature

Nếu thấy bug khác giữa chừng → thêm vào `docs/WORKPLAN.md` §8 "Discovered during work" → fix sau.

### 1.4 Rule nghỉ giữa bug
Sau mỗi bug merge → STOP code → user review → user approve → bug tiếp theo.

### 1.5 Docs update = cùng commit
Mọi commit fix bug phải đi kèm update SPEC.md §D.4 (mark strike-through) trong CÙNG commit. Không tách.

### 1.6 Session report BẮT BUỘC cho mọi task
Mỗi session (triage / bug-fix / refactor) phải kết thúc bằng 1 file báo cáo:

- **Vị trí:** `docs/session-reports/YYYY-MM-DD_<task-id>_<slug>.md`
- **Template:** `docs/session-reports/_TEMPLATE.md` (copy rồi fill)
- **Commit:** file báo cáo được add vào CÙNG commit cuối của task (kèm code fix + SPEC.md strike-through). Không commit riêng.

Lý do:
1. Audit trail xuyên session — supervisor không dò chat
2. Không dựa vào việc user paste chat lại
3. Tự verify: supervisor đọc file là biết task đóng đúng chưa

Session con KHÔNG hoàn tất cho đến khi file report ở `docs/session-reports/` tồn tại và được commit. Nếu session con bỏ sót → supervisor reject, yêu cầu chạy lại 1 session nhỏ chỉ để viết report.

---

## §2 — Thứ tự sequential (bắt buộc)

| # | Bug | Lý do thứ tự này |
|---|---|---|
| 1 | **B7** port mismatch | Blocker cho mọi test tiếp — local dev không chạy được với port sai. 5 phút fix. |
| 2 | **B9** test foundation | Setup pytest + fixtures + test DB — bắt buộc trước khi làm B1-B6 (TDD). |
| 3 | **B8** datetime.utcnow | Đơn giản, grep-replace, low risk. Làm sớm để tất cả timestamp sạch từ đầu. |
| 4 | **B5** completed_at | Thay đổi update_job logic — cần test DB foundation (B9). |
| 5 | **B6** profile current_job_id | Liên quan B5 workflow — làm ngay sau B5. |
| 6 | **B1** aspect ratio | Thay đổi UI automation — cần manual browser test. |
| 7 | **B2** bbox verify | Cùng domain UI automation — làm sau B1. |
| 8 | **B3** camera preset verify | Tương tự B2. |
| 9 | **B4** chains table | **FIXED** post-Phase-A (2026-04-18). Persist metadata + derive status. |

**Total estimate:** ~3-4 ngày làm việc (không tính browser test thực).

---

## §3 — Chi tiết từng bug

---

### B7 — Port mismatch (P0, đầu tiên)

#### Vấn đề
File `server/config.py:19` default `SERVER_PORT=8000`, nhưng:
- `worker/main.py:29` default `SERVER_URL=http://localhost:8080`
- `docker/docker-compose.yml` expose port 8080
- `docker/Dockerfile.server` bind 8080
- `scripts/start_server.cmd` expect 8080

→ Chạy `scripts/start_all.cmd` local KHÔNG dùng env var → server lên port 8000 → worker connect 8080 → connection refused loop.

#### Files cần đổi
| File | Line | Hiện tại | Sau khi fix |
|---|---|---|---|
| `server/config.py` | 19 | `int(os.getenv("SERVER_PORT", "8000"))` | `int(os.getenv("SERVER_PORT", "8080"))` |

#### Verify
```bash
# Fresh clone:
scripts/start_server.cmd    # → server lên localhost:8080
curl http://localhost:8080/health  # → 200 OK
scripts/start_worker.cmd    # → worker connect thành công, log "Starting claim loop server=http://localhost:8080"
```

#### Test
`tests/test_config.py`:
```python
import os
from server.config import SERVER_PORT

def test_default_server_port_matches_worker_default():
    """B7: Server default port must match worker's SERVER_URL default."""
    # Worker defaults to http://localhost:8080 (worker/main.py:29)
    assert SERVER_PORT == 8080, (
        f"Server default port {SERVER_PORT} must match worker default 8080. "
        "See docs/WORKPLAN.md §B7."
    )
```

#### Commit
```
fix(config): unify server port default to 8080 (B7)

Server config defaulted to 8000 while worker defaulted to 8080,
causing connection refused on clean local install.
Closes #B7

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
```

#### Estimate
**5 phút** (1 line change + 1 test).

#### Done criteria
- [ ] `server/config.py:19` updated
- [ ] `tests/test_config.py` added + passes
- [ ] Fresh local install chạy được qua `scripts/start_all.cmd`
- [ ] SPEC.md §D.4 B7 strike-through
- [ ] PR merged

---

### B9 — Test foundation (P0, viết trước khi fix B1-B6)

#### Vấn đề
`tests/` chỉ có `__init__.py` rỗng. Không có:
- pytest config
- Test DB fixture
- Async client fixture
- Mock FlowClient

→ Không thể TDD các bug sau.

#### Files tạo mới
```
tests/
  __init__.py              (giữ nguyên)
  conftest.py              (MỚI — fixtures)
  test_config.py           (từ B7)
  test_job_store.py        (MỚI — B5 reproduce)
  test_profile_store.py    (MỚI — B6 reproduce)
  test_api.py              (MỚI — API smoke tests)
  test_claim_algorithm.py  (MỚI — claim priority tests)
  test_navigation.py       (MỚI — edit_url builder, media_id extract)
```

#### `conftest.py` spec
```python
"""Shared test fixtures."""
import asyncio
import os
import tempfile
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

# Use a temp DB for every test — isolate from dev DB
@pytest.fixture(scope="function")
def temp_db_path():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"
        os.environ["DATABASE_PATH"] = str(db_path)
        yield str(db_path)
        os.environ.pop("DATABASE_PATH", None)

@pytest_asyncio.fixture(scope="function")
async def db(temp_db_path):
    """Initialize fresh schema."""
    from server.db.database import init_db
    await init_db()
    yield
    # Teardown: file auto-removed by tempdir

@pytest_asyncio.fixture(scope="function")
async def api_client(db):
    """HTTP client bound to FastAPI app (no real server)."""
    from server.app import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

@pytest.fixture
def sample_job_payload():
    return {
        "type": "text-to-video",
        "prompt": "test prompt",
        "model": "veo-3.1-fast-lp",
        "aspect_ratio": "16:9",
    }

@pytest.fixture
def sample_profile():
    from server.models.profile import Profile, ProfileStatus
    from datetime import datetime, UTC
    return Profile(
        name="test-profile",
        google_account="test@example.com",
        locale="en",
        tier="ultra",
        status=ProfileStatus.AVAILABLE,
        created_at=datetime.now(UTC),
    )
```

#### `requirements-dev.txt` (MỚI)
```
pytest>=8.0.0
pytest-asyncio>=0.23.0
httpx>=0.27.0
```

#### `pytest.ini` hoặc `pyproject.toml` thêm:
```ini
[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
python_files = "test_*.py"
```

#### Verify
```bash
pip install -r requirements-dev.txt
pytest tests/ -v
# → Tất cả test pass (có thể chỉ là placeholder test ban đầu)
```

#### Test ban đầu (placeholder để verify fixture works)
`tests/test_smoke.py`:
```python
async def test_fixture_db_works(db):
    """B9: Smoke test that the DB fixture initializes."""
    from server.db.job_store import get_job_counts
    counts = await get_job_counts()
    assert counts == {
        "pending": 0, "claimed": 0, "running": 0,
        "completed": 0, "failed": 0, "cancelled": 0,
    }

async def test_fixture_api_client_works(api_client):
    """B9: Smoke test that FastAPI test client works."""
    r = await api_client.get("/health")
    assert r.status_code == 200
```

#### Commit
```
test: add pytest foundation + fixtures (B9)

- tests/conftest.py with temp DB + async HTTP client fixtures
- requirements-dev.txt with pytest, pytest-asyncio, httpx
- pytest.ini with asyncio auto mode
- Smoke tests to verify fixtures work

Closes #B9

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
```

#### Estimate
**2 giờ** (fixture design + smoke tests).

#### Done criteria
- [ ] `conftest.py` + fixtures
- [ ] `requirements-dev.txt`
- [ ] `pytest.ini`
- [ ] Smoke tests pass
- [ ] CI config (nếu có) update
- [ ] SPEC.md §A.4 update với coverage commands
- [ ] PR merged

---

### B8 — datetime.utcnow deprecated (P1)

#### Vấn đề
Python 3.12+ emit DeprecationWarning cho `datetime.utcnow()`. Python 3.13 có thể bỏ.

Grep kết quả (confirmed):
| File | Line |
|---|---|
| `worker/main.py` | 81, 91 |
| `server/db/job_store.py` | 35, 319 |
| `server/db/profile_store.py` | 19 |
| `server/routes/worker.py` | 45, 65 |

**Tổng 7 chỗ** trong 4 files (không tính worktrees, không tính docs).

#### Fix pattern
```python
# Trước:
from datetime import datetime
now = datetime.utcnow()

# Sau:
from datetime import datetime, UTC
now = datetime.now(UTC)
```

#### Chi tiết từng file

**`worker/main.py`:**
```python
# Line 14 (import) — đổi:
from datetime import datetime, timedelta
# thành:
from datetime import UTC, datetime, timedelta

# Line 81:
last_heartbeat = datetime.utcnow()
# thành:
last_heartbeat = datetime.now(UTC)

# Line 91:
now = datetime.utcnow()
# thành:
now = datetime.now(UTC)
```

**`server/db/job_store.py`:**
```python
# Line 4 (import) — đổi:
from datetime import datetime, timedelta
# thành:
from datetime import UTC, datetime, timedelta

# Line 35 (hàm _now_iso):
def _now_iso() -> str:
    return datetime.utcnow().isoformat()
# thành:
def _now_iso() -> str:
    return datetime.now(UTC).isoformat()

# Line 319 (recover_stale_jobs):
cutoff = (datetime.utcnow() - timedelta(minutes=stale_minutes)).isoformat()
# thành:
cutoff = (datetime.now(UTC) - timedelta(minutes=stale_minutes)).isoformat()
```

**`server/db/profile_store.py`:**
```python
# Line 3 (import):
from datetime import datetime
# thành:
from datetime import UTC, datetime

# Line 19 (_now_iso):
def _now_iso() -> str:
    return datetime.utcnow().isoformat()
# thành:
def _now_iso() -> str:
    return datetime.now(UTC).isoformat()
```

**`server/routes/worker.py`:**
```python
# Import (đầu file) — check và thêm UTC
from datetime import UTC, datetime

# Line 45:
_workers[req.worker_id] = datetime.utcnow()
# thành:
_workers[req.worker_id] = datetime.now(UTC)

# Line 65:
_workers[req.worker_id] = datetime.utcnow()
# thành:
_workers[req.worker_id] = datetime.now(UTC)
```

#### Lưu ý tương thích
- `datetime.now(UTC)` trả timezone-aware datetime
- `datetime.utcnow()` trả naive datetime
- `.isoformat()` output khác:
  - Naive: `"2026-04-17T10:30:45.123456"`
  - UTC-aware: `"2026-04-17T10:30:45.123456+00:00"`

→ Nếu có code parse ISO string giả định naive → sẽ break. **Check** khi fix:
- `server/db/job_store.py:82-86` parse Pydantic datetime → OK (Pydantic handles both)
- `server/models/job.py` field types là `datetime` → Pydantic accepts both

**An toàn** — Pydantic v2 tự động normalize.

#### Test
`tests/test_datetime_migration.py`:
```python
import warnings
from datetime import UTC, datetime

def test_no_utcnow_in_code():
    """B8: Ensure no datetime.utcnow() calls remain in production code."""
    import ast
    from pathlib import Path
    
    scan_dirs = ["server", "worker", "flow"]
    offenses = []
    for d in scan_dirs:
        for py in Path(d).rglob("*.py"):
            if "__pycache__" in str(py):
                continue
            text = py.read_text(encoding="utf-8")
            if "datetime.utcnow()" in text:
                offenses.append(str(py))
    
    assert not offenses, (
        f"datetime.utcnow() is deprecated. Found in: {offenses}. "
        "Use datetime.now(UTC) instead. See SPEC §R-CODE-10."
    )

async def test_utc_timestamps_have_timezone(db):
    """B8: Timestamps written by the system must be timezone-aware."""
    from server.db.job_store import create_job
    from server.models.job import Job, JobType, JobStatus
    
    job = Job(
        id="test-1",
        type=JobType.TEXT_TO_VIDEO,
        status=JobStatus.PENDING,
        prompt="x",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    await create_job(job)
    
    from server.db.job_store import get_job
    got = await get_job("test-1")
    # Pydantic loads as aware datetime when tzinfo present in ISO string
    assert got.created_at.tzinfo is not None
```

#### Commit
```
refactor: migrate datetime.utcnow() to datetime.now(UTC) (B8)

Python 3.12 deprecated datetime.utcnow(). Migrate 7 call sites
across worker/, server/db/, server/routes/ to tz-aware equivalents.

Closes #B8

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
```

#### Estimate
**45 phút** (replace + test + verify no regression).

#### Done criteria
- [ ] 4 files updated (7 call sites)
- [ ] `test_datetime_migration.py` passes
- [ ] No DeprecationWarning trong `pytest -W error::DeprecationWarning`
- [ ] Worker log (`worker_err.log`) không còn warning về utcnow sau manual smoke test
- [ ] SPEC.md §D.4 B8 strike-through

---

### B5 — `completed_at` không set (P1)

#### Vấn đề
`server/db/job_store.py:update_job` (line 136-168) chỉ update các field trong JobUpdate, KHÔNG tự động set `completed_at` khi status transitions → completed.

Worker `dispatcher.py:266-268` chỉ return `{"status": "completed"}` không kèm `completed_at`.

→ Cột `completed_at` NULL mãi mãi. Ảnh hưởng:
- Không biết job thật sự xong lúc nào
- Không tính được duration
- `recover_stale_jobs` dùng `updated_at` thay thế (OK) nhưng logic confusing

#### Files cần đổi
| File | Change |
|---|---|
| `server/db/job_store.py:136-168` | Trong `update_job`, nếu status transitions to `completed` / `failed` / `cancelled` → auto-set `completed_at` nếu chưa có |

#### Code change

**`server/db/job_store.py` — `update_job` sau line 144:**
```python
async def update_job(job_id: str, update: JobUpdate) -> Optional[Job]:
    """Apply partial update to a job. Returns updated Job or None if not found."""
    fields = update.model_dump(exclude_unset=True)
    if not fields:
        return await get_job(job_id)

    # B5: Auto-set completed_at when job reaches terminal status
    TERMINAL_STATES = {"completed", "failed", "cancelled"}
    new_status = fields.get("status")
    if new_status is not None:
        status_value = new_status.value if hasattr(new_status, "value") else new_status
        if status_value in TERMINAL_STATES and "completed_at" not in fields:
            fields["completed_at"] = _now_iso()

    sets: list[str] = []
    params: list = []
    # ... (rest unchanged)
```

**Alternative (nếu muốn cleaner — prefer):** đặt trong Pydantic JobUpdate validator, hoặc trong route handler. Nhưng DB-level là safest (ai gọi cũng đúng).

#### Test
`tests/test_job_store.py` (thêm):
```python
async def test_completed_at_auto_set_on_completion(db):
    """B5: completed_at must be set automatically when status → completed."""
    from server.db.job_store import create_job, update_job, get_job
    from server.models.job import Job, JobType, JobStatus, JobUpdate
    from datetime import datetime, UTC
    
    # Create pending job
    job = Job(
        id="b5-1",
        type=JobType.TEXT_TO_VIDEO,
        status=JobStatus.PENDING,
        prompt="x",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    await create_job(job)
    
    assert (await get_job("b5-1")).completed_at is None
    
    # Update to completed
    await update_job("b5-1", JobUpdate(status=JobStatus.COMPLETED))
    
    updated = await get_job("b5-1")
    assert updated.completed_at is not None, "completed_at must be set on completion"
    assert updated.status == JobStatus.COMPLETED

async def test_completed_at_set_on_failed(db):
    """B5: failed also counts as terminal → completed_at set."""
    # similar, with status=FAILED

async def test_completed_at_not_overwritten(db):
    """B5: explicit completed_at in update wins over auto-set."""
    from datetime import datetime, UTC, timedelta
    explicit = datetime.now(UTC) - timedelta(hours=1)
    # ... update with completed_at=explicit, status=completed → db has explicit time

async def test_completed_at_not_set_on_non_terminal(db):
    """B5: status=running should NOT set completed_at."""
    # ...
```

#### Commit
```
fix(job_store): auto-set completed_at on terminal status (B5)

When a job transitions to completed/failed/cancelled via update_job(),
automatically set completed_at if not explicitly provided.

Before: completed_at was NULL forever since no caller set it.
After: DB-level invariant that terminal state → timestamped.

Closes #B5

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
```

#### Estimate
**1 giờ** (change + 4 tests).

#### Done criteria
- [ ] `update_job` auto-set completed_at
- [ ] 4 test cases pass
- [ ] Manual verify: tạo job t2v giả lập, PUT status=completed → GET job → completed_at có giá trị
- [ ] SPEC.md §C.1 cập nhật (xoá `⚠️ B5` chú thích)
- [ ] SPEC.md §D.4 B5 strike-through

---

### B6 — `Profile.current_job_id` không reset (P1)

#### Vấn đề
Xem `server/db/profile_store.py` — **KHÔNG có hàm nào reset `current_job_id=NULL` sau khi job xong trên server-side DB.**

Worker-side `ProfileManager.mark_available` (line 47-55) có reset nhưng đó là in-memory trên worker, không sync ra DB.

Check `server/routes/worker.py`: claim endpoint không update Profile.current_job_id, update endpoint cũng không.

**Thực tế:** `current_job_id` field trong DB có thể chưa bao giờ được set → NULL vĩnh viễn. Đây là field unused chứ không phải stale.

**Question:** nên xoá field hay implement đầy đủ?

**Decision proposal:** Implement đầy đủ (track server-side cho đa worker visibility):
- Khi `claim_next_job` thành công → UPDATE Profile SET current_job_id = job.id
- Khi `update_job` với status=terminal → UPDATE Profile SET current_job_id = NULL (nếu đang refer job này)

#### Files cần đổi
| File | Change |
|---|---|
| `server/db/job_store.py:claim_next_job` | Sau UPDATE jobs → UPDATE profiles SET current_job_id |
| `server/db/job_store.py:update_job` | Nếu terminal → UPDATE profiles SET current_job_id=NULL WHERE current_job_id=job_id |

#### Code change

**`server/db/job_store.py` — trong `claim_next_job`, sau mỗi UPDATE jobs thành công:**

```python
# Priority 1 — sau khi UPDATE jobs (line 250 hiện tại):
await db.execute(
    """
    UPDATE profiles
    SET current_job_id = ?, worker_id = ?, last_used_at = ?
    WHERE name = ?
    """,
    (job_dict["id"], worker_id, now, bound_profile),
)
# Priority 2 — tương tự sau UPDATE jobs (line 285 hiện tại):
await db.execute(
    """
    UPDATE profiles
    SET current_job_id = ?, worker_id = ?, last_used_at = ?
    WHERE name = ?
    """,
    (job_dict["id"], worker_id, now, assigned_profile),
)
```

**`server/db/job_store.py` — trong `update_job`, khi terminal status:**
```python
# Sau khi UPDATE jobs:
if status_value in TERMINAL_STATES:  # reuse từ B5
    await db.execute(
        """
        UPDATE profiles
        SET current_job_id = NULL
        WHERE current_job_id = ?
        """,
        (job_id,),
    )
```

#### Lưu ý
- Atomic: nằm trong cùng transaction với UPDATE jobs
- Nếu profile chưa tồn tại trong DB (worker dùng profile chưa register) → UPDATE không match rows → no-op, OK.

#### Test
`tests/test_profile_store.py` + `tests/test_claim_algorithm.py`:
```python
async def test_profile_current_job_set_on_claim(db):
    """B6: claim_next_job must set profile.current_job_id."""
    # Create profile + pending job
    # Call claim_next_job(worker_id="w1", available_profiles=["p1"])
    # Assert: profile "p1" has current_job_id = job.id

async def test_profile_current_job_cleared_on_completion(db):
    """B6: update_job with terminal status must clear profile.current_job_id."""
    # Setup: profile with current_job_id=X
    # Call update_job(X, status=completed)
    # Assert: profile.current_job_id is None

async def test_profile_current_job_not_cleared_on_running(db):
    """B6: non-terminal status must NOT clear current_job_id."""
    # Setup: profile with current_job_id=X
    # Call update_job(X, status=running)
    # Assert: profile.current_job_id still = X
```

#### Commit
```
fix(profile): track current_job_id on claim/complete (B6)

Profile.current_job_id column was never populated. Now:
- claim_next_job sets it to the claimed job's id
- update_job clears it when job reaches terminal state

This enables UI to show which job each profile is running.

Closes #B6

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
```

#### Estimate
**2 giờ** (2 UPDATE statements + 3 tests + verify UI).

#### Done criteria
- [ ] claim_next_job updates profile
- [ ] update_job clears profile on terminal
- [ ] 3 tests pass
- [ ] Manual: tạo job, verify Profiles page trên UI hiển thị đúng current job
- [ ] SPEC.md §C.2 xoá `⚠️ B6` chú thích
- [ ] SPEC.md §D.4 B6 strike-through

---

### B1 — Aspect ratio stub (P0)

#### Vấn đề
`flow/operations/generate.py:483-501` — `_set_aspect_ratio()` half-implemented:
- Line 488-489: nếu ratio == "16:9" → return ngay (không set gì)
- Line 493-498: tìm button theo text "16:9" / "9:16" — nhưng aspect ratio KHÔNG phải button text visible trên main UI. Nó nằm trong model options panel hoặc settings.
- Line 499-501: silent swallow exception → fail silent

**Thực tế Flow UI (theo `docs/FLOW_UI_REFERENCE.md`):**
- Info panel hiện `📱 9:16` hoặc `🖥️ 16:9` — nhưng đó là read-only metadata
- Aspect ratio selector nằm trong model compose options (khi chọn model)
- Hoặc trong dropdown riêng

**Verify cần làm TRƯỚC khi code:**
Manual browser test để document chính xác aspect ratio UI path:
1. Mở Flow homepage English profile
2. Click "+ New project"
3. Tìm selector cho aspect ratio: nút riêng? dropdown? trong model panel?
4. Document DOM selectors
5. Update `docs/FLOW_UI_REFERENCE.md` với info này
6. Mới code fix

→ **B1 có 2 phase:**
- B1a: Research + document aspect ratio UI (manual)
- B1b: Implement code

#### Files cần đổi (sau research)
| File | Change |
|---|---|
| `flow/operations/generate.py:483-501` | Rewrite `_set_aspect_ratio` dùng DOM selector thật |
| `docs/FLOW_UI_REFERENCE.md` | Thêm section Aspect Ratio UI selector |

#### Fix pattern (giả sử aspect ratio là dropdown trong model panel)
```python
async def _set_aspect_ratio(page, ratio: str):
    """Set aspect ratio. Default 16:9 — skip if same."""
    if not ratio or ratio == "16:9":
        logger.info("Using default aspect ratio 16:9")
        return

    # Known aspect ratios Flow supports
    RATIO_MAP = {
        "16:9": "16:9",   # landscape (default)
        "9:16": "9:16",   # portrait
        "1:1": "1:1",     # square
    }
    if ratio not in RATIO_MAP:
        logger.warning("Unknown aspect ratio %r — using default", ratio)
        return

    # STEP 1: Open aspect ratio selector
    # (exact selector TBD after research — placeholder:)
    try:
        # Open model options panel if aspect ratio lives there
        panel_btn = page.locator("[aria-label*='aspect' i]").first
        if await panel_btn.is_visible(timeout=2000):
            await panel_btn.click()
            await asyncio.sleep(0.5)
        
        # Click ratio option
        option = page.locator(f"[role='menuitemradio']:has-text('{RATIO_MAP[ratio]}')").first
        if await option.is_visible(timeout=2000):
            await option.click()
            logger.info("Aspect ratio set to %s", ratio)
            
            # VERIFY — check UI chip/label reflects the new ratio
            chip = page.locator(f"[aria-label*='{RATIO_MAP[ratio]}']").first
            if await chip.is_visible(timeout=2000):
                logger.info("Aspect ratio verified: %s", ratio)
            else:
                logger.warning("Aspect ratio click succeeded but chip not visible")
            return
    except Exception as e:
        logger.warning("Failed to set aspect ratio %s: %s", ratio, e)

    # Fall through: silent warn (not fatal)
    logger.warning("Could not set aspect ratio %s — using default 16:9", ratio)
```

#### Test
`tests/test_aspect_ratio.py` (unit test — mock page):
```python
import pytest
from unittest.mock import AsyncMock, MagicMock

async def test_aspect_ratio_skip_default():
    """B1: ratio 16:9 is default → should not interact with UI."""
    from flow.operations.generate import _set_aspect_ratio
    page = MagicMock()
    page.locator = MagicMock()
    await _set_aspect_ratio(page, "16:9")
    # Assert: no locator calls made (return early)
    page.locator.assert_not_called()

async def test_aspect_ratio_portrait():
    """B1: ratio 9:16 opens panel and clicks option."""
    # mock page.locator chain to simulate successful interaction
    # assert logger says "Aspect ratio set to 9:16"
```

**Plus**: manual E2E test — xem §5.

#### Commit
```
fix(generate): implement aspect ratio selection (B1)

_set_aspect_ratio was half-stub:
- Returned immediately for 16:9 (OK)
- Tried to click button with ratio text (wrong UI path)
- Silently swallowed exceptions

Now:
- Opens model options aspect-ratio dropdown
- Clicks target ratio option
- Verifies via UI chip

Researched Flow UI path: see docs/FLOW_UI_REFERENCE.md §N.

Closes #B1

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
```

#### Estimate
**3-4 giờ** (research 1.5h + code 1h + test 1h + manual verify 0.5h).

#### Done criteria
- [ ] Manual Flow UI research done
- [ ] `docs/FLOW_UI_REFERENCE.md` updated với aspect ratio selector path
- [ ] `_set_aspect_ratio` dùng đúng selector + có verify step
- [ ] Unit test pass
- [ ] Manual E2E: tạo t2v với `aspect_ratio="9:16"` → verify video output là portrait
- [ ] SPEC.md §D.4 B1 strike-through

---

### B2 — Bbox không verify (P0)

#### Vấn đề
`flow/operations/insert.py:107-159` và `remove.py:103-150` — `_draw_bbox()` mouse.move → mouse.down → mouse.up drag chuột, nhưng KHÔNG verify:
- Mouse có thực sự trên video canvas không (nếu bbox nằm ngoài → drag ngoài canvas)
- Flow có hiện overlay rectangle sau khi drag không
- Bbox có đúng vùng mong muốn không

Nếu drag fail → Flow dùng default vùng hoặc error silent → user không biết.

#### Files cần đổi
| File | Change |
|---|---|
| `flow/operations/insert.py:_draw_bbox` | Thêm pre-drag bounds check + post-drag verify |
| `flow/operations/remove.py:_draw_bbox` | Cùng logic (có thể extract thành shared helper) |

#### Refactor proposal
Extract `_draw_bbox` vào `flow/operations/_base.py`:
```python
# flow/operations/_base.py (thêm):

async def draw_bbox_on_video(page, bbox: dict) -> bool:
    """Draw a bounding box on the video canvas by mouse drag.
    
    Args:
        bbox: {x, y, w, h} normalized 0-1 relative to video rect.
    
    Returns:
        True if drag succeeded and bbox overlay is visible; False otherwise.
    """
    # Step 1: Get video rect
    video_rect = await page.evaluate("""() => {
        const video = document.querySelector('video');
        if (!video) return null;
        const r = video.getBoundingClientRect();
        return {left: r.left, top: r.top, width: r.width, height: r.height};
    }""")
    
    if not video_rect or video_rect["width"] < 50 or video_rect["height"] < 50:
        logger.error("Video element not found or too small: %s", video_rect)
        return False
    
    # Step 2: Validate bbox within 0-1
    for k in ("x", "y", "w", "h"):
        v = bbox.get(k, 0)
        if not (0 <= v <= 1):
            logger.error("bbox[%s]=%s out of range 0-1", k, v)
            return False
    
    x, y, w, h = bbox.get("x", 0.25), bbox.get("y", 0.25), bbox.get("w", 0.5), bbox.get("h", 0.5)
    
    # Clamp bbox to fit within video rect
    if x + w > 1: w = 1 - x
    if y + h > 1: h = 1 - y
    
    vl, vt = video_rect["left"], video_rect["top"]
    vw, vh = video_rect["width"], video_rect["height"]
    
    start_x, start_y = vl + x * vw, vt + y * vh
    end_x, end_y = vl + (x + w) * vw, vt + (y + h) * vh
    
    # Step 3: Drag
    await page.mouse.move(start_x, start_y)
    await page.mouse.down()
    await asyncio.sleep(0.1)
    for i in range(1, 6):
        px = start_x + (end_x - start_x) * i / 5
        py = start_y + (end_y - start_y) * i / 5
        await page.mouse.move(px, py)
        await asyncio.sleep(0.05)
    await page.mouse.up()
    await asyncio.sleep(0.5)
    
    # Step 4: VERIFY — check bbox overlay is visible
    overlay_visible = await page.evaluate("""() => {
        // Flow shows a selection rectangle after bbox drag — 
        // typically an SVG rect or div with class containing "bbox", "selection", "region"
        const candidates = document.querySelectorAll(
            'svg rect, [class*="bbox" i], [class*="selection" i], [class*="region" i]'
        );
        for (const el of candidates) {
            const r = el.getBoundingClientRect();
            if (r.width >= 20 && r.height >= 20) {
                const s = getComputedStyle(el);
                if (s.display !== 'none' && s.visibility !== 'hidden') {
                    return true;
                }
            }
        }
        return false;
    }""")
    
    if not overlay_visible:
        logger.warning(
            "Bbox drag completed but no overlay detected. "
            "bbox=%s video_rect=%s — may have missed canvas",
            bbox, video_rect,
        )
        return False
    
    logger.info(
        "Drew bbox (verified): x=%.2f y=%.2f w=%.2f h=%.2f on video %dx%d",
        x, y, w, h, vw, vh,
    )
    return True
```

**Insert/Remove update:**
```python
# insert.py:78-79, remove.py:80:
# Trước:
if bbox:
    await _draw_bbox(page, bbox)
# Sau:
if bbox:
    from flow.operations._base import draw_bbox_on_video
    drew = await draw_bbox_on_video(page, bbox)
    if not drew:
        logger.warning("Bbox drawing failed or unverified — Flow may use default region")
        # Decide policy: raise or continue? → continue (Flow tolerates missing bbox)
```

#### Research needed
**TRƯỚC khi code:** manual test để biết Flow bbox overlay DOM selector thật:
1. Mở Flow edit view
2. Click Insert → draw bbox bằng chuột
3. Inspect DOM sau khi drag — xem element nào xuất hiện (class? tag? attribute?)
4. Update selector list trong `draw_bbox_on_video`

#### Test
`tests/test_bbox.py`:
```python
async def test_bbox_validates_range():
    """B2: bbox with out-of-range values must fail verify."""
    # mock page to return valid video_rect
    # call draw_bbox_on_video({x: 1.5, y: 0, w: 0.5, h: 0.5})
    # assert return False, logger.error called

async def test_bbox_clamps_overflow():
    """B2: x + w > 1 gets clamped to fit video."""
    # bbox {x: 0.7, w: 0.5} should be clamped so x+w = 1

async def test_bbox_no_video_element():
    """B2: if video not found, return False early."""
```

Plus manual E2E:
- Tạo insert job với bbox={x:0.7,y:0.1,w:0.2,h:0.2}
- Verify video output có object ở góc trên-phải
- Tạo insert job với bbox={x:1.5,y:0,w:0.5,h:0.5} (invalid)
- Verify job không crash; log cảnh báo

#### Commit
```
fix(flow): verify bbox drawing with overlay detection (B2)

Insert/remove drew bbox via mouse drag but never verified the
drag landed on the video canvas. Now:
- Extracted draw_bbox_on_video() to flow/operations/_base.py
- Validates bbox values are 0-1
- Clamps overflow (x+w>1) to fit video
- Detects Flow's overlay rect after drag
- Returns False if drag failed; caller logs warning

Closes #B2

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
```

#### Estimate
**4 giờ** (research 1h + refactor 1.5h + test 1h + manual 0.5h).

#### Done criteria
- [ ] Manual research bbox overlay selector
- [ ] `draw_bbox_on_video` in _base.py
- [ ] insert.py + remove.py dùng shared helper
- [ ] Unit tests pass
- [ ] Manual E2E: bbox đúng vùng
- [ ] SPEC.md §D.4 B2 strike-through

---

### B3 — Camera preset không verify (P0)

#### Vấn đề
`flow/operations/camera.py:133-183` — `_click_preset` có 3 strategies:
1. `[role='button']:has-text(...)` / `button:has-text(...)` / `*:has-text():not(body):not(html):not(div)`
2. `page.get_by_text(direction, exact=False).first`
3. Strategy 3 — `*:visible` filter với regex — rất dễ match nhầm (e.g., direction="Low" có thể match "Lower" trên button khác).

**KHÔNG verify** preset đã được chọn (active state / highlighted / `aria-pressed=true`).

→ Có thể click trúng element sai, submit với preset mặc định, user không biết.

#### Research needed
TRƯỚC code:
1. Manual: click từng preset → inspect DOM
2. Document: active state là gì? `aria-pressed`? class `active`? border highlight?
3. Update `docs/FLOW_UI_REFERENCE.md` §Camera Mode với DOM selector chính xác

#### Fix pattern
```python
async def _click_preset(page, direction: str) -> bool:
    """Click a camera preset by name and verify it's selected."""
    # Strategy 1: aria-label exact match (MOST RELIABLE)
    try:
        el = page.locator(f"[aria-label='{direction}']").first
        if await el.is_visible(timeout=2000):
            await el.click(timeout=3000)
            logger.info("Clicked preset via aria-label: %s", direction)
            await asyncio.sleep(0.5)
            if await _verify_preset_selected(page, direction):
                return True
    except Exception:
        pass
    
    # Strategy 2: role=button with exact text
    try:
        el = page.locator(f"[role='button']").filter(has_text=direction).first
        # ... click + verify
    except Exception:
        pass
    
    # Strategy 3: generic with EXACT text (cẩn thận partial match)
    try:
        # Use Playwright's exact-text matching
        el = page.get_by_text(direction, exact=True).first
        if await el.is_visible(timeout=2000):
            await el.click(timeout=3000)
            await asyncio.sleep(0.5)
            if await _verify_preset_selected(page, direction):
                return True
    except Exception:
        pass
    
    logger.error("Could not click+verify camera preset: %s", direction)
    return False


async def _verify_preset_selected(page, direction: str) -> bool:
    """Verify the camera preset is active after clicking.
    
    Active state detection (any one sufficient):
    - aria-pressed="true" on element
    - aria-selected="true"
    - class contains "active", "selected", "pressed"
    - parent/ancestor has selected indicator
    """
    try:
        is_selected = await page.evaluate("""(direction) => {
            // Find element matching the direction
            const els = document.querySelectorAll(
                `[aria-label="${direction}"], [role="button"], button`
            );
            for (const el of els) {
                const text = el.textContent?.trim() || '';
                const label = el.getAttribute('aria-label') || '';
                if (text === direction || label === direction) {
                    // Check active state
                    if (el.getAttribute('aria-pressed') === 'true') return true;
                    if (el.getAttribute('aria-selected') === 'true') return true;
                    const cls = el.className || '';
                    if (/active|selected|pressed/i.test(cls)) return true;
                    // Check parent for selected state
                    const parent = el.parentElement;
                    if (parent) {
                        if (/active|selected/i.test(parent.className || '')) return true;
                    }
                }
            }
            return false;
        }""", direction)
        if is_selected:
            logger.info("Preset verified selected: %s", direction)
            return True
        logger.warning("Preset clicked but not verified active: %s", direction)
        return False
    except Exception as e:
        logger.warning("Preset verify failed: %s", e)
        return False
```

#### Test
`tests/test_camera.py`:
```python
async def test_camera_click_preset_success():
    """B3: _click_preset returns True when preset is active after click."""
    # mock page — simulate aria-label selector + aria-pressed=true

async def test_camera_click_preset_no_verify_fails():
    """B3: if no active state after click → return False."""

async def test_camera_position_vs_motion_tab():
    """B3: 'Center' goes to position tab, 'Dolly in' to motion tab."""
```

Plus manual E2E:
- camera job direction="Dolly in" → verify output có zoom-in effect
- camera job direction="Center" → verify position reset
- camera job direction="Low" → verify NOT matched với "Lower" nếu có

#### Commit
```
fix(camera): verify preset is selected after click (B3)

_click_preset used fuzzy matching (partial text, visible filter)
that could hit wrong elements. Now:
- Primary: aria-label exact match
- Fallback: role=button with exact text  
- Last: get_by_text(exact=True)
- After click, _verify_preset_selected() checks aria-pressed /
  aria-selected / active class

Closes #B3

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
```

#### Estimate
**3 giờ** (research 1h + code 1h + test 1h).

#### Done criteria
- [ ] Manual research active state selector
- [ ] `docs/FLOW_UI_REFERENCE.md` §Camera update
- [ ] `_click_preset` + `_verify_preset_selected` implemented
- [ ] Unit tests pass
- [ ] Manual E2E 3 directions
- [ ] SPEC.md §D.4 B3 strike-through

---

### ~~B4 — Chains table unused (P2)~~ ✅ FIXED post-Phase-A (2026-04-18)

#### Original defer rationale (2026-04-17, superseded)
- Chain metadata có thể derive từ `SELECT chain_id, status FROM jobs GROUP BY chain_id`
- Không ảnh hưởng correctness
- Thêm chains INSERT/UPDATE logic = thêm 2 nơi có thể out-of-sync

#### Resolution (Choice C — Hybrid, 2026-04-18)
User approved fix post-Phase-A. Choice C was selected after comparing:
- **Choice A (full sync)** — INSERT on POST + UPDATE chains.status on every job terminal transition. Rejected: two places storing the same truth creates drift risk if one UPDATE fails.
- **Choice B (view-only)** — compute everything on-demand, never touch chains table. Rejected: leaves the CREATE TABLE literally unused, which is exactly the original smell.
- **Choice C (hybrid, chosen)** — INSERT immutable metadata on POST (`id`, `profile`, `created_at`, `updated_at`), NEVER UPDATE. Aggregated status + progress derived on every GET from `SELECT id, status FROM jobs WHERE chain_id = ? ORDER BY created_at ASC`. The existing `chains.status` column stays at DEFAULT `'active'` — vestigial, not surfaced. Zero drift path by construction.

#### Implementation
- `server/models/chain.py` (NEW) — `Chain` (DB row) + `ChainAggregate` (API response) + `ChainProgress`.
- `server/db/chain_store.py` (NEW) — `create_chain`, `get_chain_row` (raw + vestigial status, tests only), `get_chain_aggregate`, `compute_aggregated_status` (pure fn).
- `server/routes/jobs.py` — `POST /api/chains` now calls `create_chain(Chain(id, profile))` before creating jobs. New `GET /api/chains/{id}` returns `ChainAggregate`.
- `server/db/database.py` — schema unchanged. Minimal diff.
- `tests/test_chains.py` — 17 cases covering: status rule unit tests (6), create/get row (2), POST persistence + GET aggregate (7), no-sync trip-wire (1), ordering (1).

#### Aggregated status rules (priority order)
1. any `failed` → `failed`
2. any `running` or `claimed` → `running`
3. any `pending` alongside `completed`/`cancelled` → `running` (in-progress)
4. all `pending` → `pending`
5. all `cancelled` → `cancelled`
6. otherwise (≥1 completed, no failures) → `completed`

#### Done criteria
- [x] Choice C rationale documented (session report §7)
- [x] chains row INSERTed on POST /api/chains
- [x] GET /api/chains/{id} returns aggregated status + progress + ordered job ids
- [x] Trip-wire test confirms no-sync invariant
- [x] 17 new tests pass, full suite 80 pass (no regression from 63 baseline)
- [x] `-W error::DeprecationWarning` clean
- [x] SPEC.md §D.4 B4 strike-through + §C.1b Chain Schema + §C.3 GET /api/chains/{id}
- [x] Session report `docs/session-reports/2026-04-18_B4_chains-table.md`

---

## §4 — Test harness setup

Xem chi tiết trong [B9 — Test foundation](#b9--test-foundation-p0-viết-trước-khi-fix-b1-b6).

### 4.1 Target coverage sau Phase A
```
server/db/job_store.py       ≥ 80%
server/db/profile_store.py   ≥ 70%
server/routes/jobs.py        ≥ 70%
server/routes/worker.py      ≥ 70%
worker/profile_manager.py    ≥ 70%
worker/project_lock.py       ≥ 90%
worker/dispatcher.py         ≥ 50% (rest needs browser)
flow/navigation.py           ≥ 80%
flow/media_id.py             ≥ 80%

OVERALL server+worker (excl flow/) ≥ 70%
```

### 4.2 Test files master list
```
tests/
  conftest.py
  test_smoke.py              (B9)
  test_config.py             (B7)
  test_datetime_migration.py (B8)
  test_job_store.py          (B5, B6)
  test_profile_store.py      (B6)
  test_claim_algorithm.py    (generic — all bugs verify claim correctness)
  test_api.py                (all — API surface smoke)
  test_navigation.py         (utility — edit_url, media_id extract)
  test_bbox.py               (B2)
  test_camera.py             (B3)
  test_aspect_ratio.py       (B1)
```

### 4.3 CI target — ✅ implemented (post-Phase-A)
- GitHub Actions workflow: `.github/workflows/tests.yml` (trigger: PR → `master`, push → `master`)
- Runs `pytest tests/ -v` + `pytest tests/ -W error::DeprecationWarning` on Python 3.13, ubuntu-latest, 10m timeout
- Fail PR nếu test fail hoặc có DeprecationWarning leak
- Coverage gate deferred — 30 % baseline chưa đạt R-TEST-4 target 70 %, sẽ add khi Phase B boost coverage
- ~~**Không implement trong Phase A** — nhưng prepare `pytest.ini` + `requirements-dev.txt` để sẵn~~ (done)

---

## §5 — Manual E2E verification protocol

Sau khi tất cả 8 bug (bao gồm B4 fix post-Phase-A) đã merged, chạy manual E2E test để verify real-world correctness.

### 5.1 Chuẩn bị
1. Chrome profile `ngoctuandt2` đã login Google account `ngoctuandt2@gmail.com`
2. LP slots còn > 4 (check Flow UI)
3. Dev server + worker running, DB clean: `rm -rf data/ && mkdir data/`
4. `scripts/start_all.cmd`

### 5.2 E2E Test Suite

#### Test 1 — Single text-to-video (B1 aspect ratio verify)
```
1. POST /api/jobs {type: text-to-video, prompt: "golden sunset ocean waves", aspect_ratio: "9:16"}
2. Observe dashboard: pending → claimed → running → completed
3. Verify:
   - Output file exists in downloads/
   - Video dimensions = portrait (9:16)
   - Job.media_id populated
   - Job.project_url populated
   - Job.completed_at populated (B5)
   - Profile.current_job_id = None sau khi xong (B6)
```

#### Test 2 — Chain 4 bước (main regression test)
```
1. POST /api/chains {
     jobs: [
       {type: t2v, prompt: "mountain lake at sunrise"},
       {type: extend, prompt: "camera zooms out to reveal forest"},
       {type: insert, prompt: "a hot air balloon", bbox: {x:0.1,y:0.1,w:0.3,h:0.3}},
       {type: camera, direction: "Dolly in"}
     ]
   }
2. Watch dashboard: 4 jobs chạy tuần tự
3. Verify after each:
   - Same project_url across all 4 (INV-2)
   - media_id: J1 = J2 = J3 (extend + insert preserve); J4 mints NEW (camera-move — SPEC INV-5)
   - Each job's stored `media_id` matches `/edit/{id}` URL at completion (post-op re-extract)
   - Same profile across all 4 (INV-1)
4. On Flow UI: open project, verify:
   - History panel có 4 entries
   - Media vẫn cùng /edit/{uuid} URL
   - Video output reflects all 4 ops (balloon visible, zoom-in camera)
5. downloads/: 4 files
```

#### Test 3 — Bbox edge cases (B2)
```
3a. POST /api/jobs {type: insert, parent_job_id: <from Test 2 step 3>, prompt: "bird", bbox: {x:0.01,y:0.01,w:0.1,h:0.1}}
    → Bird tiny in top-left corner
3b. POST với bbox out of range {x:1.5, y:0}
    → Job vẫn complete (silent fallback) nhưng log WARNING
```

#### Test 4 — Camera presets (B3)
```
Chạy 3 job camera với 3 preset khác nhau:
- direction: "Dolly in"     → zoom-in effect
- direction: "Orbit left"   → camera xoay trái
- direction: "Low"          → camera thấp xuống  ← verify KHÔNG match "Lower" vì từ mơ hồ
```

#### Test 5 — Profile pinning (INV-1)
```
Setup: 2 workers, 2 profiles (profile-A, profile-B)
1. Worker-A chỉ mount profile-A; Worker-B chỉ mount profile-B.
2. Tạo chain 3 bước.
3. Observe:
   - Job 1 claim bởi một trong 2 worker (random)
   - Job 2, 3 CHỈ claim bởi worker đã chạy Job 1
```

#### Test 6 — Project lock (INV-4)
```
Setup: 1 worker, 1 profile
1. Tạo chain 3 bước.
2. Khi chain đang chạy Job 2, tạo thêm 1 extend job cùng project_url.
3. Verify: job mới KHÔNG claim cho đến khi Job 2 + 3 của chain xong.
```

#### Test 7 — Stale recovery
```
1. Tạo job t2v.
2. Khi job đang running, Ctrl+C kill worker process.
3. Job kẹt status=running trong DB.
4. Wait 30 phút (hoặc edit recover_stale_jobs cutoff để test nhanh: 1 phút).
5. POST /api/jobs/recover → job reset về pending.
6. Start worker → claim lại → complete.
```

### 5.3 Kết quả cần ghi lại
Tạo `docs/E2E_RESULTS_PHASE_A.md` với:
- Timestamp mỗi test
- Pass/Fail
- Logs liên quan
- Screenshots nếu có
- Issues gặp

---

## §6 — Rollback plan

### 6.1 Per-bug rollback
Mỗi PR merged có commit hash → `git revert <hash>` đơn giản.

### 6.2 Nếu 1 PR gây regression sau merge
1. NGAY LẬP TỨC: `git revert` commit → push → re-deploy
2. Mở issue mới mô tả regression
3. Fix trong branch mới, không reuse branch cũ

### 6.3 Nếu phát hiện lỗi lớn giữa phase
- Tạm dừng Phase A
- Spike 1 session debug
- Update WORKPLAN.md với discovery
- Resume với thứ tự mới nếu cần

---

## §7 — Done-done checklist

Phase A coi là hoàn thành khi ALL items:

### Code
- [ ] B7 merged (port unified)
- [ ] B9 merged (test foundation)
- [ ] B8 merged (datetime migration)
- [ ] B5 merged (completed_at auto-set)
- [ ] B6 merged (profile current_job_id tracking)
- [ ] B1 merged (aspect ratio real impl)
- [ ] B2 merged (bbox verify)
- [ ] B3 merged (camera preset verify)
- [x] B4 fixed post-Phase-A (commit `4dcf50f`) — chains table persisted + aggregated status endpoint

### Test
- [ ] `pytest tests/` all pass
- [ ] Coverage ≥ 70% trên server + worker (exclude flow/)
- [ ] Zero DeprecationWarning với `pytest -W error::DeprecationWarning`

### Docs
- [ ] SPEC.md §D.4 B1-B8 strike-through với commit hash
- [x] SPEC.md §D.4 B4 strike-through (FIXED commit `4dcf50f`) + §C.1b Chain Schema + §C.3 GET /api/chains/{id}
- [ ] FLOW_UI_REFERENCE.md updated (aspect ratio, bbox overlay, camera active state)
- [ ] WORKPLAN.md §8 "Discovered during work" populated nếu có
- [ ] CLAUDE.md §6 "Epic History" thêm entry Phase A
- [ ] README.md (nếu có) reflects current state

### Manual E2E
- [ ] 7 tests §5.2 pass
- [ ] `docs/E2E_RESULTS_PHASE_A.md` filled

### Meta
- [ ] User review + approve
- [ ] Tag git: `v0.2.0-phase-a`

---

## §8 — Discovered during work

> Phần này sẽ populate KHI làm. Mỗi item = bug/issue mới phát hiện giữa khi fix B1-B9.
> Format:
> - **[B-discovery-N]** Short description — found during fixing Bx — file:line — severity — deferred to Phase B? / fix inline?

- ~~**[B10]** Pydantic `default_factory=datetime.utcnow` residual — found during B8 (commit `573cffd`) — `server/models/job.py:96-97`, `server/models/profile.py:25` — P2 severity (deprecation only, not correctness) — **deferred to post-B3** (15m estimate). Executor chose NOT to extend B8 scope per §1.3 "không tiện thể fix luôn". Full rationale in `docs/session-reports/2026-04-17_B8_datetime-utcnow.md §7 Q1`. See SPEC.md §D.4 B10 for fix plan.~~ ✅ **FIXED in commit `fe13870`** — replaced all 3 sites with `default_factory=lambda: datetime.now(UTC)` (Choice 1 — inline lambda, not `_utils.py` helper; rationale in report §7 Q1). Extended `tests/test_datetime_migration.py::test_no_utcnow_in_code` to also forbid `default_factory=datetime.utcnow` substring (RED → GREEN). Full-suite 48 pass + zero DeprecationWarning under `-W error::DeprecationWarning`. Report: `docs/session-reports/2026-04-18_B10_pydantic-default-factory.md`.
- ~~**[B11]** Bbox draw+verify targets wrong element — found during Tier1 E2E (commit `9facbe3`) — `flow/operations/_base.py::draw_bbox_on_video` (line 236 `querySelector('video')` + line ~290 union selector) — **P0** (insert/remove silently broken: always falls back to Flow default region). `video` query hits 105×60 card-strip thumbnail, not the 598×336 canvas preview; bbox is canvas-painted so union selector cannot match. Fix direction: target largest visible `<canvas>` + replace DOM-verify with pointer-delivery trust OR network-body inspection. Full evidence: `docs/session-reports/2026-04-17_Tier1_dom-validation.md §B2`. Queue: **next after B12** (B11 is silent-fallback, B12 is hard-raise regression — fix B12 first).~~ ✅ **FIXED in commit `ce6683a`** — `draw_bbox_on_video` rewritten to target largest visible `<canvas>` with `width ≥ 300` (excludes 105-px thumbnails); post-drag DOM verify removed in favor of pointer-trust (Option B) — pixel sampling rejected due to video-frame noise + CORS/WebGL risk. Tests rewritten (6 cases including two contract trip-wires). Report: `docs/session-reports/2026-04-17_B11_bbox-canvas-fix.md`.
- ~~**[B12]** Camera preset verify regression — found during Tier1 E2E (commit `9facbe3`) — `flow/operations/camera.py::_click_preset` + `_verify_preset_selected` (introduced by `58937d4`) — **P0 REGRESSION** (camera_move raises RuntimeError on every call). Strategies 1+2 find 0 elements (presets lack `aria-label` + `role="button"` attr); strategy 3 click succeeds but all 4 verify signals fail (styled-components hash-only classes). Real state marker is `getComputedStyle(label).color` rgb(48,48,48) selected vs rgb(255,255,255) unselected. Fix direction: keep strategy 3 click, rewrite verify to use computed color flip on inner label DIV. Full evidence: `docs/session-reports/2026-04-17_Tier1_dom-validation.md §B3`. **Queue: fix FIRST — currently blocks all camera jobs.**~~ ✅ **FIXED in commit `78d3e40`** — `_verify_preset_selected` rewritten to read `getComputedStyle(labelDiv).color` (threshold R+G+B < 400); `_click_preset` pruned to single `get_by_text(exact=True)` strategy (dead strategies 1+2 removed per §1.3). Tests rewritten (7 cases). Report: `docs/session-reports/2026-04-17_B12_camera-verify-fix.md`.
- **[B13]** Docs cleanup — FLOW_UI_REFERENCE.md had "Known unknowns" placeholders that were replaced with live-DOM ground truth in same commit as Tier1 retest (`9facbe3`). **RESOLVED** inline with B11/B12 discovery session, no separate fix needed.
- ~~**[B14]** L2+ nav silent failure + wrong-tile click — found during `stash@{0}` triage (`docs/session-reports/2026-04-17_stash-triage_flow-refinements.md` §7 KEEP-2 + KEEP-3) — `flow/operations/_base.py::navigate_to_edit` (no post-nav verify) + `_click_video_tile` (generic `.first` click, not media_id-filtered) — **P1** (silent wrong-media edit mode → chain state diverges from page state; caller submits op against wrong video). Fix direction: add post-nav `/edit/` verify + media_id mismatch WARNING; rewrite `_click_video_tile` to 3-priority chain (JS match on media_id → `[data-tile-id]` → `video`).~~ ✅ **FIXED in commit `72e056b`** — cherry-picked KEEP-2 + KEEP-3 verbatim; H1 nav strategy reversal and H4 dead `_click_storyboard_video` helper explicitly REJECTED. Tests added in `tests/test_base.py` (7 cases: 3 nav-verify + 4 tile-click including JS contract trip-wire). Report: `docs/session-reports/2026-04-17_B14_base-nav-verify.md`.
- ~~**[B15]** Extend panel silent fail + submit diagnostics thin + Slate editor selector fragile — found during `stash@{0}` triage (`docs/session-reports/2026-04-17_stash-triage_flow-refinements.md` §7 KEEP-4 + KEEP-5 + KEEP-6) — `flow/operations/extend.py::extend_video` (no post-click panel verify) + `_type_extend_prompt` (relies only on DOM-order "last Slate editor") — **P1** (if Extend button click fails to open panel, the flow silently proceeds to submit which times out with no diagnosis; "last Slate" heuristic assumes specific DOM ordering). Fix direction: add `_verify_extend_panel` helper (polls for `editors>=2` OR `[data-scroll-state='START']`) + Step 3.5 call + RuntimeError; log URL + editor count on submit timeout; prepend scroll-state-aware Method 1 to `_type_extend_prompt`.~~ ✅ **FIXED in commit `caef3e9`** — cherry-picked KEEP-4 + KEEP-5 + KEEP-6 (Method 1 only); H5 (placeholder fallback removal) explicitly REJECTED — master's 4 `[placeholder*='next'|'tiếp'|'tiep' i]` + `[aria-label*='extend' i]` fallbacks preserved for defense-in-depth. Tests added in `tests/test_extend.py` (12 cases: 4 panel-verify + 4 extend-flow incl. submit diagnostics + 4 Method 1/2/fallback incl. placeholder-fallback preservation trip-wire). Report: `docs/session-reports/2026-04-17_B15_extend-panel-verify.md`.
- ~~**[B16]** `click_submit` gives up on selector if `.first` is disabled — found during `stash@{0}` triage (`docs/session-reports/2026-04-17_stash-triage_flow-refinements.md` §7.4 KEEP-7) — `flow/submit.py::click_submit` (calls `page.locator(selector).first` per selector; if `.first` is disabled due to loading state or duplicate DOM node, master clicks it silently via `force=True` or falls through to the next selector without probing enabled siblings) — **P1** (intermittent submit-click no-ops → `submit_with_confirmation` times out at 15s with no button-state breadcrumb). Fix direction: replace `.first` with `range(count) / .nth(i)` iteration, add `is_enabled(timeout=300)` probe alongside `is_visible`, preserve `_SKIP_PATTERN` filter inside the loop, add per-button DEBUG log with index + state; `submit_with_confirmation` (B8 commit `5c7d625`) explicitly untouched.~~ ✅ **FIXED in commit `004d8fb`** — cherry-picked KEEP-7 verbatim; `_SKIP_PATTERN` noise filter and Ctrl+Enter keyboard fallback preserved untouched; `submit_with_confirmation` wrapper untouched (`git diff flow/submit.py` confirms zero lines changed outside the `for selector in SUBMIT_SELECTORS:` body). Tests added in `tests/test_submit.py` (8 cases: 3 core iteration + 1 skip-pattern preservation + 1 per-button debug log + 1 keyboard fallback + 1 zero-count fall-through + 1 per-button exception resilience). Report: `docs/session-reports/2026-04-18_B16_submit-iterate.md`.
- ~~**[B17]** `select_model` toggle-closes LP panel in extend mode — found during `stash@{0}` triage (`docs/session-reports/2026-04-17_stash-triage_flow-refinements.md` §7 KEEP-1) — `flow/model_selector.py::select_model` unconditionally calls `_open_model_dropdown` at Step 2.7, which clicks the chip again and toggle-closes the panel if LP items are already visible (extend-mode scenario where Video-tab switch surfaces LP items directly) — **P1** (LP items hidden by toggle-close → retry loop + JS fallback both empty → `select_model` returns False → extend proceeds with non-LP remembered model → potential credit leak; same class as B8 LP credit leak with a different trigger). Fix direction: pre-check `page.locator(MODEL_ITEM_SELECTORS).filter(has_text=re.compile(r"Lower Priority", re.IGNORECASE)).count()` before the open call; if count > 0, skip the open call (leave `dropdown_opened=False`). Preserve master's `_close_model_panel(page, dropdown_was_opened)` signature + click-outside approach (stash H1/H3/H4 rejected).~~ ✅ **FIXED in commit `f5dab42`** — cherry-picked KEEP-1 verbatim; H1 (chip_handle capture), H3 (chip_handle threading through 4 call sites), H4 (`_close_model_panel` toggle-close rewrite) explicitly REJECTED — master's click-outside (Slate editor click) + Escape fallback from B8 commit `7245ae8` preserved. Tests added in `tests/test_model_selector.py` (7 cases: 4 behavioral incl. happy / else / resilience paths + 1 KEEP-1 source trip-wire + 2 rejected-hunks static contracts guarding against silent drift). Report: `docs/session-reports/2026-04-18_B17_lp-precheck.md`.
- ~~**[B18]** Homepage `+ New project` selector locale-hardcoded — found during Tier2 2026-04-18 live E2E on `ngoctuandt20` (VI profile) — `flow/operations/generate.py::text_to_video` step 2, `NEW_PROJECT_SELECTORS` list — **P0** (every T2V job on any non-EN Google account raised `RuntimeError("Failed to find '+ New project' button on Flow homepage")` at `generate.py:125`, blocking all Tier2 validation and all production jobs on VI/FR/JP/… accounts). Flow redirects `/fx/tools/flow` → `/fx/vi/tools/flow` based on Google account locale preference; `?locale=en` is ignored. Fix direction: rewrite selector list with icon-first (Material Icon ligature `add_2` inside `<i class="google-symbols">` is the stable locale-independent signal — same token on every locale by design of Material Icons), bilingual text fallbacks for defense-in-depth, generic `Create`/`Tạo` relegated to tail. Also gate `_dismiss_overlays` on actual overlay presence to avoid unnecessary Escape (B8 lesson). Live evidence: `docs/session-reports/2026-04-18_Tier2_e2e-live.md §7 [Primary]` + Chrome MCP DOM probe 2026-04-18.~~ ✅ **FIXED in commit `8dc357c`** — `NEW_PROJECT_SELECTORS` hoisted to module-level (shared with post-login retry path); top-3 entries icon-based; 5 bilingual text variants; `_dismiss_overlays` now probes for overlay presence before acting. Tests added in `tests/test_generate.py` (7 cases: 2 contract trip-wires preventing EN-only regression + icon-first ordering + generic-last ordering + retry-path list-sharing + source-level sentinels). Reference updated: `docs/FLOW_UI_REFERENCE.md §Homepage New Project Button` with full live-DOM evidence. Tier2 retry: see `docs/E2E_RESULTS_PHASE_A.md` Run 2. Report: `docs/session-reports/2026-04-18_B18_homepage-locale-fix.md`.
- ~~**[B19]** Aspect-ratio chip selector breaks when (a) default model is not `"Video"` OR (b) Radix trigger is pre-open — found during Tier2 2026-04-18 live E2E Runs 3-6 on `ngoctuandt20` after B18 unblocked code flow past homepage — `flow/operations/generate.py::_set_aspect_ratio` — **P0** (every `text_to_video(aspect_ratio="9:16")` raised `Locator.wait_for: Timeout 3000ms` on `[role="menu"][data-state="open"]` — halts before submit, blocks Tier 2 validation of B1 end-to-end and all portrait-video jobs on accounts whose default model is not exactly `"Video"`). Two-part root cause: (a) pre-B19 regex `re.compile(r"video.*x\d", re.IGNORECASE)` on chip `innerText` didn't match real live text `"🍌 Nano Banana Pro\ncrop_9_16\nx1"` (model-name varies); (b) chip's Radix trigger arrives with `data-state="open"` from a prior interaction (likely `flow/model_selector.py`'s `button:has-text('Video')` selector colliding with the aspect chip on current DOM) — unconditional `chip.click()` then TOGGLED the menu CLOSED. Phase A Tier 1 missed both because Tier 1's default model was exactly `"Video"` and the model-selector code path at that tag didn't pre-open the aspect chip. Fix direction: replace text regex with CSS `:has-text("crop_9_16"), :has-text("crop_16_9")` on `button[aria-haspopup="menu"]` (Material Icon ligature is locale- and model-independent), AND add pre-click guard that reads `chip_btn.get_attribute("data-state")` and skips click if already `"open"`. Both parts necessary — selector alone fails Runs 4/5, guard alone doesn't help if selector matches wrong element. Live evidence: `docs/E2E_RESULTS_PHASE_A.md` Runs 3-7.~~ ✅ **FIXED in commit `e1597b2`** — `_set_aspect_ratio` now uses icon-ligature CSS selector + Radix `data-state` pre-open guard. Tests added in `tests/test_aspect_ratio.py` (+2 cases: `test_portrait_skips_chip_click_when_already_open` behavioral trip-wire + `test_chip_selector_uses_icon_not_model_text` source trip-wire with 3 guarantees — old regex anti-pattern forbidden, ligature regex matches both aspect ligatures, ligature regex rejects unrelated Material Icon tokens). Live verdict: Tier 2 Run 7 ✅ — J1 `text-to-video` reached `completed` with `media_id=f2f736d2-…` + `project_url=https://labs.google/fx/tools/flow/project/f656f223-…`. Report: `docs/session-reports/2026-04-18_B19_aspect-chip-multiline.md`. Follow-up candidates (now closed): ~~**B20** (P2 proposed) — `flow/model_selector.py` uses `button:has-text('Video')` to open model dropdown; selector also matches aspect chip → origin of the pre-open state B19 now tolerates. Propose icon / `role=combobox` anchor.~~ ✅ **FIXED** — primary `'Video'` text collision absorbed by B26 (commit `d4fca1a`); three residual fuzzy `'Veo'` sites (`_open_model_dropdown` line 281 + `get_current_model` lines 556-557) converted to canonical `button[aria-haspopup='menu']:has(i:text-is('arrow_drop_down'))` + regex `.filter(has_text=re.compile(r"^Veo", re.IGNORECASE))` per `docs/FLOW_BUTTON_EXACT.md §1.6` in commit `0aa01b8`. Trip-wire added (`tests/test_model_selector.py::test_no_fuzzy_veo_selector`). Full suite 93 pass. ~~**B21** (P3 proposed) — stray bare `arrow_drop_down` stdout print in `flow/model_selector.py`.~~ ✅ **Self-resolved** — `grep "print(" flow/model_selector.py` at master `83f621f` returns zero hits; the stray `print()` is not present in the current code. Marked closed 2026-04-19 alongside the B20-final commit. Report: `docs/session-reports/2026-04-19_B20_B21_cleanup.md`.
- ~~**[B26]** Fuzzy `:has-text(...)` selectors on Material Icon tokens silently redirect /edit/ → /project/ between model selection and submit — found during Tier2 2026-04-19 live-extension E2E verification of the B22 fix on `ngoctuandt20` — three files: `flow/model_selector.py::_switch_to_video_tab` (JS fallback), `flow/submit.py::SUBMIT_SELECTORS`, `flow/operations/_base.py::click_action_button` — **P0** (every L2 extend job fails at submit stage with `new_api_calls=0, gen_id=None, url=…/project/<id>` in the timeout log — URL is the tell: submit was running on /project/ not /edit/). Root cause: `_switch_to_video_tab`'s JS fallback used `lower.includes('videocam')` which matched the /edit/ Camera mode-switch button (its `innerText` is `"videocam\nCamera"` — icon ligature + label concatenated). Clicking it toggled Camera mode on and Flow's SPA redirected /edit/ → /project/, killing the L2 composer before submit. Same class of fuzzy-text leak also lived in `SUBMIT_SELECTORS` (`button:has(i:has-text('arrow_forward'))` — could match `arrow_forward_ios` / `arrow_forward_circle`) and `click_action_button` (`button:has-text('Chèn')` — could match tooltip-reflected text). Fix direction: exact-text everywhere via Playwright `:text-is(...)` on Material Icon `<i>` children (icon ligatures are locale-independent and stable by Google design) + `_switch_to_video_tab` JS fallback blacklists mode-switcher button titles (`'Camera'`/`'Mở rộng'`/`'Chèn'`/`'Xoá'` + EN variants) before matching + `click_action_button` switches to `button[title='{text}']` primary + `button:has(i:text-is('{icon}'))` icon-fallback via `_MODE_ICON_BY_TITLE` dict. Remove `flow/submit.py::_SKIP_PATTERN` (redundant under exact-text selector) and add `scope=` param to `click_submit`/`submit_with_confirmation` for composer-scoped search on /edit/. Live evidence: worker logs + Chrome extension live-DOM probe 2026-04-19.~~ ✅ **FIXED in commit `d4fca1a`** — 3 files (submit.py, model_selector.py, _base.py) switched to exact-text Material Icon ligature matching; JS fallback in `_switch_to_video_tab` now blacklists mode-switcher titles. Tests in `tests/test_submit.py` reworked: removed 3 obsolete tests (`_SKIP_PATTERN` filter, `SUBMIT_SELECTORS[1]` fall-through), added 2 B26 tests (scope-param behavior + source trip-wire forbidding fuzzy `:has-text('arrow_forward')` + pinning `len(SUBMIT_SELECTORS) == 1`). Full-suite 92 pass (was 93 pre-B26; net −1) + zero DeprecationWarning under `-W error::DeprecationWarning`. Live verdict: L2 extend + L2 insert both produced real videos on `ngoctuandt20` VI profile via Chrome extension (extend: new clip at 18% → composer cleared → clip in history; insert: "a small yellow pencil" visibly inserted over red cube after ~8s). Incidentally absorbs B20 (aspect-chip vs model-chip collision — both now use distinct `aria-haspopup='menu'` + exact icon anchors). Report: `docs/session-reports/2026-04-19_B26_submit-and-model-exact-text.md`. SPEC §D.4 B26 FIXED entry + TOC/header B1-B22 → B1-B26.
- ~~**[B22]** L2+ claim does NOT inherit `project_url` / `media_id` / `edit_url` from parent — found during Tier2 2026-04-18 Run 8 on `ngoctuandt20` after B19 unblocked J1 — `server/db/job_store.py::claim_next_job` line 264-282 — **P0** (every multi-level chain breaks: L2 jobs claim with `project_url=NULL` → `navigate_to_edit` has no target → worker fails; blocks ALL extend/insert/remove/camera chain operations). Current code SELECTs only `parent.profile` and UPDATEs only `jobs.profile` — `project_url`/`media_id`/`edit_url` remain NULL even though parent has them post-completion (INV-3 Store Everything + INV-2 Navigate by edit_url both depend on this inheritance). Fix direction: extend parent SELECT to fetch `project_url, media_id, edit_url` + UPDATE jobs SET to populate those 3 fields from parent values (atomic, same transaction as profile inherit + B6 profile-row mirror). Live evidence: `docs/session-reports/2026-04-18_B19_aspect-chip-multiline.md §8` + Run 8 blocker description.~~ ✅ **FIXED in commit `0637c92`** — parent SELECT now fetches all 4 fields (`profile, project_url, media_id, edit_url`); priority-1 UPDATE populates the 3 target-context fields from parent in the same `BEGIN IMMEDIATE` transaction as `profile` inherit + B6 profile-row mirror. L1 priority-2 branch untouched (no parent to inherit from). Pure propagation — NULL in parent column → NULL in child, no synthesis. Tests added in `tests/test_claim_algorithm.py` (4 cases: RED→GREEN core inherit + parent-wins-on-overwrite + L1-no-inherit blast-radius guard + NULL-preserving pure-propagation contract). Full-suite 93 pass (was 89 + 4 new) + zero DeprecationWarning under `-W error::DeprecationWarning`. Tier 2 Run 9 verdict: see `docs/E2E_RESULTS_PHASE_A.md`. SPEC.md §D.4 B22 FIXED entry + INV-3 "Claim-time propagation" note added. Report: `docs/session-reports/2026-04-18_B22_l2-inheritance.md`.
- ~~**[B28]** Action sidebar (Insert/Camera/Remove) absent on extend-output `/edit/{new_media}` URL — found during Tier2 2026-04-19 Tests 2/3/4 on `ngoctuandt20` — `/edit/{media_id}` page rendered after `extend-video` completion has `Video element loaded` but zero matches for mode-switch buttons (`button[title='Insert']`, `button:has(i:text-is('add_box'))`, similarly Camera/Remove) — **P0** (any chain with `extend-video` in the middle is blocked at the next L2+ op: J3 insert failed `RuntimeError: Failed to find Insert button`; diagnostic camera on extend-output hit `Failed to find Camera button`). Contrast: Run 10 ran t2v → camera → insert successfully, so sidebar renders on t2v and camera outputs. Delta is extend-output layout specifically. Needs: Tier 1 DOM-probe session to compare `/edit/` rendered DOM after t2v vs camera vs extend, identify the differentiating signal, then either wait for sidebar render with a stable selector or use the extend-child variant of the composer. Live evidence: `docs/session-reports/2026-04-19_tests_2-3-4_ui.md §7 B28`.~~ ✅ **RE-DIAGNOSED + FIXED** in combined B28/B29/B30 commit (2026-04-19): DOM probe `docs/session-reports/2026-04-19_B28_B29_probe.md` revealed the sidebar was NOT "absent" — buttons render with `[disabled]` ("extend-child lockout"). Root fix at claim layer (B30 walk-up: L2+ inheritance skips `extend-video` ancestors so the worker navigates to a stable non-extend parent URL where buttons are enabled). Defensive diagnostic at worker layer: `click_action_button` now raises `"extend-child lockout"` with B22-inheritance hint on `is_enabled=False`. Session report: `docs/session-reports/2026-04-19_B30_B28_B29_combined.md`.
- ~~**[B29]** L1 `/edit/{media_id}` goes stale after a sibling `extend-video` completes — found during Tier2 2026-04-19 Tests 2/3/4 Test 4 (3 camera-move presets on J1 L1 parent after J2 extend completed) — `navigate_to_edit(J1.edit_url)` now hits 15s video-element timeout, icon-fallback Camera click "succeeds" but preset grid absent; all 3 preset click+verify calls `RuntimeError: Failed to find camera preset: <direction>` — **P0** for parallel L2 fork patterns (any L2 sibling launched on an L1 parent after another L2 has completed off the same L1 fails). Works for purely-serial chains where each L2 operates on its direct parent's output (Run 10 pattern). Fix direction: unclear until Tier 1 DOM probe confirms whether Flow SPA invalidates old `/edit/{media_id}` URLs after a sibling op, or whether the L1 media_id state transitions post-extend (related to INV-5 revision below). Live evidence: same report §7 B29.~~ ✅ **RE-DIAGNOSED + FIXED** in combined B28/B29/B30 commit (2026-04-19): DOM probe confirmed Flow SPA strips `/edit/` segment from stale L1 `/edit/{old_media}` URLs after a sibling extend consumes that media, leaving the page on `/project/{id}`. Pre-B29 `navigate_to_edit` silently fell through to tile-click, potentially landing on any sibling video and violating INV-5. Root fix at claim layer (same B30 walk-up — when the chain needs L1's original URL, B30 ensures inheritance is valid; for fork patterns the walk-up still lands on L1's URL and B29's strip symptom is eliminated as long as B30 re-hooks the live media_id). Defensive diagnostic at worker layer: `navigate_to_edit` final URL check now raises `"SPA stripped /edit/ segment"` with B22-inheritance hint. Session report: `docs/session-reports/2026-04-19_B30_B28_B29_combined.md`.
- ~~**[INV-5 re-revision]** `extend-video` empirically mints a NEW `media_id` (Tests 2/3/4 J1 `6842325d-…` → J2 output `1a6e3b77-…`). SPEC §A.1 INV-5 revised 2026-04-19 `3d7b884` said "extend/insert/remove preserve; camera mints new" — empirical contradiction for extend. Needs session: (a) re-probe insert + remove for the same behavior (not yet verified — Tests 2/3/4 blocked J3/J4 at B28 before those could produce media_ids), (b) update SPEC §A.1 INV-5 matrix row for extend from "Preserved" → "Mints new", (c) audit B22 inheritance semantics to confirm child L2 claiming a parent-extend gets the extend's new media_id (parent.media_id in DB is the post-op final — likely already correct). Live evidence: report §7.3.~~ ✅ **RE-REVISED** in combined B28/B29/B30 commit (2026-04-19): SPEC §A.1 INV-5 matrix updated — extend-video row moved from "Preserved" → "**NEW uuid**" with "**Grandparent** (B30 walk-up)" as the chain-inherits-from column. Insert/remove preservation claim retained but marked **TBD (not empirically re-verified post-2026-04-19)** pending a Tier-2 insert/remove-chain run. Chain semantics resolved structurally at claim layer via B30 walk-up so the worker never sees the extend-output as a direct parent target. Sub-tasks (a)(b)(c) from the original queue item: (b) done; (c) done; (a) still pending a dedicated Tier-2 insert/remove run — tracked under the INV-5 TBD row in SPEC §A.1.
- **[B30]** ~~L2+ claim does NOT skip `extend-video` ancestors when inheriting `media_id` / `edit_url` — found during Tier2 2026-04-19 Tests 2/3/4 + B28/B29 DOM probe — `server/db/job_store.py::claim_next_job` line ~264-307 (B22 inherit block) — **P0** (every chain with `extend-video` in the middle inherits the extend-output URL, whose Insert/Remove/Camera buttons are disabled [B28] and whose L1 sibling URL gets SPA-stripped [B29]; pre-B30 both symptoms were unrecoverable at the worker layer). INV-5 re-revision (extend mints NEW media_id) converts what was a "preservation" assumption into a structural chain-inheritance problem: the child L2+ must NOT land on the extend-output, it must land on the nearest non-extend ancestor. Fix: extend claim-layer B22 logic with a walk-up loop that follows `parent_job_id` while `parent.type == 'extend-video'`, stopping at the first non-extend ancestor or the root. Profile + project_url still come from the direct parent (both invariant across the chain). Safety-bound the walk at 16 iterations. Live evidence: `docs/session-reports/2026-04-19_B28_B29_probe.md` + Tests 2/3/4 behavioral description.~~ ✅ **FIXED** in combined B28/B29/B30 commit (2026-04-19) — walk-up loop added to `claim_next_job` L2+ branch; `media_id` + `edit_url` now inherited from the first non-`extend-video` ancestor. Non-extend parents (camera-move / insert-object / remove-object / text-to-video) keep B22 baseline (loop terminates at direct parent on iteration 0). Tests added in `tests/test_claim_algorithm.py` (3 cases: single extend ancestor, chain of 2 extends, non-extend parent regression guard; 4 pre-existing B22 tests still pass unmodified). Full-suite 107 pass (was 100 + 7 new B28/B29/B30) + zero DeprecationWarning under `-W error::DeprecationWarning`. SPEC §A.1 INV-5 matrix revised + §D.4 B28/B29/B30 FIXED entries + TOC/header B1-B27 → B1-B30. Report: `docs/session-reports/2026-04-19_B30_B28_B29_combined.md`.
- **[B32]** ~~B30 walk-up covers `edit_url` too → navigating grandparent L1 `/edit/` after sibling extend still triggers B29 SPA-strip; B29 guard raises with no recovery path — found during supervisor-led live probe 2026-04-19 (project `513d580b-…`) post-B31 — `server/db/job_store.py::claim_next_job` + `flow/operations/_base.py::navigate_to_edit` — **P0** (architectural refinement of B30; chains with extend-in-middle still blocked even with B30 walk-up). Live DOM probe found concrete workaround: right history panel renders each clip as `<div data-tile-id="fe_id_{media_id}">`; dispatching full MouseEvent sequence (pointerdown → mousedown → pointerup → mouseup → click) on target tile flips disabled sidebar → enabled WITHOUT changing `page.url`. Plain `tile.click()` insufficient (Flow styled-components). Fix: (a) split B22/B30 in `claim_next_job` — `edit_url` from direct parent (B22, never stale), `media_id` walks up (B30, semantic target); (b) new `_activate_clip_tile(page, media_id)` helper dispatches the MouseEvent sequence via `page.evaluate`; (c) `navigate_to_edit` invokes helper when URL media ≠ target media. Live evidence: `docs/session-reports/2026-04-19_Tier2_Run12_B32_verify.md` § J3/J4/J5 worker log excerpts.~~ ✅ **FIXED in commit `b4e99f6`** (2026-04-19). Tests: `tests/test_base.py` test_navigate_warns_on_media_id_mismatch rewritten as test_navigate_activates_target_tile_on_media_mismatch + 3 new tests for `_activate_clip_tile` (dispatch contract, missing tile, empty media_id). `tests/test_claim_algorithm.py` B30 tests updated for B32 split (`edit_url` = direct parent, `media_id` walks up). Full-suite 110 pass + zero DeprecationWarning. Tier 2 Run 12 verified live — 5-op chain t2v→extend→insert→remove→camera all completed on ngoctuandt20, zero "Mode button disabled" errors. SPEC §D.4 B32 FIXED entry + §A.1 INV-5 matrix refined with camera-move context-dependency note. `docs/FLOW_BUTTON_EXACT.md` §5.1a "parent-clip select workaround IMPLEMENTED" replaces "not implemented" wording.
- **[B33]** Camera-move `media_id` behavior is context-dependent — does NOT always mint a NEW uuid as earlier INV-5 revision asserted — found during Tier 2 Run 12 (2026-04-19) — SPEC.md §A.1 INV-5 matrix + CLAUDE.md §4 Critical fields + FLOW_ENGINEERING_NOTES.md INV-5 row — **P2** (docs-only; engine already handles both modes correctly via `finalize_operation` re-extraction from `page.url`, INV-3 Store Everything still holds). Empirical observations: Run 10 J1 t2v → J2 camera-move = **NEW uuid** (camera at L2 direct off L1). Run 12 J4 remove → J5 camera-move = **preserved in-place** (camera at L5 after B32 tile-activation pinned URL to the active clip). Pattern: camera mints new when Flow surfaces a fresh `/edit/{new}` URL transition (early-chain, URL not pinned); preserves when URL already pins to an active clip via B32 tile activation (deep-chain). Not an engine bug — Flow's SPA genuinely treats camera in these two contexts differently. Fix: docs-only — INV-5 matrix updated with "context-dependent" wording + explicit Run 10 vs Run 12 evidence citations + consequence notes that `finalize_operation` re-extraction covers both cases correctly. ✅ **FIXED in commit `120f5e0`** (docs only).
- **[B34]** 1080p download never succeeds live — `_upsampled` poll window 30s (3 retries × 10s) too short for Flow's 1-3 min upscale latency; every Tier 2 run falls through to 720p — found via `downloads/` folder audit (zero `_1080p_` files across Run 10 + Run 12 + Tests 2/3/4) — `flow/download.py:14-16` constants + `_api_download_with_retry` hardcoded `max_retries=3` — **P1** (720p fallback still functional but user-facing quality expectation is 1080p; code claims to try for it but never succeeds). Fix: bump `UPSCALE_POLL_INTERVAL` 10→15s + add `UPSCALE_MAX_RETRIES=12` env-overridable constant + `_api_download_with_retry(max_retries=None)` defaults to the constant. Total poll window 15 × 12 = 180s (3 min) matches observed Flow upscale envelope. Tests: `tests/test_download.py` +2 (default window ≥120s source trip-wire + env-override contract). ✅ **FIXED in commit `d454155`**.
- **[B35]** Engine relied on Flow's per-account x1/x2/x3/x4 default — `ngoctuandt20` default x2 silently doubled LP credit on every L1 submit (Run 10 + Run 12 + earlier Tier 2 all burned 2×) — found 2026-04-19 when user flagged a stale-job screenshot showing `Video x2 @9%/9%` on composer — `flow/operations/generate.py::text_to_video` had no code path interacting with Quantity tablist (row 4 of model chip panel per `docs/FLOW_UI_REFERENCE.md` §Model Chip Panel) — **P0** (silent credit leak; 2× LP per L1 submit undetected because DB only stored 1 of 2 minted media_ids — ambiguity hid). Fix: new `_set_output_count(page, count=1)` helper mirroring `_set_aspect_ratio` Radix pattern (open chip → click `[id$="-trigger-1"]` → wait `data-state="active"` → click-outside → verify chip innerText contains `x1`); Step 4.5 injection in `text_to_video` between aspect and prompt. Scope L1-only (L2 composers don't expose Quantity). Tests: `tests/test_output_count.py` +5 (happy path + `data-state='open'` skip-guard + verify-fail WARNING + ValueError on `count∉{1..4}` + source trip-wire that `text_to_video` body contains `_set_output_count(`). Memory `feedback_output_count_x1.md` added. ✅ **FIXED in commit `dc486a7`**.

---

## §9 — Estimate tổng

| Bug | Estimate |
|---|---|
| B7 | 5 phút |
| B9 | 2 giờ |
| B8 | 45 phút |
| B5 | 1 giờ |
| B6 | 2 giờ |
| B1 | 3-4 giờ (inc. research) |
| B2 | 4 giờ (inc. research) |
| B3 | 3 giờ (inc. research) |
| B4 | 45 phút (post-Phase-A, 2026-04-18) |
| **Buffer** (debug, test fail, rework) | 30% |
| **Manual E2E §5** | 2-3 giờ |
| **TOTAL** | **~22-26 giờ** ≈ **3-4 ngày làm việc** |

(Không tính thời gian wait browser test và user review giữa bug.)
