# PRD — Flow Feature Update 2026-05 v2

**Status:** Supersedes v1; ready for user review after review-blocker rewrite  
**Author:** Senior PRD rewrite pass  
**Date:** 2026-05-20  
**Supersedes:** `docs/PRD_FLOW_FEATURE_UPDATE_2026-05.md`  
**Primary inputs:** `docs/FLOW_DISCOVERY_2026-05-20.md`, `docs/REVIEW_FLOW_UPDATE_2026-05-20.md`, `CLAUDE.md`, `docs/PROJECT_SPINE.md`

This v2 replaces v1 because all four v1 units received `REQUEST_CHANGES` in `docs/REVIEW_FLOW_UPDATE_2026-05-20.md:44` through `docs/REVIEW_FLOW_UPDATE_2026-05-20.md:48`. The biggest v1 blockers were impossible unit ownership, a nonexistent Agent call site, cross-origin reverseAPI auth assumptions, missing `error_kind` persistence, swallowed L2 batch paywall errors, and stale `duration`/image-registry scope.

Traceability anchors:
- v1 scoped `duration` in Unit B at `docs/PRD_FLOW_FEATURE_UPDATE_2026-05.md:37`, `docs/PRD_FLOW_FEATURE_UPDATE_2026-05.md:99`, migration at `docs/PRD_FLOW_FEATURE_UPDATE_2026-05.md:170`, and open answer at `docs/PRD_FLOW_FEATURE_UPDATE_2026-05.md:232`; v2 removes all active duration work per `docs/FLOW_DISCOVERY_2026-05-20.md:301` and review `docs/REVIEW_FLOW_UPDATE_2026-05-20.md:17`.
- v1 claimed all units were file-disjoint at `docs/PRD_FLOW_FEATURE_UPDATE_2026-05.md:191`; review disproved this at `docs/REVIEW_FLOW_UPDATE_2026-05-20.md:18`.
- v1 named `FlowClient.navigate_to_project()` at `docs/PRD_FLOW_FEATURE_UPDATE_2026-05.md:123`; real hooks are `FlowClient.start()` at `flow/client.py:540` and `FlowClient.reset_for_next_job()` at `flow/client.py:629`, per review `docs/REVIEW_FLOW_UPDATE_2026-05-20.md:19`.
- v1 assumed same-origin Agent `fetch` auth at `docs/PRD_FLOW_FEATURE_UPDATE_2026-05.md:121`; review rejects that because Agent endpoint host is `aisandbox-pa.googleapis.com`, not `labs.google`, at `docs/REVIEW_FLOW_UPDATE_2026-05-20.md:20`.

---

## 1. Goal

Bring FlowEngine back to safe operation on the 2026-05 Google Flow UI by fixing selected P0/P1 deltas and stale defaults without violating chain invariants from `docs/PROJECT_SPINE.md:55` through `docs/PROJECT_SPINE.md:63`.

Primary outcomes:
- Current video model selection works after LP label removal, while old `*-lp` job/config strings keep backward-compatible aliases.
- Video composer automation handles the restructured menu, explicitly enters Video mode, selects Frames/Ingredients sub-tabs when needed, enforces L1 `x1`, and checks the credit preview before submit.
- Agent mode is disabled only at per-job deterministic automation time, with verified auth or DOM fallback, and mutation is logged.
- Free-tier L2 paywall produces canonical non-retry failure instead of selector timeouts, profile burns, or swallowed multitab errors.
- Server job schema persists `error_kind` and `error_message`; LP defaults and sentinels are purged across server, worker, frontend, and affected tests.
- Final merge train adds integration smoke coverage and docs sync after code units land.

Non-goals:
- No image registry migration from `flow/operations/image.py`; image model map, ratio map, and selector already exist at `flow/operations/image.py:58`, `flow/operations/image.py:71`, and `flow/operations/image.py:341`.
- No Agent job type; Agent remains a state hazard for deterministic generation.
- No Tools marketplace, Characters, Scenes, Trash, Share mint/revoke, voice asset attach, or existing-media asset-picker attach in this epic.
- No reverseAPI generation replay scope beyond Agent toggle verification.

---

## 2. Scope Decisions

### In Scope — Selected P0/P1 and Required Cleanup

| # | Item | Strategy | Unit |
|---|---|---|---|
| 1 | Video LP removal | Replace primary video labels with current UI labels; keep `*-lp` aliases | A |
| 2 | Video model alias compatibility | Map old `veo-3.1-lite-lp`/`veo-3.1-fast-lp` to canonical non-LP keys with warning | A |
| 3 | Omni Flash awareness | Add paid-tier registry entry, never auto-select on free profiles | A |
| 4 | `generate.py` model call compatibility | Update only model-selection call sites after Unit A registry changes | A, serialized before B |
| 5 | Composer menu restructure | Open by role/text/menu discovery, not exact icon inventory | B |
| 6 | Video mode force | Force `Video` tab for all `*-to-video` L1 jobs | B |
| 7 | Frames/Ingredients sub-tabs | Select explicit Video sub-tab row for `frames-to-video` and `ingredients-to-video` | B |
| 8 | L1 count and credit guard | Enforce L1 `x1`; read `Generating will use N credits`; fail before submit if count/preview violates configured guard | B |
| 9 | Agent disable | Per-job Agent OFF immediately before deterministic automation; verified reverseAPI auth or DOM-click fallback | C |
| 10 | L2 free-tier paywall | Detect banner + Upgrade CTA, raise `L2PaywallError`, no profile burn, no retry | D |
| 11 | L2 multitab propagation | Re-raise paywall from tab worker and inspect L2 batch results in dispatcher path | D |
| 12 | Error persistence | Add `error_kind` and `error_message` to server models, DDL, migrations, store plumbing | E |
| 13 | LP default purge | Replace `veo-3.1-lite-lp`/`veo-3.1-fast-lp` defaults and sentinels across server, worker, frontend, tests | E |
| 14 | Final smoke/docs | Cross-unit integration test plus `PROJECT_SPINE`/UI reference sync | F |

