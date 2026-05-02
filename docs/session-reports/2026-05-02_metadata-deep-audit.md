# FlowEngine metadata deep audit - 2026-05-02

## TL;DR
- 9 findings: 1 critical, 6 important, 2 minor
- Top 3 risks:
  - `DELETE /api/jobs/{id}` hard-deletes rows that other jobs still reference, so one operator action can orphan a chain and corrupt lineage/project views.
  - `jobs.project_id` exists in schema and powers project APIs, but live job creation/claim/update paths never write it, so `/api/projects` is largely disconnected from real production jobs.
  - L2 child jobs can still enter `claimed` with incomplete target context because route-time inheritance and claim-time validation do not enforce non-null `project_url` / `media_id` / `edit_url`.
- Coverage gaps in tests:
  - No delete/orphan integrity test for `DELETE /api/jobs/{id}`.
  - No end-to-end test that real `POST /api/jobs` / worker completion populates `project_id` for `/api/projects`.
  - No scheduler test that quarantined profiles are excluded from claim.
  - No rollback/atomicity test for `POST /api/chains`.

## Already Covered Upstream
- PR #178 already covers the `/api/jobs/{id}/related` 500 caused by extra DB columns being selected into `Job`.
- Branch `claude/job-chain-metadata-backfill` already covers the missing `chain_id` population/backfill for L1 rows. Findings below mention that branch where relevant but do not restate it as a new issue.

## Findings

### F1 [Critical] `DELETE /api/jobs/{id}` destroys lineage rows instead of cancelling them
**Where**: `server/routes/jobs.py:348-360`; `server/db/job_store.py:606-610`; `docs/SPEC.md:802-818`

**Symptom**: Deleting any claimed, running, or already-parented job can leave descendants with dangling `parent_job_id`, remove chain history, and break related/project/tree reads for that chain. Users see disappearing jobs while the UI still receives a fake `cancelled` broadcast for a row that no longer exists.

**Root cause**: The route docstring promises split behavior: claimed/running jobs should be marked `cancelled`, pending jobs deleted. The implementation does neither. It always calls `delete_job(job_id)`, which executes a raw `DELETE FROM jobs`, then mutates the in-memory copy to `cancelled` and broadcasts it. That violates the spec's explicit orphan warning for mid-chain deletes and bypasses all terminal-state integrity logic in `update_job()`, including `completed_at` stamping and profile-pointer cleanup.

**Recommended fix**: Route delete/cancel through `update_job()` for non-pending rows and either reject deletion of parented rows or convert delete into a soft-cancel consistently. Touch [server/routes/jobs.py](/D:/AI/FlowEngine/.claude/worktrees/metadata-deep-audit/server/routes/jobs.py) and [server/db/job_store.py](/D:/AI/FlowEngine/.claude/worktrees/metadata-deep-audit/server/db/job_store.py); preserve lineage rows so [server/routes/jobs.py](/D:/AI/FlowEngine/.claude/worktrees/metadata-deep-audit/server/routes/jobs.py) related/chain endpoints and [frontend/js/pages/project-view.js](/D:/AI/FlowEngine/.claude/worktrees/metadata-deep-audit/frontend/js/pages/project-view.js) can still traverse the DAG.

**Test coverage**: No test. Recommend `tests/test_jobs_api.py` case: create parent+child, delete parent, assert no hard delete occurs and descendants remain traversable. Acceptance criterion: deleting a parented or active job must not leave any row with `parent_job_id` pointing at a missing job.

### F2 [Important] `project_id` is schema-backed and frontend-visible but never written by real job lifecycle paths
**Where**: `server/db/database.py:32-77`; `server/db/database.py:195-200`; `server/db/job_store.py:54-108`; `server/models/job.py:109-280`; `server/db/project_store.py:71-92`; `server/db/project_store.py:95-142`; `server/db/project_store.py:212-251`; `frontend/js/pages/gallery.js:169`; `frontend/js/pages/project-view.js:1312-1350`; `tests/test_projects_api.py:46-82`

