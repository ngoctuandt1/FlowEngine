# PR #196 Round-2 Codex Review

## Test results

Sandbox read-only — pytest rejected before execution.

## Spec compliance (round-1 blockers)

1. Same-profile cap removal: addressed — `worker/main.py` no longer caps by profile count; repeats profiles to fill slots.
2. Dynamic CDP port: partially addressed — `allocate_cdp_port()` wired; probe socket released before Chrome binds (TOCTOU window remains).
3. Burn/wipe drain race: not addressed in original diff — `_draining` added after `dispatch_job()` completed, never cleared reliably for requeue path.
4. L1 load balancing: addressed — rotation offset in `worker/main.py`.
5. Startup validation: partially addressed — env var names correct but bool parsing was case-sensitive (`strip()` without `lower()`).

## Findings

### Important

1. **Burn-recovery drain deadlock** — `worker/main.py:209` discards `_draining` before the requeue branch; `worker/main.py:214` adds the profile only after wipe/rewarm is complete; with one configured profile the profile can remain permanently drained after requeue. Fix: wrap the requeue `api.update_job` call in `try/finally` and discard after the await.

2. **Env var case-sensitive bypass** — `FLOW_USE_BASE_PROFILE=True` / `FLOW_BROWSER_POOL=YES` pass startup validation but are not parsed correctly vs the expected `(1, true, yes)` set. Fix: `.strip().lower()` on all three flag parsers.

3. **CDP port TOCTOU** — probe socket closed before Chrome binds `--remote-debugging-port`. Port range + in-process counter bound the window; retry-on-failure or `--remote-debugging-port=0 + DevToolsActivePort` would eliminate it, but Playwright doesn't expose that API. Accepted-risk item.

### Minor

1. L1 rotation offset applies only in same-profile concurrency path; comment could clarify it is a soft balancer, not a guarantee.

## Verdict

Two Important blockers (drain deadlock + env case) fixed before merge. CDP TOCTOU accepted as known limitation.