### Deferred

- `duration` is deferred because free-tier composer has no duration selector and paid selector/payload is unknown (`docs/FLOW_DISCOVERY_2026-05-20.md:284`, `docs/FLOW_DISCOVERY_2026-05-20.md:301`).
- Image registry migration is not needed because `flow/operations/image.py` already has all three image models and five ratios.
- Existing-media asset picker attach is deferred; discovery says it could unlock chained generation without re-upload at `docs/FLOW_DISCOVERY_2026-05-20.md:156` through `docs/FLOW_DISCOVERY_2026-05-20.md:158`, but no endpoint/payload is captured.
- Voice asset attach is deferred; v1 goal contradiction is removed because voice is not in this epic.
- Project auto-name is deferred to docs/dashboard polish; discovery notes it at `docs/FLOW_DISCOVERY_2026-05-20.md:174` through `docs/FLOW_DISCOVERY_2026-05-20.md:178`.
- Trash/Share/Characters/Scenes/Tools remain follow-up epics; Scenes 404 remains verification debt, not hard product truth.
- `Return silent videos` is follow-up probe debt, not an irrelevant hard claim.
- L2 output-count enforcement is deferred. Free-tier L2 composer is paywalled, so there is no observed L2 count chip to enforce. If paid-tier L2 composer exposes a count chip, add follow-up work after Unit D.

---

## 3. Unit Decomposition

Parallel rule: no more than four active Codex branches at once. No two active branches may edit the same file. Where a file must be touched by two units, serialize those units and transfer ownership only after the earlier branch merges.

### Unit A — Video Model Registry + Alias Compatibility

**Reasoning level:** `high`

**OWNS in Wave 1:**
- `flow/model_selector.py`
- `flow/operations/generate.py` — model-selection call sites only; no composer panel logic
- `tests/test_model_selector.py`
- `tests/test_model_selector_lite_fallback.py`

**READS:**
- `flow/operations/image.py` to confirm image registry remains local and untouched.
- `server/models/job.py`, `frontend/js/constants.js`, and `worker/dispatcher.py` only to understand LP strings Unit E later purges.

**FORBIDDEN:**
- `flow/operations/image.py`
- Composer panel helpers in `flow/operations/generate.py`
- `server/*`, `worker/*`, `frontend/*`

**Acceptance criteria:**
1. Primary video model registry contains current labels only: `omni-flash`, `veo-3.1-lite`, `veo-3.1-fast`, `veo-3.1-quality`; `omni-flash` is marked paid and never selected for free profiles.
2. Primary registry has no `Lower Priority` display labels; LP strings live only in alias/fallback compatibility data.
3. `veo-3.1-lite-lp` maps to `veo-3.1-lite`; `veo-3.1-fast-lp` maps to `veo-3.1-fast`; alias use logs one warning with original and canonical key.
4. `free_mode=True` no longer coerces canonical models back to LP aliases at `flow/model_selector.py:205` through `flow/model_selector.py:209`; it selects `veo-3.1-lite` as the free-profile baseline unless the caller passes a stricter canonical non-LP model.
5. Retire stale zero-credit semantics at `flow/model_selector.py:194`, `flow/model_selector.py:391`, and `flow/model_selector.py:444`; credit cost `0` is no longer a precondition for free-profile selection because Lite may cost nonzero credits.
6. Replace free-mode `_verify_credits(... expected=0)` hard failures with budget-compatible verification: parsed cost must be less than or equal to `FLOW_MAX_CREDITS_PER_JOB` from Unit B, default `10`; missing preview remains a clear pre-submit failure.
7. Selector can still tolerate legacy LP labels during rollout, but canonical selection prefers non-LP labels first.
8. `DEFAULT_MODEL` in `flow/model_selector.py` becomes `veo-3.1-lite`; server/frontend defaults wait for Unit E.
9. `generate.py` call sites pass canonical video model keys into selector without changing composer menu structure.
10. Root tests only: update `tests/test_model_selector.py` and `tests/test_model_selector_lite_fallback.py`; do not create `tests/flow/...`.

**Live verification:**
- Cheap MCP dropdown inspection on ngoctuandt20, profile `s17524h173` ULTRA, and one second free profile if available.
- No credit burn required for selector verification.

### Unit B — Composer Panel Restructure, Video Side Only

**Reasoning level:** `xhigh`

**Runs after Unit A and Unit D merge.** Unit B receives `flow/operations/generate.py` ownership only after Unit A is merged and imports `CreditBudgetExceeded` only after Unit D adds it in `flow/operations/_base.py`.

**OWNS in Wave 2:**
- `flow/operations/generate.py` — composer chip-open, Video tab forcing, Frames/Ingredients sub-tabs, L1 `x1` enforcement, credit preview guard
- `tests/test_generate.py`

