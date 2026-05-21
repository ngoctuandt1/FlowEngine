# PRD - FlowEngine Wave 5 Feature Wiring (2026-05)

**Status:** planning PRD for Wave 5 worktrees  
**Base:** `master` `689e0e6`  
**Inputs:** Unit H discovery (`docs/discovery_extension_captures.jsonl`, 165 records) and `docs/PRD_FLOW_EXTENSION_2026-05.md`  
**Goal:** wire Flow surfaces beyond PRD v3 without speculative paid/credit mutations.

## Source Anchors
- Unit H marks Characters ready, Trash/Settings partial, Link Share blocked, Voices ready (`docs/PRD_FLOW_EXTENSION_2026-05.md:11`).
- Characters route, composer, Nano Banana 2 model chip, upload/project attach, and @tag banner are observed (`docs/PRD_FLOW_EXTENSION_2026-05.md:24`).
- Trash route exposes Restore All/Delete All but no live mutation body because trash was empty (`docs/PRD_FLOW_EXTENSION_2026-05.md:65`).
- Share modal/mint for projects was not captured; only tool share static paths were observed (`docs/PRD_FLOW_EXTENSION_2026-05.md:94`).
- Voices tab is visible; `projectInitialData.externalReferenceMedia[]` includes 30 `AUDIO` presets (`docs/PRD_FLOW_EXTENSION_2026-05.md:148`, `docs/discovery_extension_captures.jsonl:73`).
- View Settings shows Return silent videos; `videoFx.updateUserSettings` is known but body was not captured in this probe (`docs/PRD_FLOW_EXTENSION_2026-05.md:194`).
- Static endpoint literals are hints only; body/auth must be captured or mocked before reverse-API production use (`docs/PRD_FLOW_EXTENSION_2026-05.md:333`).

## Shared Rules
- No unit invents Flow mutation bodies from static JS literals.
- Reverse API is preferred only when body/auth shape is captured or fully mocked in Engine tests.
- UI fallback remains mandatory for all user-visible Flow actions.
- Tests use mocked HTTP/Playwright; no live Flow credit burn in unit PRs.
- Do not log cookies, bearer tokens, share tokens beyond redacted suffixes, or copied links in full.

## Wave Plan
- **Wave 5a parallel:** Units I, J, K, N.
- **Wave 5b sequential after 5a merges:** Units L, M, O.
- **Dependency note:** Current master already has local `characters` and `projects` modules; units must migrate/extend them, not duplicate routers blindly.
- **Risk note:** `server/models/job.py`, `server/db/job_store.py`, and `server/db/database.py` are likely shared schema touchpoints for deleted/share/voice fields. If implementers cannot keep patches disjoint, merge J before K before L.

## Unit I - Characters Entity

**Reasoning:** `high`  
**Wave:** Wave 5a

**OWNS**
- `server/models/character.py`
- `server/db/character_store.py`
- `server/routes/characters.py`
- `flow/characters.py`
- `flow/operations/character.py`
- `frontend/js/pages/characters.js`
- `tests/test_characters.py`
- `tests/test_flow_characters.py`

**READS**
- `docs/PRD_FLOW_EXTENSION_2026-05.md:24`
- `docs/discovery_extension_captures.jsonl:24`
- `docs/discovery_extension_captures.jsonl:26`
- `docs/discovery_extension_captures.jsonl:27`
- `docs/discovery_extension_captures.jsonl:151`
- `server/db/database.py:125`
- `server/app.py:539`
- `flow/operations/generate.py:282`
- `worker/dispatcher.py:521`

**FORBIDDEN**
- All OWNS of other Wave 5 units. If a shared schema/router file is unavoidable, stop and coordinate before editing.