**Symptom**: `/api/projects` CRUD works on paper, but project cover thumbs, chain summaries, and project detail pages only populate for rows inserted manually in tests or by external SQL. Real jobs created through the API/worker do not join back to projects.

**Root cause**: The `jobs` table has a `project_id` column and index, and `project_store` derives project cover and chain lists entirely from `jobs.project_id`. But `JobCreate`, `Job`, and `JobUpdate` do not expose `project_id`, and `create_job()` omits it from the insert statement. The current tests hide the gap by directly inserting `jobs.project_id` with SQL instead of exercising live job creation. This is schema-store-model drift introduced by PR #164's data model without corresponding lifecycle wiring.

**Recommended fix**: Decide whether `project_id` is canonical or dead. If canonical, add it to [server/models/job.py](/D:/AI/FlowEngine/.claude/worktrees/metadata-deep-audit/server/models/job.py), write it in [server/db/job_store.py](/D:/AI/FlowEngine/.claude/worktrees/metadata-deep-audit/server/db/job_store.py), and populate it from job create/worker completion paths plus any backfill needed for existing rows. If not canonical, remove or redesign the project-store queries in [server/db/project_store.py](/D:/AI/FlowEngine/.claude/worktrees/metadata-deep-audit/server/db/project_store.py) so project views do not rely on an unmaintained column.

**Test coverage**: `tests/test_projects_api.py` only proves manual SQL inserts work. Recommend `tests/test_projects_job_linking.py`. Acceptance criterion: a project created via `/api/projects` and then used by normal job flows must surface at least one chain and cover thumb without direct SQL mutation.

### F3 [Important] Single-job child creation still under-inherits metadata; `edit_url` is omitted and `chain_id` is only covered upstream in another branch
**Where**: `server/routes/jobs.py:165-194`; `server/models/job.py:225-267`; `frontend/js/pages/chain-builder.js:119-132`; `frontend/js/pages/job-detail.js:392-398`; `docs/PROJECT_SPINE.md:56-60`

**Symptom**: A child created via `POST /api/jobs` can enter the queue without `edit_url`, so downstream navigation falls back to recomputing from `project_url` + `media_id` when possible and otherwise starts incomplete. The same route also omits `chain_id` inheritance, which is already being handled on branch `claude/job-chain-metadata-backfill`.

**Root cause**: When `parent_job_id` is present, `create_single_job()` only copies `profile`, `project_url`, and `media_id` from a completed parent. It does not copy `edit_url`. That leaves the row dependent on later claim-time repair rather than enforcing the invariant at creation time. The frontend has already adapted to this by reconstructing edit links in `job-detail.js`. Separately, route-time `chain_id` omission exists here too, but that portion is already covered by `claude/job-chain-metadata-backfill`.

**Recommended fix**: Extend [server/routes/jobs.py](/D:/AI/FlowEngine/.claude/worktrees/metadata-deep-audit/server/routes/jobs.py) child-create inheritance to include `edit_url`, and keep the `chain_id` note aligned with the in-flight backfill branch rather than re-fixing it twice. The create path should persist the same L2 navigation context the worker expects later.

**Test coverage**: No route test covers child create inheritance. Recommend `tests/test_jobs_api.py`. Acceptance criterion: creating an L2 child from a completed parent must persist `profile`, `project_url`, `media_id`, and `edit_url` immediately; `chain_id` should be asserted only once the backfill branch lands.

### F4 [Important] Claim-time L2 inheritance accepts NULL parent target context and promotes it into `claimed`
**Where**: `server/db/job_store.py:406-489`; `worker/dispatcher.py:477-500`; `docs/SPEC.md:77-83`; `tests/test_claim_algorithm.py:224-250`