**READS:**
- `flow/model_selector.py` for Unit A API.
- `flow/operations/_base.py` for the Unit D-owned `CreditBudgetExceeded` exception contract only.
- `flow/operations/image.py` for parity awareness only.

**FORBIDDEN:**
- `flow/model_selector.py`
- `flow/operations/_base.py`
- `flow/operations/extend.py`, `flow/operations/insert.py`, `flow/operations/remove.py`, `flow/operations/camera.py`
- `server/*`, `worker/*`, `frontend/*`

**Acceptance criteria:**
1. Composer menu open uses resilient role/text/menu discovery: `button[aria-haspopup="menu"]` filtered by visible text, current mode/model/count text, and menu open result.
2. Do not hard-code exact Material icon ligatures as acceptance criteria; icons may be used only as fallback diagnostics.
3. For `text-to-video`, `frames-to-video`, and `ingredients-to-video`, composer always forces `Video` before setting aspect, count, model, or uploads.
4. `frames-to-video` selects the `Frames` sub-tab row and verifies `Start`/`End` upload affordances before upload.
5. `ingredients-to-video` selects the `Ingredients` sub-tab row and preserves existing upload behavior; no asset-picker existing-media attach in this unit.
6. L1 output count is forced to one and verification accepts both `1x` and `x1`; count defaults like `x4` must not reach submit.
7. Credit preview text is read after count/model selection. If preview exceeds configured per-job budget or count verification fails, submit is blocked with a clear error before any generation request.
8. Per-job budget source is env var `FLOW_MAX_CREDITS_PER_JOB`; read at submit time with `os.environ.get("FLOW_MAX_CREDITS_PER_JOB", "10")`, parse to `int`, and use default budget `10` when unset.
9. If parsed preview cost `N` exceeds budget `M`, raise `CreditBudgetExceeded(cost=N, budget=M)` before any generation request; exception class lives in Unit D's `flow/operations/_base.py`, not a new Unit B-owned module.
10. `tests/test_generate.py` includes budget guard coverage: mock the credit preview / `_verify_credits` path to return cost `40` with `FLOW_MAX_CREDITS_PER_JOB=10` and assert `CreditBudgetExceeded` is raised before submit.
11. No L2 count enforcement is added; free-tier L2 composer is paywalled and unobserved for count chips.
12. Tests live at repo-root `tests/test_generate.py`.

**Live verification:**
- Zero-credit composer inspection for menu structure and count/preview text.
- One controlled submit only in final live-verify pass if user approves credit spend.

### Unit C — Agent Disable, Per Job

**Reasoning level:** `xhigh`

**OWNS in Wave 1:**
- `flow/agent.py` — new helper module
- `flow/client.py` — full ownership for this branch, limited to `reset_for_next_job(target_url=...)` integration and support methods
- `worker/dispatcher.py` — narrow lease/navigation seam only: `_client_lease` at `worker/dispatcher.py:123` through `worker/dispatcher.py:139`, plus L2 lease call-site lines `worker/dispatcher.py:387`, `worker/dispatcher.py:415`, `worker/dispatcher.py:442`, and `worker/dispatcher.py:468` to pass a project/edit `target_url`; do not edit handler bodies, LP defaults, or dispatcher catches
- `worker/browser_pool.py` — narrow reset-url seam `worker/browser_pool.py:93` through `worker/browser_pool.py:134`, especially the existing `reset_for_next_job(target_url=reset_url)` call at `worker/browser_pool.py:133`

**READS:**
- `flow/operations/_base.py:navigate_to_edit` to confirm the current first post-lease project/edit navigation lives at `flow/operations/_base.py:170` through `flow/operations/_base.py:173`, with login retry at `flow/operations/_base.py:191` through `flow/operations/_base.py:192` and fallback direct edit navigation at `flow/operations/_base.py:222` through `flow/operations/_base.py:224`.
- `flow/landing.py` for `dismiss_flow_marketing_landing` at `flow/landing.py:269` and login-redirect behavior.

**FORBIDDEN:**
- All files and line ranges outside Unit C OWNS.
- `worker/dispatcher.py` LP default lines `worker/dispatcher.py:257`, `worker/dispatcher.py:290`, `worker/dispatcher.py:361`, and `worker/dispatcher.py:393` owned by Unit E.
- `worker/dispatcher.py` paywall/credit result adapter ranges `worker/dispatcher.py:577` through `worker/dispatcher.py:590`, `worker/dispatcher.py:709` through `worker/dispatcher.py:749`, `worker/dispatcher.py:803` through `worker/dispatcher.py:823`, and `worker/dispatcher.py:1280` through `worker/dispatcher.py:1330` owned by Unit D.
- `flow/operations/_base.py` edits. If the reset-url seam proves impossible, implementer MUST grep `page.goto|navigate_to_edit|agent` and pin an explicit `_base.py` line-range handoff with Unit D before editing.

