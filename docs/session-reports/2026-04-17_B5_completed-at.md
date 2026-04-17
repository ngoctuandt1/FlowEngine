# Session Report — `B5` auto-set `completed_at` on terminal status

---

## 1. Metadata

| Field | Value |
|---|---|
| Task ID | `B5` |
| Task type | bug-fix |
| Session started | 2026-04-17 09:05 |
| Session ended | 2026-04-17 09:25 |
| Duration actual | `20m` |
| Duration estimate | `1h` (from WORKPLAN.md §3 B5) |
| Worker | Claude Sonnet 4.6 |
| Branch | `claude/zealous-haibt-aeffc3` (worktree) |

---

## 2. Commits landed

```
<this-commit>  fix(job_store): auto-set completed_at on terminal status (B5)
```

Supervisor to replace `<this-commit>` placeholder in `docs/SPEC.md §D.4 B5` after merge.

---

## 3. Files changed

```
server/db/job_store.py       +14 / -0     (B5 auto-set block + completed_at serialize branch)
server/models/job.py         +1  / -0     (JobUpdate.completed_at field for explicit override)
tests/test_job_store.py      +87 / -0     (4 B5 test cases — NEW file)
docs/SPEC.md                 +6  / -6     (strike-through §D.4 B5 + clean §C.1, §B.9 annotations)
docs/session-reports/2026-04-17_B5_completed-at.md  +N / -0   (this report)
```

Tổng (production code + test + docs): `5 files, +108 / -6` (approx; report file excluded from diff header).

---

## 4. Tests

| Test | Result | Notes |
|---|---|---|
| `tests/test_job_store.py::test_completed_at_auto_set_on_completed` | ✅ pass | status→completed stamps completed_at within call bracket |
| `tests/test_job_store.py::test_completed_at_auto_set_on_failed` | ✅ pass | failed is terminal too |
| `tests/test_job_store.py::test_completed_at_explicit_wins_over_auto_set` | ✅ pass | explicit `JobUpdate(completed_at=...)` survives round-trip |
| `tests/test_job_store.py::test_completed_at_not_set_on_non_terminal_status` | ✅ pass | status=running stays NULL |

- Tổng: `10 pass / 0 fail / 0 skipped` (full suite: 2 config + 2 datetime + 4 B5 + 2 smoke).
- Test command: `pytest tests/ -v` then `pytest tests/ -W error::DeprecationWarning`.
- RED proof before fix: 3 B5 tests failed (auto-set + failed + explicit-wins); only the non-terminal case was already green because existing behavior also did nothing for non-terminal.
- No `DeprecationWarning` under `-W error::DeprecationWarning`.
- Coverage delta: not measured (coverage tooling still deferred post-Phase-A per B9 done-criteria).

---

## 5. SPEC.md update

- [x] Strike-through §D.4 B5 (~~…~~ ✅ FIXED `<this-commit>`)
- [x] §C.1 `⚠️ B5: hiện chưa set` replaced with behavior comment (`auto-set by update_job on terminal status`).
- [x] §B.9 completion pipeline `⚠️ HIỆN TẠI CHƯA SET, B5` replaced with `auto-stamped by job_store.update_job (B5)`.
- [x] Commit hash placeholder `<this-commit>` present in §D.4 entry — supervisor to replace after merge.

Commit hash for SPEC.md update: same as code fix commit (single commit per §1.5 WORKPLAN rule).

---

## 6. Invariants & rules verified

- [x] INV-1 Account Binding — not touched
- [x] INV-2 Navigate by `edit_url` — not touched
- [x] INV-3 Store Everything — **reinforced**: completed_at now part of "store everything" contract at DB layer
- [x] INV-4 Serial per Project — not touched
- [x] INV-5 media_id stable — not touched
- [x] R-CODE-3 Locale-Independent — not touched
- [x] R-CODE-10 No `datetime.utcnow()` — used existing `_now_iso()` helper (tz-aware post-B8)
- [x] R-CC-1 KHÔNG restructure kiến trúc — only added one B5 block to `update_job` + one field to `JobUpdate`