**Symptom**: A parent row marked `completed` but missing `project_url`, `media_id`, or `edit_url` can still unlock its child. The child is then claimed with NULL target metadata and fails later in worker navigation or operation setup instead of being blocked at the scheduler boundary.

**Root cause**: `claim_next_job()` uses `parent.status = 'completed'` and `parent.profile IN (...)` as the only readiness gate for L2 jobs. It then copies whatever `project_url`, `media_id`, and `edit_url` are stored on the parent row, including NULLs. `worker/dispatcher.py` only project-locks when `project_url` is truthy, so a malformed claimed child bypasses both data-integrity validation and the project mutex. The existing B22 tests even encode the current behavior for `NULL edit_url`, which proves the gap is currently accepted rather than treated as invalid state.

**Recommended fix**: Tighten [server/db/job_store.py](/D:/AI/FlowEngine/.claude/worktrees/metadata-deep-audit/server/db/job_store.py) claim eligibility for L2+ jobs to require non-null inherited target context, or fail the row into a diagnosable terminal state before worker claim. Revisit the permissive B22 regression in [tests/test_claim_algorithm.py](/D:/AI/FlowEngine/.claude/worktrees/metadata-deep-audit/tests/test_claim_algorithm.py) so tests match the invariant rather than the current leak.

**Test coverage**: Existing tests encode the permissive behavior. Recommend updating `tests/test_claim_algorithm.py`. Acceptance criterion: an L2 child whose completed parent lacks required target metadata must not be returned by `/api/worker/claim`.

### F5 [Important] Profile/job mirror cleanup is incomplete: `worker_id` remains stale on terminal updates, and stale recovery never clears the profile row
**Where**: `server/db/job_store.py:154-213`; `server/db/job_store.py:570-603`; `server/db/profile_store.py:110-127`; `frontend/js/pages/engine-status.js:128-132`; `docs/SPEC.md:1018-1020`; `tests/test_profile_store.py:71-93`; `tests/test_e2e_invariants.py:276-376`

**Symptom**: After a normal completion, `profiles.worker_id` can still point at the last worker even though `current_job_id` was cleared. After stale recovery, both `profiles.current_job_id` and `profiles.worker_id` can remain pinned to a dead worker while the job row is reset to `pending`. Dashboard/operator views then disagree with scheduler reality.

**Root cause**: `update_job()` clears only `profiles.current_job_id` for terminal states; it never clears `profiles.worker_id`. `recover_stale_jobs()` only rewrites the `jobs` row and never touches `profiles` at all. That leaves two sources of truth diverged. The spec and UI both treat profile rows as the live worker/profile state mirror, but only half the fields are maintained.

**Recommended fix**: In [server/db/job_store.py](/D:/AI/FlowEngine/.claude/worktrees/metadata-deep-audit/server/db/job_store.py), clear both `current_job_id` and `worker_id` on terminal transition when they point at the finishing job, and make stale recovery repair the profile row in the same transaction as the job reset. Review any worker affinity semantics before clearing `worker_id` permanently, but the current UI already interprets it as active ownership.

**Test coverage**: `tests/test_profile_store.py` covers `current_job_id` only; `tests/test_e2e_invariants.py` covers job-row stale recovery only. Recommend extending both. Acceptance criterion: after completion or stale recovery, no profile row may retain `current_job_id` or `worker_id` for a job that is no longer active.

### F6 [Important] `POST /api/chains` is non-transactional and can persist partial chains
**Where**: `server/routes/jobs.py:197-231`; `server/routes/jobs.py:234-265`; `server/db/job_store.py:54-108`; `docs/SPEC.md:408-417`

**Symptom**: If one step in chain creation fails after earlier inserts succeeded, the system can retain a `chains` row plus a truncated prefix of jobs. Depending on failure position, `GET /api/chains/{id}` returns either a partial DAG or a 404 for an existing chain row with zero jobs.