**Pre-coding live capture gate:**
1. Use profile `s17524h173` ULTRA for capture. Account is paid-tier ULTRA, but Flow OAuth is separate AI Test Kitchen client with scope `aisandbox`.
2. Do not assume warm Mail login authorizes Flow. If Flow OAuth prompt appears, click `Create with Google Flow` CTA and drive `handle_login_redirect` again before capturing Agent toggle.
3. Toggle Agent in the UI once and capture the exact live `PATCH https://aisandbox-pa.googleapis.com/v1/projects/{pid}/agentInfo?updateMask=agent_toggle_state` request.
4. Paste into PR notes: request body shape, `Authorization` mechanism, credential mode, origin/referrer behavior, response shape, and whether auth is Bearer token, SAPISIDHASH, cookie-backed, or other.
5. Because endpoint host is `aisandbox-pa.googleapis.com` and page host is `labs.google`, do not rely on `page.evaluate(fetch(...))` auto-attaching auth. Use reverseAPI only after capture proves required headers/body can be reproduced from page context.
6. If capture cannot prove cross-origin auth, implement DOM-click fallback only: detect active Agent chip, click it off, wait for normal composer controls to reappear, and log that reverseAPI was unavailable.

**Acceptance criteria:**
1. `flow/agent.py` exports disable helper returning structured result: already off, toggled off, reverseAPI unavailable, DOM fallback used, or failed nonfatal.
2. Agent is disabled immediately before deterministic automation only, not at browser start. `FlowClient.start()` remains one-time browser initialization and is not the hook.
3. `flow/client.py:629` `reset_for_next_job(target_url=...)` is the central per-job integration point. If `target_url` contains a Flow project id, navigate via the existing `page.goto` block at `flow/client.py:646` through `flow/client.py:650`, handle landing/login recovery as needed, then disable Agent before handing client back.
4. `_client_lease(profile, target_url=...)` or equivalent reset-url parameter covers both paths: non-pooled clients call `reset_for_next_job(target_url=target_url)` after browser start and before `yield`; pooled clients call `pool.lease(profile, reset_url=target_url)` so `worker/browser_pool.py:133` reaches the same hook.
5. L2 handlers pass a project-bearing target URL into `_client_lease` before composer automation: extend at `worker/dispatcher.py:387`, insert at `worker/dispatcher.py:415`, remove at `worker/dispatcher.py:442`, and camera at `worker/dispatcher.py:468`. These edits must not touch LP default `worker/dispatcher.py:393`.
6. Agent OFF runs immediately after navigation to the project/edit URL and before any composer interaction. Current post-lease operation navigation at `flow/operations/_base.py:170` through `flow/operations/_base.py:173` may remain as a second navigation, but no composer selector may run before the Unit C reset-url Agent OFF hook.
7. If `target_url` does not contain a project id, reset remains buffer cleanup only; do not invent project state.
8. Every Agent mutation logs profile, project id, previous detection state, method (`reverseAPI` or `DOM`), and whether restoration token is available.
9. Expose `restore_agent_state` helper for opt-in restoration, but do not auto-restore by default unless user config enables it.
10. 403/404/reverseAPI failures are nonfatal only if DOM fallback confirms normal composer controls are visible; otherwise fail with clear diagnostic.
11. Unit F integration smoke covers Agent already-off, DOM fallback, and mutation logging because Unit C is forbidden from editing tests.

**Live verification:**
- Zero-credit Agent toggle capture on `s17524h173` ULTRA.
- Zero-credit normal composer visible after Agent OFF.

### Unit D — Free-Tier L2 Paywall + Runtime Propagation

**Reasoning level:** `xhigh`

**OWNS in Wave 1:**
- `flow/operations/_base.py` — `L2PaywallError`, `CreditBudgetExceeded`, and shared L2 availability helpers
- `flow/operations/_multitab.py` — paywall propagation plus LP fallback purge at `flow/operations/_multitab.py:305` and `flow/operations/_multitab.py:586`
- `worker/dispatcher.py` — narrow result adapters only: single-job dispatch result/catch path `worker/dispatcher.py:577` through `worker/dispatcher.py:590`, alternate single-job generic catch/retry path `worker/dispatcher.py:709` through `worker/dispatcher.py:749`, L1 batch `CreditBudgetExceeded` catch path `worker/dispatcher.py:803` through `worker/dispatcher.py:823`, and L2 batch result inspection path `worker/dispatcher.py:1280` through `worker/dispatcher.py:1330`
- `tests/test_l2_paywall.py` — new root test
- Existing root dispatcher tests only if needed for L2 batch result behavior

**READS:**
- `flow/operations/extend.py`, `flow/operations/camera.py`, `flow/operations/insert.py`, `flow/operations/remove.py` for shared `_base.py` hook placement.
- `server/models/job.py`, `server/db/database.py`, `server/db/job_store.py` only to align with Unit E error contract.

**FORBIDDEN:**
- `server/*`
- `flow/agent.py`
- `flow/client.py`
- `flow/operations/generate.py`
- `worker/dispatcher.py` LP default lines `worker/dispatcher.py:257`, `worker/dispatcher.py:290`, `worker/dispatcher.py:361`, and `worker/dispatcher.py:393` later owned by Unit E
- `worker/dispatcher.py` lease/navigation seam `worker/dispatcher.py:123` through `worker/dispatcher.py:139` and L2 lease call-site lines `worker/dispatcher.py:387`, `worker/dispatcher.py:415`, `worker/dispatcher.py:442`, and `worker/dispatcher.py:468` owned by Unit C