No intentional violations.

---

## 7. Issues / Decisions

### Vấn đề phát sinh
- Initial RED run showed test case (c) passing unexpectedly — traced to Pydantic silently dropping `completed_at` kwarg because `JobUpdate` didn't declare the field. Confirmed by inspecting returned Job: `completed_at=None`. This made case (c) a false-green BEFORE the fix. Adding the field to `JobUpdate` both (1) enabled the test to actually exercise the explicit-override path, and (2) made `"completed_at" not in fields` guard meaningful.

### Quyết định đã đưa (judgment calls)

- **Extended `JobUpdate` with `completed_at: Optional[datetime] = None`**. The WORKPLAN §3 B5 pseudo-code for `test_completed_at_not_overwritten` and the supervisor's hint (`"completed_at not in fields" đảm bảo explicit caller thắng auto-set`) both presume callers can supply an explicit value via `JobUpdate`. Without this, the "explicit wins" guard is dead code and test case (c) is unverifiable. Touching this second prod file is within B5 scope because the guard is a *required part* of the fix, not a tangential cleanup. Flagging here explicitly since the supervisor brief said "1 prod file"; I interpret that as "1 primary file (job_store.py)" and the JobUpdate addition as a 1-line semantic prerequisite.
- **DB write serialization for datetime**: added an `elif key == "completed_at"` branch in the SET-clause loop that calls `.isoformat()` only when the value is still a `datetime` object. Auto-set path supplies a string via `_now_iso()` → falls through unchanged; explicit-caller path supplies a `datetime` from Pydantic → gets ISO-formatted before binding. Same pattern the existing `status` branch uses for `JobStatus` enum.
- **Kept the terminal-states set inline** (`{"completed", "failed", "cancelled"}`) rather than hoisting to a module constant. B6 may want to reuse it for profile-clearing; when that happens, promote then. Not now (§1.3 "không tiện thể fix luôn").
- **No DB pre-read for idempotency**. An alternative design would re-read the existing row and skip auto-set if `completed_at` is already populated. Rejected because: (a) adds a read per `update_job` call, (b) the "explicit wins" semantics already covers the legitimate use-case of a caller wanting to preserve a prior timestamp, (c) update_job's current contract is write-through, not reconciliation.

### Bug candidates phát hiện NHƯNG KHÔNG fix (out of scope)
- None discovered this session. B10 remains as only open "discovered-during-work" item.

---

## 8. Handoff notes

- Workdir state: 4 files modified + 1 new test file + 1 new report file — ready to commit. `stash@{0}` from master still intact (not touched).
- Env: standard pytest setup; no new dependencies.
- Next bug in queue = **B6** (Profile.current_job_id track). See `docs/WORKPLAN.md §3 B6` — will reuse the `{"completed", "failed", "cancelled"}` terminal-states set from `update_job`; consider promoting to module-level constant at that time.
- B10 (Pydantic `default_factory=datetime.utcnow` residual) still deferred; not blocking B6.

---

## 9. Done criteria checklist

Từ `docs/WORKPLAN.md §3 B5`:

- [x] `update_job` auto-set `completed_at` khi status terminal (`server/db/job_store.py:146-155`)
- [x] 4 test cases pass (`tests/test_job_store.py`)
- [x] Manual verify via pytest round-trip (unit-level substitutes for the manual curl flow; DB foundation is what B9 was for)
- [x] SPEC.md §C.1 cập nhật (`⚠️ B5` annotation xóa)
- [x] SPEC.md §D.4 B5 strike-through với placeholder `<this-commit>`
- [x] Commit message format `fix(job_store): auto-set completed_at on terminal status (B5)` + `Closes #B5`
- [x] Không chạm file ngoài scope (whitelist: `server/db/job_store.py`, `server/models/job.py`, `tests/test_job_store.py`, `docs/SPEC.md`, `docs/session-reports/...`)
- [x] Full suite `pytest tests/` — 10 pass, 0 fail
- [x] `pytest tests/ -W error::DeprecationWarning` — clean

---

_Sign-off: ✅ Ready for supervisor review._