**Root cause**: `create_chain_endpoint()` inserts the `chains` row first, then calls `create_job()` for each step, and each `create_job()` commits independently. There is no shared transaction across the chain row and the N job rows. The read path then derives chain existence from jobs, not from the `chains` table, so persistence and visibility can diverge immediately on a mid-loop error.

**Recommended fix**: Move chain creation to a single DB transaction spanning the immutable `chains` insert and all job inserts. That change belongs in [server/routes/jobs.py](/D:/AI/FlowEngine/.claude/worktrees/metadata-deep-audit/server/routes/jobs.py) and likely [server/db/job_store.py](/D:/AI/FlowEngine/.claude/worktrees/metadata-deep-audit/server/db/job_store.py) or a dedicated store helper so rollback is guaranteed.

**Test coverage**: `tests/test_chains.py` covers aggregation, not atomic create failure. Recommend `tests/test_chains_api.py`. Acceptance criterion: if any step insert fails, neither the `chains` row nor any child job row may persist.

### F7 [Important] Quarantined profiles are not enforced by the scheduler
**Where**: `server/routes/profiles.py:67-89`; `server/db/profile_store.py:110-127`; `server/routes/worker.py:43-55`; `server/db/job_store.py:406-425`; `server/db/job_store.py:491-534`; `docs/SPEC.md:438-454`

**Symptom**: A profile manually quarantined in the admin UI can still claim jobs if a worker process continues advertising that profile name in `/api/worker/claim`.

**Root cause**: The profile subsystem already defines `status = 'quarantined'` and `get_available_profiles(worker_id)` correctly excludes quarantined rows. But `claim_next_job()` never calls that store helper. It trusts the worker-supplied `profiles` list directly in both the L2 and L1 branches. That means quarantine is UI-visible and persisted, but not enforced where claims are actually made.

**Recommended fix**: Intersect worker-advertised profiles with DB-allowed profiles before entering the claim query. The enforcement point is [server/routes/worker.py](/D:/AI/FlowEngine/.claude/worktrees/metadata-deep-audit/server/routes/worker.py) or [server/db/job_store.py](/D:/AI/FlowEngine/.claude/worktrees/metadata-deep-audit/server/db/job_store.py); the existing [server/db/profile_store.py](/D:/AI/FlowEngine/.claude/worktrees/metadata-deep-audit/server/db/profile_store.py) helper already expresses the intended rule.

**Test coverage**: `tests/test_profiles_routes.py` verifies quarantine persistence only. No claim-path test exists. Recommend `tests/test_worker_claim_quarantine.py`. Acceptance criterion: a quarantined profile name must never be able to claim an L1 or L2 job, even if a worker includes it in the request body.

### F8 [Minor] `safety_filter` remains a dangling schema/query/template field after removal from the job model
**Where**: `server/db/database.py:47-60`; `server/db/job_store.py:249-286`; `server/models/job.py:109-308`; `server/models/template.py:14-30`; `git log --since=2026-04-22 --oneline` shows prior removal commit `1dd0e80`

**Symptom**: The database and related-job SQL still carry `safety_filter`, and workflow templates still allow it, but runtime `Job` models no longer define it. This is exactly the class of drift that caused PR #178's extra-column failure surface, even though that specific related-endpoint 500 is already covered upstream.

**Root cause**: `safety_filter` was removed from the job model surface, but the column was left in the schema and in the raw SQL projections for related-job traversal. Templates still expose the field as a first-class step attribute. The current system survives only because `Job` ignores extra input by default in Pydantic v2; the field is not actually modeled end-to-end.

**Recommended fix**: Either restore `safety_filter` as a real job field everywhere or finish removing it from [server/db/database.py](/D:/AI/FlowEngine/.claude/worktrees/metadata-deep-audit/server/db/database.py), [server/db/job_store.py](/D:/AI/FlowEngine/.claude/worktrees/metadata-deep-audit/server/db/job_store.py), and [server/models/template.py](/D:/AI/FlowEngine/.claude/worktrees/metadata-deep-audit/server/models/template.py). This is low-severity now because current `Job` parsing is permissive, but it is stored drift.

