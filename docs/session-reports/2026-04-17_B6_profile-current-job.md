# Session Report — `B6` track `Profile.current_job_id` on claim/terminal

---

## 1. Metadata

| Field | Value |
|---|---|
| Task ID | `B6` |
| Task type | bug-fix |
| Session started | 2026-04-17 09:35 |
| Session ended | 2026-04-17 09:55 |
| Duration actual | `20m` |
| Duration estimate | `2h` (from WORKPLAN.md §3 B6) |
| Worker | Claude Sonnet 4.6 |
| Branch | `claude/serene-taussig-7a8ddf` (worktree) |

---

## 2. Commits landed

```
<this-commit>  fix(profile): track current_job_id on claim/complete (B6)
```

Supervisor to replace `<this-commit>` placeholder in `docs/SPEC.md §D.4 B6` after merge.

---

## 3. Files changed

```
server/db/job_store.py                                  +30 / -7     (TERMINAL_STATES hoist + 2 profile UPDATE in claim + 1 profile UPDATE in update_job)
tests/test_profile_store.py                             +92 / -0     (NEW — 3 B6 test cases)
docs/SPEC.md                                            +3  / -4     (§C.2 annotation replaced; §D.4 B6 strike-through + details)
docs/session-reports/2026-04-17_B6_profile-current-job.md  +N / -0   (this report)
```

Tổng: `4 files, +125 / -11` (report file excluded from diff metric).

---

## 4. Tests

| Test | Result | Notes |
|---|---|---|
| `tests/test_profile_store.py::test_profile_current_job_set_on_claim` | ✅ pass | L1 claim path stamps profile.current_job_id with claimed job.id |
| `tests/test_profile_store.py::test_profile_current_job_cleared_on_completion` | ✅ pass | status=completed clears the pointer |
| `tests/test_profile_store.py::test_profile_current_job_not_cleared_on_running` | ✅ pass | status=running leaves pointer alone |

- Tổng: `13 pass / 0 fail / 0 skipped` (10 cũ + 3 mới).
- Test commands used:
  - `pytest tests/test_profile_store.py -v` → 3/3 pass
  - `pytest tests/ -v` → 13/13 pass
  - `pytest tests/ -W error::DeprecationWarning` → 13/13 pass, no warnings promoted to errors
- RED proof before fix: `test_profile_current_job_set_on_claim` + `test_profile_current_job_cleared_on_completion` failed (current_job_id stayed at its initial value because no code path ever wrote to it server-side). The third test passed trivially because nothing clears the field in either direction — post-fix it still passes for the right reason (running ∉ TERMINAL_STATES → no clear branch taken).
- Coverage delta: not measured (coverage tooling still deferred post-Phase-A per B9 done-criteria).

---

## 5. SPEC.md update

- [x] §C.2 `⚠️ B6: không reset sau complete` replaced with behavior comment (`set on claim, cleared on terminal (B6)`).
- [x] §D.4 B6 strike-through (~~…~~ ✅ FIXED `<this-commit>`) with full triệu chứng/fix detail + guard test reference.
- [x] Commit hash placeholder `<this-commit>` present in §D.4 — supervisor to replace after merge.

Commit hash for SPEC.md update: same as code fix commit (single commit per §1.5 WORKPLAN rule).

---

## 6. Invariants & rules verified

- [x] INV-1 Account Binding — not touched; claim still inherits parent profile in L2+ branch
- [x] INV-2 Navigate by `edit_url` — not touched
- [x] INV-3 Store Everything — **reinforced**: profile.current_job_id now tracked server-side across claim + terminal, visible to all workers/UI
- [x] INV-4 Serial per Project — not touched; claim_next_job's NOT EXISTS project_url guard untouched
- [x] INV-5 media_id stable — not touched
- [x] R-CODE-3 Locale-Independent — not touched (no UI/locale code)
- [x] R-CODE-10 No `datetime.utcnow()` — used existing `_now_iso()` helper (tz-aware post-B8); new `last_used_at` writes use the same `now` already computed at top of `claim_next_job`
- [x] R-CC-1 KHÔNG restructure kiến trúc — 4 localised insertions (1 module constant, 2 profile UPDATE in claim branches, 1 profile UPDATE in update_job) + 1 variable hoist for status normalisation. No file/module reorganisation.

No intentional violations.

---

## 7. Issues / Decisions

### Vấn đề phát sinh
- None. RED → GREEN on first implementation pass. Third test was already passing pre-fix because the pre-fix behavior happened to satisfy the "non-terminal must not clear" invariant vacuously (nothing cleared in any direction). Kept the test — it guards the correct post-fix behavior (terminal-only clear) against future regressions where someone might naively clear on every status change.

### Quyết định đã đưa (judgment calls)