**Acceptance Criteria**
1. Character schema exposes `id`, `project_id`, `name`, `ref_image_url`, `voice_id`, `created_at`; migrate current local `Character` shape without breaking existing `/api/characters` list/create/get/update/delete contracts (`server/models/character.py:26`, `server/routes/characters.py:57`).
2. CRUD endpoints validate project binding, reject blank names, return stable ISO timestamps, and keep router mounted under `/api/characters` (`server/app.py:539`).
3. Flow character automation opens `/fx/tools/flow/project/{project_id}/characters`, clicks `New character`, fills `div[role="textbox"][contenteditable="true"]`, selects `Nano Banana 2`, and submits `Create` through UI fallback (`docs/PRD_FLOW_EXTENSION_2026-05.md:26`, `docs/PRD_FLOW_EXTENSION_2026-05.md:31`).
4. Composer prompt handling resolves `@tag` mentions to known characters before submit and leaves unresolved tags visible as validation errors; Unit H observed @tag banner copy (`docs/PRD_FLOW_EXTENSION_2026-05.md:35`).
5. Preset prompt helper may use captured `flow.generateCharacterPrompt`; final create must not invent a reverse-API body until `v1/flow/entities` body is captured (`docs/PRD_FLOW_EXTENSION_2026-05.md:44`, `docs/discovery_extension_captures.jsonl:151`).
6. Tests cover schema migration, CRUD, @tag parsing, Nano Banana 2 UI path, and mocked character-create failure/timeout.

## Unit J - Trash + Project Mutations

**Reasoning:** `high`  
**Wave:** Wave 5a

**OWNS**
- `server/models/trash.py`
- `server/db/trash_store.py`
- `server/routes/trash.py`
- `server/models/project.py`
- `server/db/project_store.py`
- `server/routes/projects.py`
- `flow/trash.py`
- `frontend/js/pages/trash.js`
- `tests/test_trash_api.py`
- `tests/test_projects_api.py`
- `tests/test_flow_trash.py`

**READS**
- `docs/PRD_FLOW_EXTENSION_2026-05.md:65`
- `docs/PRD_FLOW_EXTENSION_2026-05.md:121`
- `docs/discovery_extension_captures.jsonl:37`
- `docs/discovery_extension_captures.jsonl:39`
- `docs/discovery_extension_captures.jsonl:153`
- `docs/discovery_extension_captures.jsonl:155`
- `docs/discovery_extension_captures.jsonl:156`
- `docs/discovery_extension_captures.jsonl:157`
- `server/db/database.py:25`
- `server/routes/jobs.py:686`

**FORBIDDEN**
- All OWNS of other Wave 5 units. If a shared schema/router file is unavoidable, stop and coordinate before editing.

**Acceptance Criteria**
1. Soft-delete model adds `deleted_at` semantics for jobs and projects; Project exposes at least `id`, `name`, `deleted_at`, `created_at`, `updated_at` while preserving existing summary/detail API (`server/models/project.py:51`, `server/db/database.py:25`).
2. Normal job/project lists exclude soft-deleted rows by default; explicit trash list returns deleted rows with type, name/prompt, project_id/job_id, and `deleted_at` (`server/routes/jobs.py:559`, `server/db/job_store.py:396`).
3. `POST /api/trash/restore` restores selected `job_ids`, `project_ids`, or `all`; endpoint is idempotent and returns restored counts.
4. `DELETE /api/trash/permanent` permanently deletes selected trash rows only; requires explicit ids or `all=true` and never deletes active rows.
5. Flow trash automation opens `/fx/tools/flow/project/{project_id}/trash`, verifies `Trash`, then clicks `Restore All` or `Delete All` only after caller intent is explicit (`docs/PRD_FLOW_EXTENSION_2026-05.md:67`, `docs/PRD_FLOW_EXTENSION_2026-05.md:70`).
6. Reverse-API trash/project mutation code treats static endpoints as hints only until bodies are captured (`docs/discovery_extension_captures.jsonl:153`, `docs/discovery_extension_captures.jsonl:155`).
7. Tests cover soft-delete filtering, restore idempotency, permanent-delete guardrails, and Flow UI fallback with mocked Playwright.