**Acceptance criteria:**
1. Define `L2PaywallError` and `CreditBudgetExceeded` in `flow/operations/_base.py` alongside `LeafLockoutError`; do not create `flow/exceptions.py`.
2. Add `_assert_l2_available(page, op_name, profile)` or equivalent shared helper in `_base.py` and call it before each L2 op attempts old action selectors.
3. Positive paywall signal is only the persistent banner text `Video editing is only available for paid subscribers` plus visible `Upgrade` CTA.
4. Absence of legacy Extend/Insert/Remove/Camera buttons is diagnostic context only, never a positive paywall signal.
5. `L2PaywallError` carries canonical `error_kind="paid_tier_required"`, operation name, profile, and concise user-facing message.
6. `CreditBudgetExceeded` carries `cost`, `budget`, `error_kind="credit_budget_exceeded"`, and message `cost {N} exceeds budget {M}` for Unit B and dispatcher use.
7. Dispatcher single-job paths at `worker/dispatcher.py:577` through `worker/dispatcher.py:590` and `worker/dispatcher.py:709` through `worker/dispatcher.py:749` catch `L2PaywallError` before generic `Exception` and return canonical shape: `status="failed"`, `error_kind="paid_tier_required"`, `error_message`, and backward-compatible `error`.
8. Dispatcher catches `CreditBudgetExceeded` in the single-job result adapter ranges and the L1 batch catch at `worker/dispatcher.py:803` through `worker/dispatcher.py:823`; it returns `status="failed"`, `error_kind="credit_budget_exceeded"`, `error_message="cost {N} exceeds budget {M}"`, and backward-compatible `error`.
9. Operation is carried separately via existing `job.type` or explicit result field, not concatenated as `paid_tier_required_extend`.
10. Profile is not burned, job is not retried, and recaptcha/profile-swap paths are not invoked for paywall or credit-budget failures.
11. `_multitab.py` re-raises `L2PaywallError` before the generic `Exception` catch currently returning `{status: "failed", error: str(exc)}` at `flow/operations/_multitab.py:366` through `flow/operations/_multitab.py:374`.
12. L2 batch dispatcher path at `worker/dispatcher.py:1280` through `worker/dispatcher.py:1330`, especially `worker/dispatcher.py:1309`, explicitly inspects tab results/exceptions and preserves `paid_tier_required` for affected jobs.
13. Replace `_multitab.py` fallback defaults `veo-3.1-fast-lp` at `flow/operations/_multitab.py:305` and `flow/operations/_multitab.py:586` with current free-profile default `veo-3.1-lite`; Unit D handles this because it already owns `_multitab.py`, with no post-D handoff to Unit E.
14. If paid-tier L2 composer later exposes count chips, do not implement here; file a follow-up after live paid capture.
15. Tests cover DOM banner detection, non-paywall missing-button diagnostic, single-job dispatcher handling for paywall and credit-budget failures, and multitab/batch propagation.

**Live verification:**
- Free profile: navigate to existing L2 edit URL and confirm banner + Upgrade CTA without submit.
- Paid profile: verify whether banner is absent and locate paid L2 surfaces without submitting.
- Queue one free-tier L2 job only after Unit E persistence lands; expect failed job with `error_kind="paid_tier_required"`, operation visible, profile still active.

### Unit E — Server Schema + LP Default Purge

**Reasoning level:** `xhigh`

**Runs after Unit D merges** because both units touch `worker/dispatcher.py` in different ranges. Unit E owns LP-default dispatcher lines only after Unit D is merged and must avoid Unit D result adapter ranges `worker/dispatcher.py:577` through `worker/dispatcher.py:590`, `worker/dispatcher.py:709` through `worker/dispatcher.py:749`, `worker/dispatcher.py:803` through `worker/dispatcher.py:823`, and `worker/dispatcher.py:1280` through `worker/dispatcher.py:1330`.

**OWNS in Wave 3:**
- `server/models/job.py`
- `server/db/database.py`
- `server/db/job_store.py`
- `server/routes/jobs.py`
- `worker/dispatcher.py` — LP default refs only near `worker/dispatcher.py:257`, `worker/dispatcher.py:290`, `worker/dispatcher.py:361`, `worker/dispatcher.py:393`; do not edit Unit C lease seam or Unit D single-job/batch result adapter ranges except conflict resolution after merge
- `frontend/js/constants.js`
- `tests/test_default_model.py`
- `tests/test_chain_metadata_backfill.py`
- `tests/test_retarget.py`
- Any affected root `tests/test_*.py` for store/update payload behavior

**FORBIDDEN:**
- `flow/*`
- Unit D paywall detection logic
- Unit C Agent logic
- Unit B composer logic

**Acceptance criteria:**
1. `server/models/job.py` changes `DEFAULT_MODEL` from `veo-3.1-lite-lp` to `veo-3.1-lite`.
2. `TEXT_TO_IMAGE_ROUTE_SENTINEL` no longer uses `veo-3.1-fast-lp`. Prefer removing sentinel branch; if route compatibility needs a sentinel, use a non-LP internal value or explicit omitted-model path.
3. `Job` and `JobUpdate` include nullable `error_kind: str | None` and `error_message: str | None`.
4. `server/db/database.py` fresh DDL adds `error_kind TEXT` and `error_message TEXT` next to existing `error TEXT`; migration uses `_ensure_job_column` near current additive migrations at `server/db/database.py:333` and onward.
5. `server/db/job_store.py` serializes/deserializes both new fields in create/read/update/list paths.
6. Worker update payload can persist `error_kind="paid_tier_required"` and `error_message` from Unit D without dropping fields.
7. `server/routes/jobs.py` no longer routes text-to-image by LP sentinel string from `server/routes/jobs.py:217`; behavior remains backward-compatible for old queued LP strings through Unit A aliases or explicit route mapping.
8. `worker/dispatcher.py` LP default refs at `worker/dispatcher.py:257`, `worker/dispatcher.py:290`, `worker/dispatcher.py:361`, and `worker/dispatcher.py:393` are replaced with non-LP defaults or image-model explicit defaults as appropriate.
9. `frontend/js/constants.js` model options and `DEFAULT_MODEL` stop presenting `Lower Priority` labels and use `veo-3.1-lite` default.
10. Tests update expected defaults from LP to non-LP, while preserving backwards compatibility for old stored jobs/config values.
11. Database migration is additive and safe for existing SQLite DBs.