- **Hoisted `TERMINAL_STATES` to a module-level `frozenset` constant**. B5 (commit `4d24c10`) left this as an inline set literal in `update_job` with a handoff note that B6 would promote it. Supervisor brief explicitly pre-authorised this as "DRY cần thiết, không phải tiện tay fix". Used `frozenset` over `set` because it is immutable — callers cannot mutate a shared constant. Single source of truth for both B5 completed_at auto-stamp and B6 profile-clear paths.
- **Normalise `status_value` once at top of `update_job`**. Original B5 code computed `status_value` only inside `if new_status is not None`. B6 needs the same derived value for the profile-clear branch after the jobs UPDATE. Hoisting the normalisation one level up (with `status_value: Optional[str] = None` as default) lets both B5 and B6 checks reuse `status_value in TERMINAL_STATES` — which short-circuits cleanly when `status` isn't in the update payload (None ∉ frozenset of strings).
- **Profile UPDATE in claim happens in the same `BEGIN IMMEDIATE` transaction as the jobs UPDATE**. `claim_next_job` already wraps everything in a single `async with get_db()` + `BEGIN IMMEDIATE` / `db.commit()` envelope, and I added the profile UPDATE before the existing `await db.commit()` calls in both priority branches. This guarantees atomicity: either both rows move together or neither does. Prevents a window where a concurrent reader could see a job in `claimed` state but the profile row still showing `current_job_id=NULL`.
- **Profile UPDATE in update_job sits before `await db.commit()` too**. Same reasoning — terminal transitions commit jobs + profile together. Placed the UPDATE before the `cursor.rowcount == 0` early return is evaluated, but this is safe: if the jobs row doesn't exist (`rowcount == 0`), the profile UPDATE is a no-op because `WHERE current_job_id = <nonexistent-job-id>` matches nothing. No stray profile clear for non-existent jobs.
- **Also set `worker_id` and `last_used_at` on the profile during claim** (per WORKPLAN §3 B6 spec literal). This is broader than just `current_job_id` but the supervisor brief explicitly listed those three columns. `last_used_at` reuses the same `now` string already computed at the top of `claim_next_job` — no extra `_now_iso()` call. Aligns profile-row freshness with the claim event.
- **Did NOT touch `server/models/profile.py`**. The default_factory=datetime.utcnow residual there is B10 scope (per WORKPLAN §8 "Discovered during work"); the B6 fix is pure server/db layer.
- **Did NOT touch `worker/profile_manager.py`**. Worker-side in-memory `mark_available` is a separate concern (worker's own coordination) — B6 scope per WORKPLAN is server-side DB truth. The in-memory side still works correctly because worker calls `update_job` on completion, which now transitively clears the DB profile row.
- **No-op on missing profile row is intentional, not ignored**. If a worker calls claim with a profile name that has no row in `profiles` (e.g. test environment or unregistered profile), the `UPDATE profiles WHERE name = ?` silently matches zero rows. The jobs UPDATE still succeeds and the claim returns normally. Raising would break existing tests (test_job_store cases don't pre-create profiles) and the worker's in-memory profile manager is the authoritative source for "can this worker use this profile". Added a comment in the update_job branch noting this is intentional.

### Bug candidates phát hiện NHƯNG KHÔNG fix (out of scope)
- None discovered this session. B10 remains the only open "discovered-during-work" item.

---

## 8. Handoff notes

- Workdir state: 2 prod/test files modified + 1 new test file + 1 new report file + SPEC.md — all within the session brief whitelist. Ready to commit. `stash@{0}` ("WIP: flow refinements…") from master still intact (not touched, not popped, not dropped).
- Env: standard pytest setup; no new dependencies.
- Next bug in queue = **B1 — Aspect ratio stub** (per WORKPLAN §3 B1). Caveats for next session:
  - B1 has a research phase (§3 B1 "B1a: Research + document aspect ratio UI") — needs manual browser interaction on Flow homepage before code changes land.
  - B1 likely wants to peek `stash@{0}` for prior UI work on model/aspect ratio selectors (note: peek only via `git stash show -p stash@{0}` — do NOT pop/drop in an exploratory read).
  - TERMINAL_STATES is now a module-level constant in `server/db/job_store.py:12`; any future refactor touching job-state sets should reuse it.
- B10 (Pydantic `default_factory=datetime.utcnow` residual) still deferred; not blocking B1.

---

## 9. Done criteria checklist

Từ `docs/WORKPLAN.md §3 B6` + supervisor brief:

- [x] `claim_next_job` updates profile in priority-1 branch (`server/db/job_store.py:283-292`)
- [x] `claim_next_job` updates profile in priority-2 branch (`server/db/job_store.py:328-337`)
- [x] `update_job` clears profile on terminal (`server/db/job_store.py:186-195`)
- [x] TERMINAL_STATES hoisted to module-level frozenset constant (`server/db/job_store.py:12`)
- [x] 3 test cases pass (`tests/test_profile_store.py`)
- [x] Full suite `pytest tests/ -v` → 13/13 pass (10 existing + 3 new)
- [x] `pytest tests/ -W error::DeprecationWarning` — clean
- [x] SPEC.md §C.2 cập nhật (`⚠️ B6` annotation replaced with behavior comment)
- [x] SPEC.md §D.4 B6 strike-through với placeholder `<this-commit>`
- [x] Commit message format `fix(profile): track current_job_id on claim/complete (B6)` + `Closes #B6`
- [x] Không chạm file ngoài whitelist (`server/db/job_store.py`, `tests/test_profile_store.py`, `docs/SPEC.md`, `docs/session-reports/...`)
- [x] `stash@{0}` vẫn còn (verified via `git stash list` pre-commit)

---

_Sign-off: ✅ Ready for supervisor review._
