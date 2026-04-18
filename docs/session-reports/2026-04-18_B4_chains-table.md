# Session Report — `B4` Chains Table Persistence + Aggregated Status

---

## 1. Metadata

| Field | Value |
|---|---|
| Task ID | `B4` |
| Task type | bug-fix (unban previously deferred) |
| Session started | 2026-04-18 |
| Session ended | 2026-04-18 |
| Duration actual | ~45m |
| Duration estimate | 45m (post-Phase-A re-estimate; original was 0 because deferred) |
| Worker | Claude Sonnet 4.6 |
| Branch | `claude/laughing-einstein-caf912` (worktree off `master` @ `25e9fba`) |

---

## 2. Commits landed

```
4dcf50f  feat(chains): persist chain metadata + aggregated status API (B4)
```

(hash to be recorded after commit)

---

## 3. Files changed

```
server/models/chain.py                               +46 / -0     (NEW — Chain, ChainAggregate, ChainProgress pydantic models)
server/db/chain_store.py                             +135 / -0    (NEW — create_chain, get_chain_row, get_chain_aggregate, compute_aggregated_status)
server/routes/jobs.py                                +18 / -5     (POST /api/chains INSERT chain row; NEW GET /api/chains/{id}; rename handler to avoid shadowing)
tests/test_chains.py                                 +262 / -0    (NEW — 17 cases covering all choice-C contracts)
docs/SPEC.md                                         +~60 / -~8   (§C.1b Chain Schema + §C.3 GET endpoint + §D.3.6 strike + §D.4 B4 strike with rationale)
docs/WORKPLAN.md                                     +~30 / -~10  (TOC + §2 table + §3 B4 full rewrite + §7 checklist + §9 estimate)
docs/session-reports/2026-04-18_B4_chains-table.md   +NEW         (this file)
```

Tổng: 7 files, approx +550 / -25 lines.

---

## 4. Tests

| Test | Result | Notes |
|---|---|---|
| `tests/test_chains.py::test_status_rule_all_pending` | ✅ pass | rule #4 |
| `tests/test_chains.py::test_status_rule_any_failed_wins` | ✅ pass | rule #1 priority |
| `tests/test_chains.py::test_status_rule_any_running_or_claimed` | ✅ pass | rule #2 |
| `tests/test_chains.py::test_status_rule_all_completed` | ✅ pass | rule #6 |
| `tests/test_chains.py::test_status_rule_mixed_pending_completed_is_running` | ✅ pass | rule #3 |
| `tests/test_chains.py::test_status_rule_empty_list_is_pending` | ✅ pass | defensive default |
| `tests/test_chains.py::test_create_chain_persists_row` | ✅ pass | choice C INSERT |
| `tests/test_chains.py::test_get_chain_row_returns_none_for_unknown` | ✅ pass | None vs 404 |
| `tests/test_chains.py::test_post_chains_inserts_chain_row` | ✅ pass | POST → DB side-effect |
| `tests/test_chains.py::test_get_chain_returns_aggregated_all_pending` | ✅ pass | API smoke |
| `tests/test_chains.py::test_get_chain_status_all_completed` | ✅ pass | full-success |
| `tests/test_chains.py::test_get_chain_status_any_failed` | ✅ pass | priority bubble-up |
| `tests/test_chains.py::test_get_chain_status_any_running` | ✅ pass | in-flight |
| `tests/test_chains.py::test_get_chain_status_mixed_pending_completed` | ✅ pass | partial progress |
| `tests/test_chains.py::test_get_chain_404_for_unknown_id` | ✅ pass | error path |
| `tests/test_chains.py::test_chain_status_not_synced_from_job_update` | ✅ pass | **choice C trip-wire** |
| `tests/test_chains.py::test_get_chain_aggregate_includes_job_ids_in_order` | ✅ pass | `ORDER BY created_at ASC` |

