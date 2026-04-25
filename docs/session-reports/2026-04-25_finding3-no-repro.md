# Session — 2026-04-25: Finding 3 (L1 reuse-existing-project) repro attempt

**Goal:** repro the suspicious behavior from [2026-04-24_live_verify_post_45_44.md §Finding 3](2026-04-24_live_verify_post_45_44.md) — J3 (L1 `text-to-video`, `parent=null`, `chain=null`) landed in J2's project instead of creating a fresh one.

**Verdict: NO REPRO.** 3 sequential L1 jobs each minted their own project. Filing skipped per the 2026-04-24 report's own gate ("circumstantial — needs dedicated repro before filing").

## Setup

- Worktree commit (test code): `5b3e508` master + cleanup PR #55 staged.
- Server: `python run_server.py` from `D:/AI/FlowEngine/` (port 8080, fresh process). Pending queue cleared (2 stale `extend-video` jobs from 2026-04-24 deleted via `DELETE /api/jobs/{id}`).
- Worker: `WORKER_PROFILES=ngoctuandt20 MAX_CONCURRENT_JOBS=1 FLOW_USE_BASE_PROFILE=1 python run_worker.py` — single fresh worker, no stale workers running.
- Profile: `ngoctuandt20` (Ultra, English locale).
- Submission strategy: submit J(n+1) only after J(n) reached `completed` (sequential, not parallel) — addresses Hypothesis (c) "race with INS" from the original Finding 3.

## Results

| Job | Type | Status | `project_url` (last 36) | `media_id` (first 12) | Output |
|---|---|---|---|---|---|
| J1 `c579d38d` | text-to-video | ✅ completed | `…be544e78-ec57-4e17-80eb-fea7aae96e55` | `7ccc3041-c6b…` | `t2v_1080p_1777087254.mp4` |
| J2 `a360ac01` | text-to-video | ✅ completed | `…e4bb86d7-aafa-424f-9635-f1a6856bd70c` | `f2f6185c-dbc…` | `t2v_1080p_1777087457.mp4` |
| J3 `18750d29` | text-to-video | ✅ completed | `…59f5e817-3e3c-46c5-914d-ab5212e191a2` | `d7ddc93f-729…` | `t2v_1080p_1777087653.mp4` |

Three distinct `project_url`s, three distinct `media_id`s, all three at full `1080p`. Each job had `parent_job_id=null` and `chain_id=null` as expected for L1.

`completed_at` deltas:
- J1 → J2: ~3m 23s
- J2 → J3: ~3m 16s

Worker log: 0 errors, 0 exceptions, no marketing-landing detection fired (Flow homepage served `+ New project` directly all 3 times, consistent with the A/B nature documented in `feedback_flow_marketing_landing_bypass.md`).

## Why the original Finding 3 might have happened (still unproven)

The 2026-04-24 report flagged 4 hypotheses; this run rules out (c) (race with INS) since INS was not in the queue. The other three are still possible but unrepro'd:

- **(a) `+ New project` reopened existing project** — not seen this run.
- **(b) Temp-profile clone residual editor state** — not seen this run.
- **(d) Two worker processes concurrent** — explicitly avoided this run; the 2026-04-24 report's own "Log coverage note" called out a stale concurrent worker. **This is the most likely root cause** of the original Finding 3, and operationally addressed by killing all workers before each verify run (already in the 2026-04-25 handoff §1 Gap 1 action list).

## Recommendation

- **Do NOT file an issue.** Single-run anomaly, dedicated repro failed, most-likely cause is the operational stale-worker artifact called out in the original report itself.
- **Update [2026-04-25_session-handoff.md](2026-04-25_session-handoff.md)** Gap 1 → CLOSED-NO-REPRO.
- **Operational hygiene reminder** stays valid: kill all worker processes before any live-verify run; the engine has no global `MAX_CONCURRENT_JOBS` lock.

## Credit tally

| Job | Type | Output | Notes |
|---|---|---|---|
| J1 `c579d38d` | text-to-video | 1× 1080p (size unrecorded; file in `downloads/`) | 1080p-upscale path, no 4K cost |
| J2 `a360ac01` | text-to-video | 1× 1080p | 1080p-upscale path, no 4K cost |
| J3 `18750d29` | text-to-video | 1× 1080p | 1080p-upscale path, no 4K cost |

**Generations total: 3** L1 t2v at 1080p. No L2 jobs, no 4K video upscale (50-cr trap avoided).

## Workdir state at end

- Server (PID 953) still running on :8080.
- Worker (PID 1998) still running, polling.
- 3 completed jobs in DB, queue empty.
- No code touched in this session.