## Unit K - Link Sharing

**Reasoning:** `high`  
**Wave:** Wave 5a

**OWNS**
- `server/models/share.py`
- `server/db/share_store.py`
- `server/routes/share.py`
- `flow/share.py`
- `frontend/js/pages/job-share.js`
- `tests/test_share_api.py`
- `tests/test_flow_share.py`

**READS**
- `docs/PRD_FLOW_EXTENSION_2026-05.md:94`
- `docs/discovery_extension_captures.jsonl:98`
- `docs/discovery_extension_captures.jsonl:160`
- `server/models/job.py:208`
- `server/routes/jobs.py:668`
- `server/db/job_store.py:31`

**FORBIDDEN**
- All OWNS of other Wave 5 units. If a shared schema/router file is unavoidable, stop and coordinate before editing.

**Acceptance Criteria**
1. Job share metadata stores nullable `share_token`, `share_url`, `shared_at`, `revoked_at`; public job reads never mint tokens implicitly (`server/models/job.py:208`, `server/db/job_store.py:31`).
2. `POST /api/jobs/{id}/share` mints or returns current share token, persists it, and returns a copyable URL; missing job returns 404 (`server/routes/jobs.py:668`).
3. `DELETE /api/jobs/{id}/share` revokes current token and is idempotent; revoked links are no longer returned by job detail.
4. Flow share automation clicks the Flow share button, waits for Copy link modal, copies/extracts one HTTPS URL, and stores only the token/URL needed by Engine (`docs/PRD_FLOW_EXTENSION_2026-05.md:96`, `docs/PRD_FLOW_EXTENSION_2026-05.md:100`).
5. Do not use `flowAgent:shareApplet` for project/job sharing except as discovery evidence; Unit H captured it as tool-sharing static literal only (`docs/PRD_FLOW_EXTENSION_2026-05.md:107`, `docs/discovery_extension_captures.jsonl:160`).
6. Tests mock Flow HTTP/clipboard and prove mint, repeat-mint, revoke, no-share-button fallback, and no secret/token leakage in logs.

## Unit L - Voices Ingredients

**Reasoning:** `high`  
**Wave:** Wave 5b

**OWNS**
- `server/models/asset.py`
- `server/db/asset_store.py`
- `server/routes/assets.py`
- `server/models/job.py`
- `server/db/job_store.py`
- `worker/dispatcher.py`
- `flow/operations/generate.py`
- `frontend/js/pages/create-job.js`
- `tests/test_assets_api.py`
- `tests/test_jobs_api.py`
- `tests/test_generate_voices.py`

**READS**
- `docs/PRD_FLOW_EXTENSION_2026-05.md:148`
- `docs/discovery_extension_captures.jsonl:73`
- `server/models/job.py:104`
- `server/db/job_store.py:271`
- `flow/operations/generate.py:282`
- `tests/test_composer_chip.py:125`

**FORBIDDEN**
- All OWNS of other Wave 5 units. If a shared schema/router file is unavoidable, stop and coordinate before editing.

**Acceptance Criteria**
1. Server asset model supports new asset type `voice` with `id`, `name`, `description`, `sample_url`, `source`, `created_at`; preset Flow voices can be read-only external assets (`docs/PRD_FLOW_EXTENSION_2026-05.md:161`).
2. `JobCreate.voice_asset_id` is accepted for supported L1 video jobs, persisted, surfaced on `Job`, and rejected for unsupported types (`server/models/job.py:104`, `server/db/job_store.py:271`).
3. Voice catalog import/list maps `projectContents.externalReferenceMedia[]` records where `mediaType == "AUDIO"` and preserves `mediaId`, `workflowDisplayName`, `generatedAudio.name`, description, and `audioSamplePath` (`docs/discovery_extension_captures.jsonl:73`).
4. Flow composer automation opens `+`, switches to `Voices`, selects requested asset, and verifies selected chip/state before submit (`docs/PRD_FLOW_EXTENSION_2026-05.md:154`).
5. Submit body reverse-API path is not invented; UI path is canonical until voice attach body is captured (`docs/PRD_FLOW_EXTENSION_2026-05.md:186`, `docs/PRD_FLOW_EXTENSION_2026-05.md:337`).
6. Tests cover asset CRUD/list, JobCreate validation, dispatcher payload propagation, and mocked composer selection.