**Test coverage**: Manual repro only. Acceptance criterion: schema, SQL projections, templates, and `Job` model must either all include `safety_filter` or all omit it.

### F9 [Minor] Frontend fallback chains are masking backend metadata gaps instead of surfacing them early
**Where**: `frontend/js/pages/chain-builder.js:126-132`; `frontend/js/pages/gallery.js:169`; `frontend/js/pages/project-view.js:1312-1350`; `frontend/js/pages/chain-tree.js:1216-1220`; `frontend/js/pages/job-detail.js:392-398`; `frontend/js/pages/job-detail.js:1461-1475`

**Symptom**: The UI often "works" even when backend metadata is missing, but only by degrading to job-id routes, reconstructed edit URLs, or ad-hoc related/list merges. That delays detection of integrity bugs and produces inconsistent views across pages.

**Root cause**: Several frontend pages explicitly compensate for missing backend fields:
- `chain-builder.js` falls back from `related.chain_id` to `chain_root_id` to `parentJob.chain_id` to `parentJob.id`.
- `gallery.js` links tiles with `job.chain_id || job.id`.
- `project-view.js` has a legacy job-id fallback and a compatibility merge path that accepts jobs with missing `chain_id`.
- `job-detail.js` rebuilds edit links from `project_url` + `media_id` when `edit_url` is absent.
- `chain-tree.js` drops from chain-detail payloads to `GET /api/jobs?chain_id=...` and synthesizes summary fields.

The underlying L1 `chain_id` gap is already covered by `claude/job-chain-metadata-backfill`, but the broader pattern still hides future metadata regressions.

**Recommended fix**: Once the known backend gaps are fixed, tighten these pages to warn on invariant violations instead of silently routing around them. The relevant files are [frontend/js/pages/chain-builder.js](/D:/AI/FlowEngine/.claude/worktrees/metadata-deep-audit/frontend/js/pages/chain-builder.js), [frontend/js/pages/gallery.js](/D:/AI/FlowEngine/.claude/worktrees/metadata-deep-audit/frontend/js/pages/gallery.js), [frontend/js/pages/project-view.js](/D:/AI/FlowEngine/.claude/worktrees/metadata-deep-audit/frontend/js/pages/project-view.js), [frontend/js/pages/chain-tree.js](/D:/AI/FlowEngine/.claude/worktrees/metadata-deep-audit/frontend/js/pages/chain-tree.js), and [frontend/js/pages/job-detail.js](/D:/AI/FlowEngine/.claude/worktrees/metadata-deep-audit/frontend/js/pages/job-detail.js).

**Test coverage**: No test; mostly manual repro only today. Acceptance criterion: pages should stop needing `job.id` fallback for chain navigation once chain metadata invariants are satisfied.

## Notes From Recent PR Audit
- `78b4a43` / PR #164 added `projects` plus `jobs.project_id`, but there is no corresponding live job-store/model wiring or route-level integration test. That omission directly underlies F2.
- `335a84c` / PR #142 added `GET /api/chains/{chain_id}` and did include endpoint tests, but not atomic-create rollback coverage for `POST /api/chains` (F6).
- `41be7cf` / PR #124 added `/api/jobs/{id}/related`; the extra-column 500 it exposed is already covered by PR #178, but the same class of schema/model drift still exists around `safety_filter` (F8).
- `cf991e0` / PR #123 added profile precedence fixes and tests, but not single-job child-create inheritance coverage for `edit_url` (F3).
- `6002bcd` / PR #177 was docs-only; no new job-lifecycle drift was introduced there.

## Frontend Field Surface Audited