**Live verification:**
- No credit burn required for schema/default tests.
- After Unit D/E merge, one free-tier L2 failure verifies persistence through API and dashboard surfaces.

### Unit F — Merge-Train Integration Smoke + Docs Sync

**Reasoning level:** `medium`

**Runs after A, B, C, D, and E are merged.** Not parallel.

**OWNS in Wave 4:**
- `tests/test_integration_2026_05_update.py` — new root integration smoke test
- `docs/PROJECT_SPINE.md`
- `docs/FLOW_UI_REFERENCE.md`

**FORBIDDEN:**
- Production code unless merge conflict requires a separate fix PR.

**Acceptance criteria:**
1. Integration smoke test covers model alias path, non-LP default path, composer Video mode/count guard, Agent already-off/no-op path, and canonical L2 paywall result shape using mocks where live credit would be required.
2. `docs/PROJECT_SPINE.md` updates composer UI, models, job data fields, and edit-view/paywall notes if architecture/data model changed.
3. `docs/FLOW_UI_REFERENCE.md` adds `2026-05 composer panel structure` and L2 paywall banner selectors.
4. Final live-verify checklist is documented with exact profile(s), credit budget, and screenshots/network captures required.

---

## 4. Cross-Cutting Contracts

### Error Shape

Canonical paywall failure:
- `status = "failed"`
- `error_kind = "paid_tier_required"`
- `error_message = "<job.type> requires paid-tier Flow video editing for profile <profile>"`
- Operation source: use existing `job.type` when possible; if a result object needs an explicit field, use `op`, not a concatenated error kind.
- Backward compatibility: keep existing `error` populated with concise human-readable text until all frontend surfaces read `error_kind` and `error_message`.

Canonical credit-budget failure:
- `status = "failed"`
- `error_kind = "credit_budget_exceeded"`
- `error_message = "cost {N} exceeds budget {M}"`
- Backward compatibility: keep existing `error` populated with the same concise message until all frontend surfaces read `error_kind` and `error_message`.

### Credit Guard

- Composer automation must verify `x1` before submit.
- If Flow shows `Generating will use N credits`, capture and log `N`.
- Per-job budget is `FLOW_MAX_CREDITS_PER_JOB`, read with `os.environ.get("FLOW_MAX_CREDITS_PER_JOB", "10")`, parsed as `int`, default `10`.
- Unit B blocks submit if count is not one or if preview cost exceeds budget; budget failure raises `CreditBudgetExceeded(cost=N, budget=M)` before any generation request.
- Worker dispatcher maps `CreditBudgetExceeded` to `error_kind="credit_budget_exceeded"` and `error_message="cost {N} exceeds budget {M}"` through Unit D-owned dispatcher ranges.
- Do not claim `Veo 3.1 Lite` is free until live x1 preview/submit verification is complete.

### OAuth / Live Probe Prerequisites

- Flow OAuth is separate from Mail login. Warm Mail login does not guarantee Flow authorization.
- For profile `s17524h173` ULTRA, if Flow asks for AI Test Kitchen OAuth, click `Create with Google Flow` and drive `handle_login_redirect` again.
- Agent reverseAPI capture must prove auth mechanism before coding reverseAPI path.
- Shared live probes should be run once and attached to relevant PRs; do not let each unit infer Flow API shapes independently.

### Tests

- Tests live at repo root as `tests/test_*.py`; do not create `tests/flow/...` or `tests/worker/...`.
- Specific existing fixtures: `tests/test_model_selector.py`, `tests/test_model_selector_lite_fallback.py`, `tests/test_generate.py`, `tests/test_default_model.py`, `tests/test_chain_metadata_backfill.py`, `tests/test_retarget.py`.
- Integration smoke belongs only to Unit F.

### Migrations

- Schema lives in `server/db/database.py`, not `schema.sql`.
- Additive migrations live near `server/db/database.py:333` and use `_ensure_job_column`.

---

## 5. Sequencing

Dependency graph:
- `A -> B` because both edit `flow/operations/generate.py`; Unit A owns model call sites first, Unit B owns composer logic after A merges.
- `D -> B` because Unit D owns `CreditBudgetExceeded` in `flow/operations/_base.py` and dispatcher result plumbing, while Unit B raises that exception from `flow/operations/generate.py`.
- `D -> E` because both edit `worker/dispatcher.py`; Unit D owns single-job and L2 batch result adapters first, Unit E owns LP default refs after D merges.
- `C` is line-disjoint from D/E inside `worker/dispatcher.py`; Unit C owns lease seam lines only, Unit D owns result adapter ranges including `worker/dispatcher.py:803` through `worker/dispatcher.py:823`, and Unit E owns LP default literals.
- `F` runs last after A/B/C/D/E.

Parallelism plan:

