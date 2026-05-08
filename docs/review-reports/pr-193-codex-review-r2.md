# PR #193 Round-2 Codex Review

## Test results

Sandbox read-only — pytest rejected before execution.

## Spec compliance

- `JobUpdate` exposes `worker_id` and `claimed_at` for the requeue path (`server/models/job.py`).
- Pydantic v2 `model_dump(exclude_unset=True)` correctly includes explicitly-set `None` fields.
- `job_store` requeue condition at `server/db/job_store.py:286` logically correct for detecting explicit claim-owner clear.
- `profiles` clear uses `WHERE current_job_id = ?` — does not accidentally clear rows owned by another job.
- `cmdline.split(b"\x00")` in `worker/profile_swapper.py` handles trailing NUL safely.

## Findings

### Important

**Missing DB-level regression test for burn requeue metadata clearing** — PR adjusts profile-swapper/dispatcher assertions but does not add a test proving `JobUpdate(status=pending, worker_id=None, claimed_at=None)` clears both `jobs.worker_id / jobs.claimed_at` and `profiles.current_job_id / profiles.worker_id`. Both reviewers flagged this.

Suggested: create claimed job + profiles row, call `update_job(...)` with requeue payload, assert both tables cleared. Add negative case where `worker_id` is omitted to confirm profiles row not cleared accidentally.

## Verdict

One Important blocker (missing DB-level test) fixed before merge via `test_requeue_clears_job_and_profile_claim_metadata`.