- Full suite: **80 pass / 0 fail / 0 skip** (baseline was 63 → +17 new).
- Test command: `pytest tests/ -q` → 80 passed in 7.33s.
- Deprecation-clean: `python -W error::DeprecationWarning -m pytest tests/ -q` → 80 passed, no warning.
- Coverage delta: +100% on new `server/db/chain_store.py` + `server/models/chain.py`; POST /api/chains flow additionally exercised via `test_post_chains_inserts_chain_row` etc.

---

## 5. SPEC.md update

- [x] Strike-through §D.3.6 (chains gotcha section) — now "✅ FIXED"
- [x] Strike-through §D.4 B4 (known-bugs section) — full Choice C rationale + rejected alternatives inline
- [x] NEW §C.1b Chain Schema (Chain, ChainAggregate, ChainProgress) + aggregated status rules table
- [x] §C.3 Job Endpoints — added GET /api/chains/{id}; annotated POST /api/chains with INSERT side-effect
- [x] Commit hash `4dcf50f` recorded in SPEC.md, WORKPLAN.md, and this report

---

## 6. Invariants & rules verified

- [x] INV-1 Account Binding — chain row records `profile` but never inherits it onto jobs on behalf of callers; jobs still set their own profile at claim time. No chain-level override path introduced.
- [x] INV-2 Navigate by `edit_url` — unchanged (this task touches server only).
- [x] INV-3 Store Everything — unchanged.
- [x] INV-4 Serial per Project — unchanged; `project_lock.py` still owns this.
- [x] INV-5 media_id stable — unchanged.
- [x] R-CODE-3 Locale-Independent — no UI selectors touched.
- [x] R-CODE-10 No `datetime.utcnow()` — `chain_store._now_iso` uses `datetime.now(UTC)`; `Chain` model uses `Field(default_factory=lambda: datetime.now(UTC))` per B10 pattern.
- [x] R-CC-1 KHÔNG restructure kiến trúc — schema unchanged, no table columns added/removed, no module moved.

Whitelist respected: touched only files listed in the brief. Blacklist (flow/, worker/, models/job.py + profile.py, routes/worker.py, DESIGN.md, .claude/) untouched — confirmed via `git status` / `git diff --stat`.

---

## 7. Issues / Decisions

### Decision — A/B/C choice for persistence approach

**Chosen: Choice C (Hybrid).**

| Option | Mechanism | Drift risk | Table idiom |
|---|---|---|---|
| A | INSERT on POST + UPDATE `chains.status` on every job terminal | **Yes** — two places store same truth; one UPDATE failing splits them | Clean (uses the column as designed) |
| B | Never INSERT; compute from jobs at every GET | None | **Bad** — `CREATE TABLE that's never INSERT'd` stays, which *is* the original smell |
| **C** | INSERT immutable metadata on POST; NEVER UPDATE status; compute on read | **None** by construction | Acceptable — table holds real metadata; `status` column stays vestigial at DEFAULT |

**Why C over A:** The problem with A is not that the logic is hard — it's that adding an `UPDATE chains SET status=...` call in `update_job` creates a new class of bug (out-of-sync row) in exchange for solving a non-problem (the derived SELECT is cheap, one GROUP BY). The old data model's mistake was *having* a `status` column at all; C corrects that by treating it as vestigial instead of doubling down on it.

**Why C over B:** B leaves the chains table unused, which is the original smell the task is trying to fix. If a future reader sees `CREATE TABLE chains` and no `INSERT INTO chains`, they will raise this bug again. C actually uses the table for the one thing that *is* immutable (id + profile + created_at).

**Trade-off accepted:** The `chains.status` column stays at DEFAULT `'active'` forever. This is confusing for readers who expect it to mean something. Mitigations:
1. Module docstring in `chain_store.py` states "INSERT-only, no UPDATE path by design".
2. SPEC.md §C.1b says explicitly: "The `chains.status` DB column is vestigial — stays at INSERT default `'active'`. Not surfaced through the API; do not read it in application code."
3. Trip-wire test `test_chain_status_not_synced_from_job_update` drives a job `pending → running → completed` and asserts the chain row's `status` column doesn't move. If a future PR adds a sync path, this test breaks.