| Wave | Units | Parallelism | Notes |
|---|---|---:|---|
| 1 | A, C, D | 3 | C and D both touch `worker/dispatcher.py` but only in explicit, disjoint line ranges |
| 2 | B | 1 | Starts after A and D merge |
| 3 | E | 1 | Starts after D merge |
| 4 | F | 1 | Final reconciliation only |

Branch names:
- `claude/fe-video-models-2026-05`
- `claude/fe-composer-video-2026-05`
- `claude/fe-agent-disable-2026-05`
- `claude/fe-l2-paywall-2026-05`
- `claude/fe-schema-lp-purge-2026-05`
- `claude/fe-2026-05-merge-smoke-docs`

Each PR uses explicit base `master` and project convention from `CLAUDE.md:211` through `CLAUDE.md:212`: one PR per issue with `Closes #N` once issues are minted.

---

## 6. Open Questions for User

1. **Image model default** — recommended: `nano-banana-pro` because it matches Flow default. Alternative: `nano-banana-2` if user prefers lower-cost unverified default. This epic does not modify image registry unless user explicitly changes scope.
2. **Removed: duration** — deferred per discovery because free-tier composer has no selector and paid selector/payload is unknown.
3. **L2 free-tier fail behavior** — recommended: clean `paid_tier_required` failure in Unit D plus follow-up server-side guard when no paid profiles are available.
4. **Veo 3.1 Lite cost** — recommended: re-verify live before any “free engine” claim; defer controlled x1 submit to post-merge live-verify pass with explicit credit budget.
5. **Agent disable timing** — recommended: per-job in `reset_for_next_job`, with mutation log and optional `restore_agent_state`; not per-session at browser start.

---

## Verification Debt

Carry these false-assumption flags from review `docs/REVIEW_FLOW_UPDATE_2026-05-20.md:51` through `docs/REVIEW_FLOW_UPDATE_2026-05-20.md:60` into implementation PR checklists.

| Flag | Source | Handling |
|---|---|---|
| LP deprecation has happened | `docs/REVIEW_FLOW_UPDATE_2026-05-20.md:51` | Verify cheaply via MCP dropdown on ngoctuandt20, `s17524h173` ULTRA, and second free profile; no credit burn. |
| Lite costs 10 credits each | `docs/REVIEW_FLOW_UPDATE_2026-05-20.md:52` | Verify cheaply by setting x1 and reading preview; burn one controlled Lite x1 submit only if preview/metadata cannot prove cost and user approves. |
| Default Image is 1:1 | `docs/REVIEW_FLOW_UPDATE_2026-05-20.md:53` | Verify cheaply with fresh profile or cleared site storage, new project twice; carry as sticky-state assumption in comments if not stable. |
| Default app mode is Image with Nano Banana Pro | `docs/REVIEW_FLOW_UPDATE_2026-05-20.md:54` | Verify cheaply on fresh project/profile and after Video toggle + reload; code must force Video regardless of default. |
| L2 ops fully paywalled for free tier | `docs/REVIEW_FLOW_UPDATE_2026-05-20.md:55` | Verify cheaply via DOM/network navigation on ngoctuandt20, second free profile, and paid profile; no submit needed unless paid L2 surface remains ambiguous. |
| Default video duration ~8s | `docs/REVIEW_FLOW_UPDATE_2026-05-20.md:56` | Carry as deferred assumption; verify cheaply via preview/metadata search, burn one x1 submit only in post-merge live pass if needed. |
| No duration selector in free composer | `docs/REVIEW_FLOW_UPDATE_2026-05-20.md:57` | Verify cheaply with DOM text search for `4s`, `6s`, `8s`, and `duration` across free and paid profiles; no code until captured. |
| Characters uses Nano Banana 2 | `docs/REVIEW_FLOW_UPDATE_2026-05-20.md:58` | Verify cheaply by opening Characters on fresh project/profile and inspecting menu; no epic code depends on it. |
| Scenes is dead route | `docs/REVIEW_FLOW_UPDATE_2026-05-20.md:59` | Verify cheaply on paid and free accounts before citing as hard defer rationale; no credit burn. |
| No hidden Insert/Remove/Camera options | `docs/REVIEW_FLOW_UPDATE_2026-05-20.md:60` | Verify cheaply on paid account with right-click, timeline `+`, kebab, and keyboard probes; avoid submit unless user approves a paid L2 test. |

---

## Changelog vs v1

Critical fixes:
- C1 fixed — removed all active `duration` scope, AC, migration, risk, and open-answer text from v1 lines `docs/PRD_FLOW_FEATURE_UPDATE_2026-05.md:37`, `docs/PRD_FLOW_FEATURE_UPDATE_2026-05.md:99`, `docs/PRD_FLOW_FEATURE_UPDATE_2026-05.md:170`, `docs/PRD_FLOW_FEATURE_UPDATE_2026-05.md:183`, and `docs/PRD_FLOW_FEATURE_UPDATE_2026-05.md:232`; addresses `docs/REVIEW_FLOW_UPDATE_2026-05-20.md:17`.
- C2 fixed — replaced false “all 4 units file-disjoint” fan-out from `docs/PRD_FLOW_FEATURE_UPDATE_2026-05.md:191` with serialized A→B and D→E ownership; addresses `docs/REVIEW_FLOW_UPDATE_2026-05-20.md:18`.
- C3 fixed — replaced nonexistent `FlowClient.navigate_to_project()` from `docs/PRD_FLOW_FEATURE_UPDATE_2026-05.md:123` with `reset_for_next_job(target_url=...)` at `flow/client.py:629`; addresses `docs/REVIEW_FLOW_UPDATE_2026-05-20.md:19`.
- C4 fixed — removed same-origin Agent fetch assumption from `docs/PRD_FLOW_FEATURE_UPDATE_2026-05.md:121`; added live PATCH capture gate and DOM fallback; addresses `docs/REVIEW_FLOW_UPDATE_2026-05-20.md:20`.
- C5 fixed — added Unit E ownership for `error_kind`/`error_message` Pydantic fields, DDL, migration, and store plumbing because current `JobUpdate` lacks those fields and DB has only `error`; addresses `docs/REVIEW_FLOW_UPDATE_2026-05-20.md:21`.
- C6 fixed — added `_multitab.py` re-raise and L2 batch dispatcher result inspection for paywall propagation; addresses `docs/REVIEW_FLOW_UPDATE_2026-05-20.md:22`.
- C7 fixed — removed Unit B L2 `_set_output_count()` AC from v1 `docs/PRD_FLOW_FEATURE_UPDATE_2026-05.md:98`; deferred paid-tier L2 count chip follow-up; addresses `docs/REVIEW_FLOW_UPDATE_2026-05-20.md:23`.