## Unit M - View Settings Persistence

**Reasoning:** `high`  
**Wave:** Wave 5b

**OWNS**
- `server/models/settings.py`
- `server/db/settings_store.py`
- `server/routes/settings.py`
- `flow/settings.py`
- `tests/test_settings_api.py`
- `tests/test_flow_settings.py`

**READS**
- `docs/PRD_FLOW_EXTENSION_2026-05.md:194`
- `docs/discovery_extension_captures.jsonl:89`
- `docs/discovery_extension_captures.jsonl:91`
- `server/routes/settings.py:26`
- `server/db/settings_store.py:43`
- `server/models/settings.py:21`

**FORBIDDEN**
- All OWNS of other Wave 5 units. If a shared schema/router file is unavoidable, stop and coordinate before editing.

**Acceptance Criteria**
1. Add `POST /api/settings` body with `return_silent_videos: bool = False` default, plus optional passthrough keys for Flow view settings (`server/routes/settings.py:26`, `server/models/settings.py:21`).
2. Server proxy forwards to Flow tRPC `videoFx.updateUserSettings` with same-origin/auth context abstraction; tests use mocked HTTP only (`docs/PRD_FLOW_EXTENSION_2026-05.md:210`).
3. Default `return_silent_videos=False` matches observed Flow default intent and never silently flips existing users to silent output (`docs/PRD_FLOW_EXTENSION_2026-05.md:202`).
4. GET/read path exposes effective settings, merging persisted Engine defaults with Flow `getUserSettings` when available (`docs/PRD_FLOW_EXTENSION_2026-05.md:208`).
5. Failures from Flow proxy return structured 502/504 errors with `error_kind`, no raw cookies/tokens in responses or logs.
6. Tests cover default body, forward payload shape, timeout, Flow error, and compatibility with existing `/api/settings/ai` endpoints (`server/routes/settings.py:53`).

## Unit N - AI Locator Phase G2

**Reasoning:** `xhigh`  
**Wave:** Wave 5a

**OWNS**
- `flow/landing.py`
- `flow/model_selector.py`
- `flow/operations/generate.py`
- `flow/edit_menu.py`
- `tests/test_ai_locator_integration.py`
- `tests/test_landing.py`
- `tests/test_model_selector.py`
- `tests/test_composer_chip.py`

**READS**
- `docs/AI_LOCATOR_DESIGN_2026-05.md`
- `flow/ai_locator.py:75`
- `flow/landing.py:186`
- `flow/model_selector.py:321`
- `flow/operations/generate.py:282`
- `flow/operations/_base.py:882`
- `tests/test_ai_locator.py:96`

**FORBIDDEN**
- All OWNS of other Wave 5 units. If a shared schema/router file is unavoidable, stop and coordinate before editing.