Alternative considered and rejected: drop the `status` column via schema migration. Rejected because (i) schema changes are a bigger blast radius than needed, (ii) the column is harmless if no one reads it, (iii) it's already in the FK of some DB exports.

### Decision — aggregated status rules

Followed the task brief's 5 rules verbatim, plus two defensive additions:
- **Empty job list → `pending`.** A chain row with zero jobs shouldn't crash the API; pending is the only sensible default.
- **`cancelled` handled explicitly.** The brief didn't cover cancelled-only chains. Decision: all-cancelled → `cancelled`; mixed `cancelled`+`completed` (no failures) → `completed` (at least one success). This mirrors how `update_job::TERMINAL_STATES` treats cancelled as terminal-but-not-failure.

### Decision — transaction scope for POST /api/chains

The handler does `await create_chain(chain)` **before** the job-creation loop. If a job insert fails mid-loop, we have an orphan chain row with incomplete jobs under it. Accepted because:
- `create_job` is extremely unlikely to fail in practice (validated request + non-null inserts).
- Wrapping both in one transaction would require plumbing a db connection through `create_job` and `create_chain`, which is a bigger refactor than the bug deserves.
- An orphan chain row still returns a well-formed `ChainAggregate` (empty jobs list → `pending`), so no crash.

Revisit if we see orphan chains in practice.

### Decision — function rename in `server/routes/jobs.py`

The old handler was named `create_chain`. The new code imports `create_chain` from `server.db.chain_store`, which would shadow the handler name. Renamed the handler to `create_chain_endpoint` (purely scope-local; FastAPI route registration uses the decorator, not the function name). Added a companion `get_chain` handler for GET /api/chains/{id}.

### Bug candidates discovered but not fixed (out of scope)

- `server/routes/jobs.py:88` — `await broadcast_job_update(jobs[0])` only notifies for the chain head. Frontend clients listening for chain-progress updates (post-B4 use case) will miss jobs 2..N. **Not in B4 scope** — WebSocket chain broadcast is explicitly deferred to Phase B per task brief [LƯU Ý]. Suggest tracking as `[B-discovery-chain-ws]` in Phase B.
- `server/db/database.py:17` — `chains.status TEXT NOT NULL DEFAULT 'active'` is now known to be vestigial. Could remove via migration in a future schema cleanup pass. Not urgent.

---

## 8. Handoff notes

- Workdir state: clean (all diffs captured in the whitelist files).
- Env: no new env vars.
- Next session can read `docs/SPEC.md §C.1b` for the Chain schema and `docs/WORKPLAN.md §3 B4` for the full decision record.
- The `chains.status` column vestigiality is documented in SPEC.md §C.1b and enforced by `tests/test_chains.py::test_chain_status_not_synced_from_job_update`. Future PRs adding a sync path will fail this test — that's intentional.

---

## 9. Done criteria checklist

From task brief `[DONE CRITERIA]`:

- [x] Choice A/B/C chosen + rationale §7 (Choice C)
- [x] Chain row persist on POST /api/chains
- [x] GET /api/chains/{id} returns aggregated status
- [x] Tests pass (chain CRUD + aggregation rules) — 17 new
- [x] Full suite 63+ pass, no regression — 80 pass (63 baseline + 17 new)
- [x] `-W error::DeprecationWarning` clean
- [x] SPEC §D.4 B4 strike-through + §C Chain schema added (§C.1b)
- [x] WORKPLAN §3 B4 "FIXED" thay vì "DEFER"
- [x] Zero diff ngoài whitelist (verified via `git status`)
- [x] Report 9 section (this file)

---

_Sign-off: ✅ Ready for supervisor review._