Important fixes:
- I1 fixed — places `L2PaywallError` in `flow/operations/_base.py` beside `LeafLockoutError`, not unowned `flow/exceptions.py`; addresses `docs/REVIEW_FLOW_UPDATE_2026-05-20.md:26`.
- I2 fixed — schema/migration ownership points to `server/db/database.py`, not nonexistent `server/db/schema.sql`; addresses `docs/REVIEW_FLOW_UPDATE_2026-05-20.md:27`.
- I3 fixed — all tests use repo-root `tests/test_*.py` paths, not `tests/flow/...`; addresses `docs/REVIEW_FLOW_UPDATE_2026-05-20.md:28`.
- I4 fixed — narrowed Unit A to video model registry and alias compatibility because image registry already exists in `flow/operations/image.py`; addresses `docs/REVIEW_FLOW_UPDATE_2026-05-20.md:29`.
- I5 fixed — added Unit E for cross-file LP default purge across server, worker, frontend, and affected tests while Unit A keeps alias compatibility; addresses `docs/REVIEW_FLOW_UPDATE_2026-05-20.md:30`.
- I6 fixed — paywall positive signal is banner text plus `Upgrade` CTA only; missing legacy L2 buttons are diagnostics only; addresses `docs/REVIEW_FLOW_UPDATE_2026-05-20.md:31`.
- I7 fixed — canonicalized paywall error as `error_kind="paid_tier_required"` plus operation from `job.type` or `op`, not `paid_tier_required_<op>`; addresses `docs/REVIEW_FLOW_UPDATE_2026-05-20.md:32`.
- I8 fixed — Agent disable now runs immediately before deterministic automation, logs persistent project-state mutation, and exposes opt-in restoration; addresses `docs/REVIEW_FLOW_UPDATE_2026-05-20.md:33`.
- I9 fixed — chip-open AC now uses role/text/menu discovery instead of exact Material icon ligature inventory; addresses `docs/REVIEW_FLOW_UPDATE_2026-05-20.md:34`.
- I10 fixed — moved integration smoke test to Unit F final reconciliation PR, not a parallel unit; addresses `docs/REVIEW_FLOW_UPDATE_2026-05-20.md:35`.
- I11 fixed — raised Unit C reasoning level from `high` to `xhigh`; addresses `docs/REVIEW_FLOW_UPDATE_2026-05-20.md:36`.

---

## Changelog vs v2

- NC1 fixed — Unit C now owns the reachable Agent OFF lease/reset-url seam: `worker/dispatcher.py:123` through `worker/dispatcher.py:139`, L2 lease call sites at `worker/dispatcher.py:387`, `worker/dispatcher.py:415`, `worker/dispatcher.py:442`, and `worker/dispatcher.py:468`, and `worker/browser_pool.py:93` through `worker/browser_pool.py:134`; Agent OFF must run after target URL navigation and before composer interaction.
- NC2 fixed — Unit D now owns single-job paywall/credit result adapters at `worker/dispatcher.py:577` through `worker/dispatcher.py:590` and `worker/dispatcher.py:709` through `worker/dispatcher.py:749`, plus L1 credit-budget catch path `worker/dispatcher.py:803` through `worker/dispatcher.py:823` and L2 batch path `worker/dispatcher.py:1280` through `worker/dispatcher.py:1330`, while Unit E keeps only LP default refs.
- NI1 fixed — Unit A now explicitly removes stale zero-credit free-mode semantics at `flow/model_selector.py:194`, `flow/model_selector.py:391`, and `flow/model_selector.py:444`, removes `free_mode=True` LP coercion at `flow/model_selector.py:205` through `flow/model_selector.py:209`, and maps legacy LP aliases to non-LP models with warnings.
- NI2 fixed — Unit B credit guard now defines `FLOW_MAX_CREDITS_PER_JOB` default `10`, raises `CreditBudgetExceeded(cost=N, budget=M)`, tests cost `40` vs budget `10`, and relies on Unit D dispatcher plumbing for `error_kind="credit_budget_exceeded"`.
- NI3 fixed — Unit D now owns `_multitab.py` LP fallback purge at `flow/operations/_multitab.py:305` and `flow/operations/_multitab.py:586`, replacing `veo-3.1-fast-lp` with `veo-3.1-lite`.