**Acceptance Criteria**
1. Fast-path behavior stays unchanged: current hardcoded selectors run first for landing CTA, model chip-open, composer Video tab switch, and edit-view kebab (`flow/landing.py:186`, `flow/model_selector.py:321`, `flow/operations/generate.py:282`).
2. AI fallback calls `flow.ai_locator.ai_locate` only after every existing candidate fails and only when `FLOW_AI_LOCATOR_ENABLED=true` (`flow/ai_locator.py:75`, `flow/ai_locator.py:101`).
3. Landing CTA fallback returns same locator/click contract as `_find_landing_cta`; no AI call occurs when a candidate is visible (`flow/landing.py:187`).
4. Model selector fallback opens the chip/menu without changing registry, model aliases, or credit logic (`flow/model_selector.py:39`).
5. Composer Video tab fallback only targets tab switching and does not change Frames/Ingredients/upload/x1 enforcement (`flow/operations/generate.py:282`).
6. Edit-view kebab helper is isolated and reused by future share/trash ops; if no edit-view helper exists, add a new small module instead of touching L2 submit code.
7. Tests prove opt-in gating, no-call fast path, all-candidates-fail fallback, cache/key usage, and graceful miss when AI returns NOT_FOUND.

## Unit O - Reverse-API Priority Refactor

**Reasoning:** `xhigh`  
**Wave:** Wave 5b

**OWNS**
- `flow/operations/_base.py`
- `flow/operations/extend.py`
- `flow/operations/insert.py`
- `flow/operations/remove.py`
- `flow/operations/camera.py`
- `flow/agent.py`
- `flow/reverse_api.py`
- `flow/share.py`
- `flow/characters.py`
- `tests/test_reverse_api_priority.py`
- `tests/test_agent.py`
- `tests/test_extend_api.py`
- `tests/test_insert_api.py`
- `tests/test_remove_api.py`
- `tests/test_camera_api.py`

**READS**
- `docs/PRD_FLOW_EXTENSION_2026-05.md:329`
- `docs/discovery_extension_captures.jsonl:151`
- `docs/discovery_extension_captures.jsonl:153`
- `docs/discovery_extension_captures.jsonl:155`
- `docs/discovery_extension_captures.jsonl:160`
- `flow/operations/extend.py:344`
- `flow/operations/insert.py:314`
- `flow/operations/remove.py:310`
- `flow/operations/camera.py:494`
- `flow/agent.py:289`

**FORBIDDEN**
- All OWNS of other Wave 5 units. If a shared schema/router file is unavoidable, stop and coordinate before editing.

**Acceptance Criteria**
1. `FLOW_PREFER_REVERSE_API` defaults true; when false, existing UI-click behavior remains primary and no reverse attempt is made.
2. Extend/insert/remove/camera hot paths try reverse API before UI click, using captured templates/endpoints where available, then fall back to UI on recoverable reverse errors (`flow/operations/extend.py:344`, `flow/operations/insert.py:314`, `flow/operations/remove.py:310`, `flow/operations/camera.py:494`).
3. Agent toggle keeps reverse-API first, then DOM fallback, with consistent status/result logging and mocked HTTP tests (`flow/agent.py:289`, `flow/agent.py:684`).
4. Share-link mint and character create use reverse-API first only for captured safe bodies; otherwise they skip directly to UI fallback and log `reverse_api_unavailable` (`docs/PRD_FLOW_EXTENSION_2026-05.md:333`).
5. Common helper centralizes preference gate, timeout, recoverable vs fatal errors, redacted logging, and metric/debug metadata; no operation swallows fatal validation/paywall errors (`flow/operations/_base.py:68`).
6. Tests use mocked HTTP/templates to verify reverse-first success, reverse failure to UI fallback, env-disabled UI-first path, and no raw auth/token logging.

## Prompt Files
- `C:/Users/Tuan/AppData/Local/Temp/codex_unit_i.txt`
- `C:/Users/Tuan/AppData/Local/Temp/codex_unit_j.txt`
- `C:/Users/Tuan/AppData/Local/Temp/codex_unit_k.txt`
- `C:/Users/Tuan/AppData/Local/Temp/codex_unit_l.txt`
- `C:/Users/Tuan/AppData/Local/Temp/codex_unit_m.txt`
- `C:/Users/Tuan/AppData/Local/Temp/codex_unit_n.txt`
- `C:/Users/Tuan/AppData/Local/Temp/codex_unit_o.txt`