### `frontend/js/pages/chain-builder.js`
- Reads from `/api/jobs/{id}/related`: `self.id`, `self.type`, `self.chain_id`, `self.project_url`, `self.profile`, `self.media_id`, top-level `chain_id`, top-level `chain_root_id`.
- Reads from `/api/chains` create response: `chain_id`, `jobs[].id`, `jobs[].chain_id`.

### `frontend/js/pages/project-view.js`
- Reads job payload fields: `id`, `type`, `status`, `prompt`, `direction`, `profile`, `job_level`, `parent_job_id`, `chain_id`, `output_files`, `ingredient_image_paths`, `aspect_ratio`, `start_image_path`, `end_image_path`, `ref_image_path`, `created_at`, `createdAt`.
- Reads chain/detail payload fields: `jobs`, `edges`, `root_id`, `chain_id`, `id`.
- Reads related payload fields when in compatibility mode: `self`, `parent`, `ancestors`, `children`, `siblings`, `chain_root_id`.

### `frontend/js/pages/job-detail.js`
- Reads job payload fields: `id`, `type`, `status`, `prompt`, `model`, `aspect_ratio`, `bbox`, `direction`, `profile`, `job_level`, `parent_job_id`, `chain_id`, `project_url`, `media_id`, `edit_url`, `output_files`, `generation_id`, `worker_id`, `claimed_at`, `completed_at`, `created_at`, `updated_at`, `error`, `ingredient_image_paths`, `start_image_path`, `end_image_path`, `ref_image_path`.
- Reads chain/list payload fields: `jobs[].chain_id`, `jobs[].id`, `jobs[].parent_job_id`.

### `frontend/js/pages/jobs.js`
- Reads job payload fields: `id`, `type`, `status`, `profile`, `chain_id`, `created_at`, `createdAt`, `bbox`, `ingredient_image_paths`.
- Reads recovery response field: `recovered`.

### `frontend/js/pages/gallery.js`
- Reads job payload fields: `id`, `type`, `status`, `prompt`, `profile`, `chain_id`, `media_id`, `output_files`, `direction`, `created_at`, `createdAt`.

### `frontend/js/pages/chain-tree.js`
- Reads chain summary/detail fields: `id`, `chain_id`, `profile`, `created_at`, `updated_at`, `status`, `progress`, `root_prompt`, `jobs`, `stats`.
- Reads job payload fields: `id`, `type`, `status`, `prompt`, `direction`, `profile`, `job_level`, `parent_job_id`, `chain_id`, `media_id`, `output_files`, `error`, `created_at`, `updated_at`, `claimed_at`, `completed_at`.

## Pydantic Extra-Field Handling Check
- Current `Job`/`JobCreate`/`JobUpdate` models in [server/models/job.py](/D:/AI/FlowEngine/.claude/worktrees/metadata-deep-audit/server/models/job.py) do not set `extra='forbid'`; current behavior is permissive and is why stale DB columns like `safety_filter` do not currently crash every codepath.
- `TemplateStep` explicitly sets `extra="allow"` in [server/models/template.py](/D:/AI/FlowEngine/.claude/worktrees/metadata-deep-audit/server/models/template.py#L17).
- No audited job/chain/project model in scope currently introduces `extra='forbid'`.

## Legacy NULL Surface To Backfill Carefully
- `project_id`: column added by `_ensure_job_column` in `server/db/database.py:195-200`; project queries in `server/db/project_store.py:71-142` and `server/db/project_store.py:224-245` assume it may exist but currently tolerate NULL by omission. Impact is silent invisibility, not 500.
- `start_image_path` / `end_image_path`: additive columns in `server/db/database.py:193-194`; validated at create time for `frames-to-video`, so old NULL rows are only risky if hand-edited into that type.
- `chain_id` on old L1 rows: covered by `claude/job-chain-metadata-backfill`.
- `edit_url` on old completed rows: currently masked by frontend reconstruction in `frontend/js/pages/job-detail.js:392-398`, but still part of F3/F4 integrity debt.
