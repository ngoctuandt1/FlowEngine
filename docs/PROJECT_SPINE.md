# PROJECT_SPINE

> This is the canonical spine. Read first. Update whenever architecture, code map, or deploy topology changes.

- Scope: repo `master` at `77c16bb155f15f24e29d07e5c02c138f7c934a3a` plus the 2026-05-01 public cutover state.
- Purpose: one 5-minute sync doc for future feature work.
- Not here: deep rationale lives in [docs/DESIGN.md](DESIGN.md), invariants/test contract in [docs/SPEC.md](SPEC.md), and roadmap in [docs/WORKPLAN.md](WORKPLAN.md).

## 1. What is FlowEngine

FlowEngine is a browser-automation engine for Google Flow (`https://labs.google/fx/tools/flow`): a FastAPI server queues jobs, one or more workers claim them over HTTP, and Playwright-driven Chrome profiles execute Flow operations such as text-to-video, frames-to-video, ingredients-to-video, text-to-image, extend, insert, remove, and camera-move. It is multi-account by design, chain-aware by design, and currently exposed publicly at `https://ai.hassio.io.vn`.

## 2. Architecture

FlowEngine is four layers with a strict split: the frontend is a vanilla JS SPA, the server owns HTTP/API/SQLite, the worker owns claim/dispatch/profile state, and the `flow/` package owns Google Flow browser automation. The frontend talks to the server over HTTP plus `WS /ws/jobs`; workers talk to the server over authenticated HTTP poll/update/heartbeat calls.

```text
frontend/ (index.html + js/pages/*)
    |  HTTP GET/POST/DELETE + WebSocket /ws/jobs
    v
server/ (FastAPI + SQLite)
    |  HTTP poll/update/heartbeat on /api/worker/*
    v
worker/ (claim loop + dispatcher + profile/project guards)
    |  Playwright / FlowClient calls
    v
flow/ (login + navigation + submit + wait + download + operations)
    |  Chrome profile + Google Flow DOM/network
    v
labs.google/fx/tools/flow
```

Component summary:

- `server/`: FastAPI app, route registration, dashboard auth gate, SQLite schema/init, REST + WebSocket surface.
- `worker/`: polling worker that claims the next eligible job, acquires profile/project guards, dispatches by job type, and reports completion/failure.
- `flow/`: browser automation layer around Google Flow, including login recovery, URL/media-id helpers, stable selectors, wait logic, downloads, and operation modules.
- `frontend/`: static SPA served by the FastAPI app; route state is hash-based and pages mostly consume `frontend/js/api.js` plus `frontend/js/ws.js`.

## 3. Job system invariants

These are the chain rules that future work must preserve. Do not restate or fork them elsewhere; the source of truth is [docs/SPEC.md](SPEC.md), especially [INV-1 at `docs/SPEC.md:52`](SPEC.md#L52), [INV-2 at `docs/SPEC.md:63`](SPEC.md#L63), [INV-3 at `docs/SPEC.md:73`](SPEC.md#L73), [INV-4 at `docs/SPEC.md:83`](SPEC.md#L83), and [INV-5 at `docs/SPEC.md:90`](SPEC.md#L90).

- `INV-1` + `INV-3`: L1 creates the project; every L2+ child inherits the completed parent's `project_url`, `media_id`, `edit_url`, and `profile` at claim time. Main enforcement: [server/db/job_store.py:226](../server/db/job_store.py#L226), [server/models/job.py:81](../server/models/job.py#L81), [docs/SPEC.md:73](SPEC.md#L73).
- `INV-1`: the same `profile` must hold across the entire chain. Different Google account = different project ownership = Flow 404 / redirect failure. Main enforcement: [server/db/job_store.py:232](../server/db/job_store.py#L232), [docs/SPEC.md:52](SPEC.md#L52).
- `INV-2`: target a clip only via `edit_url` (`/edit/{media_id}`); never via `video_index`, generic grid-card order, or DOM card counting. Main enforcement: [flow/navigation.py:38](../flow/navigation.py#L38), [flow/operations/_base.py:43](../flow/operations/_base.py#L43), [docs/SPEC.md:63](SPEC.md#L63).
- `INV-4`: execution is serial per `project_url`. There are two guards: claim-time SQL refuses a second active job on the same project, and the worker still acquires `ProjectLock` before L2+ work. Main enforcement: [server/db/job_store.py:226](../server/db/job_store.py#L226), [worker/project_lock.py:13](../worker/project_lock.py#L13), [docs/SPEC.md:83](SPEC.md#L83).
- `INV-5`: `media_id` is re-extracted after every operation and stored back; downstream work must consume the final stored value, not assume stability across ops. Main enforcement: [flow/operations/_base.py:619](../flow/operations/_base.py#L619), [flow/operations/_base.py:706](../flow/operations/_base.py#L706), [docs/SPEC.md:90](SPEC.md#L90).

Read before changing chain logic:

- [docs/SPEC.md](SPEC.md)
- [docs/FLOW_MULTILEVEL_JOBS.md](FLOW_MULTILEVEL_JOBS.md)
- [docs/FLOW_PIPELINE_KNOWLEDGE.md](FLOW_PIPELINE_KNOWLEDGE.md)

## 4. Production deploy topology

This section is the current runtime topology, not just the repo template. The repo tracks the server service template; the public cutover details come from the 2026-05-01 session report plus operator state.

| Item | Current state | Source |
|---|---|---|
| Public URL | `https://ai.hassio.io.vn` | [session report:1-3](session-reports/2026-05-01_web-ai-hassio-flowengine-cutover.md#L1) |
| Dashboard password | `1` | Operator-stated current runtime |
| Edge routing | Cloudflare Tunnel, token-mode, dashboard-managed | Operator-stated current runtime; cutover kept the existing tunnel route on port `8899` per [session report:10-12](session-reports/2026-05-01_web-ai-hassio-flowengine-cutover.md#L10) |
| Tunnel target | `192.168.86.42:8899` | Operator-stated current runtime |
| Server bind used for public cutover | FlowEngine bound on `0.0.0.0:8899` to avoid moving the tunnel route | [session report:53-56](session-reports/2026-05-01_web-ai-hassio-flowengine-cutover.md#L53), [CLAUDE.md:191-199](../CLAUDE.md#L191) |
| systemd units | `flowengine-server`, `flowengine-worker`, `flowengine-xvfb` on Debian | Operator-stated current runtime; repo only ships the server unit template at [deploy/debian/flowengine-server.service:1](../deploy/debian/flowengine-server.service#L1) |
| Install path | `/opt/flowengine` | [deploy/debian/flowengine-server.service:11](../deploy/debian/flowengine-server.service#L11), [deploy/debian/README.md:43](../deploy/debian/README.md#L43) |
| Env file | `/etc/flowengine/flowengine.env` | [deploy/debian/flowengine-server.service:12](../deploy/debian/flowengine-server.service#L12), [deploy/debian/README.md:46](../deploy/debian/README.md#L46) |
| Dashboard auth switch | `DASHBOARD_PASSWORD` enables signed-cookie auth and middleware | [server/dashboard_auth.py:36](../server/dashboard_auth.py#L36), [server/app.py:98](../server/app.py#L98) |
| Proxy handling | `TRUST_PROXY_HEADERS=1` plus uvicorn proxy-header support are required so auth sees HTTPS correctly | [server/dashboard_auth.py:38](../server/dashboard_auth.py#L38), [deploy/debian/flowengine-server.service:15](../deploy/debian/flowengine-server.service#L15), [CLAUDE.md:220-223](../CLAUDE.md#L220) |
| Archived old engine | `/opt/_archive/video-ai-studio.20260501` (and `/opt/_archive/video-ai.20260501`) | [session report:61-65](session-reports/2026-05-01_web-ai-hassio-flowengine-cutover.md#L61) |

Important distinction:

- Repo template: [deploy/debian/flowengine-server.service](../deploy/debian/flowengine-server.service) still shows `127.0.0.1:8080` behind nginx/Caddy.
- Public runtime cutover: moved FlowEngine onto `0.0.0.0:8899` to preserve the existing Cloudflare Tunnel route for `ai.hassio.io.vn`.

## 5. Data model

Top fields only. For any schema change, read both the Pydantic model and the backing store/DDL before editing API, worker, or UI logic.

| Entity | Key model fields | Key DB fields | Full schema |
|---|---|---|---|
| Job | `JobCreate`/`Job`/`JobUpdate` carry `type: JobType`, `status: JobStatus`, `job_level: int`, `parent_job_id: str?`, `chain_id: str?`, `profile: str?`, `project_url: str?`, `media_id: str?`, `edit_url: str?`, `prompt: str?`, `model: str`, `aspect_ratio: str`, `bbox: BBox?`, `direction: str?`, `start_image_path: str?`, `end_image_path: str?`, `ingredient_image_paths: list[str]`, `ref_image_path: str?`, `output_files: list[str]`, `generation_id: str?`, `worker_id: str?`, `claimed_at/completed_at: datetime?`, `error: str?`, `created_at/updated_at: datetime` | `jobs` stores the same shape in SQLite-friendly form: `id/type/status TEXT`, `job_level INTEGER`, `parent_job_id/chain_id/profile/project_url/media_id/edit_url TEXT`, `bbox_json/ingredient_image_paths_json/output_files_json TEXT`, `generation_id/worker_id/error TEXT`, `claimed_at/completed_at/created_at/updated_at TEXT`, plus `safety_filter TEXT` for template/media compatibility | Model: [server/models/job.py:74](../server/models/job.py#L74), [server/models/job.py:168](../server/models/job.py#L168), [server/models/job.py:219](../server/models/job.py#L219). DB: [server/db/database.py:22](../server/db/database.py#L22). Store logic: [server/db/job_store.py:1](../server/db/job_store.py#L1) |
| Chain | API uses `Chain`, `ChainAggregate`, and `ChainCreateResponse`: `id`, `profile`, `created_at`, aggregate `status`, aggregate `progress`, and ordered `jobs` ids | `chains` table still has `id/profile/project_url/media_id/status/created_at/updated_at`; current chain store treats the row as immutable metadata and computes status/progress from `jobs` on read | Model: [server/models/chain.py:18](../server/models/chain.py#L18), [server/models/chain.py:36](../server/models/chain.py#L36), [server/models/chain.py:50](../server/models/chain.py#L50). DB: [server/db/database.py:12](../server/db/database.py#L12). Aggregate rules: [server/db/chain_store.py:1](../server/db/chain_store.py#L1) |
| Profile | `Profile` and `ProfileUpdate` carry `name`, `google_account`, `locale`, `tier`, `status`, `current_job_id`, `worker_id`, `last_used_at`, `created_at` | `profiles` table mirrors `name/google_account/locale/tier/status/current_job_id/worker_id/last_used_at/created_at` | Model: [server/models/profile.py:15](../server/models/profile.py#L15), [server/models/profile.py:28](../server/models/profile.py#L28). DB: [server/db/database.py:69](../server/db/database.py#L69). Store logic: [server/db/profile_store.py:1](../server/db/profile_store.py#L1) |
| Character | `CharacterCreate`, `CharacterUpdate`, and `Character` center on `id`, `name`, `description`, `image_paths: list[str]`, `created_at`, `updated_at` | `characters` stores `id/name/description/image_paths/created_at/updated_at`, with `image_paths` JSON-serialized in SQLite | Model: [server/models/character.py:10](../server/models/character.py#L10), [server/models/character.py:18](../server/models/character.py#L18), [server/models/character.py:26](../server/models/character.py#L26). DB: [server/db/database.py:81](../server/db/database.py#L81). Store logic: [server/db/character_store.py:1](../server/db/character_store.py#L1) |
| Template | `TemplateStep` describes placeholder-friendly job-step fields (`type`, `prompt`, `model`, `aspect_ratio`, `bbox`, `direction`, image refs, `safety_filter`); `Template` adds `id/name/description/steps/created_at/updated_at`; `TemplateInstantiate` adds `template_id` + `vars` | `templates` stores `id/name/description/steps_json/created_at/updated_at` | Model: [server/models/template.py:14](../server/models/template.py#L14), [server/models/template.py:41](../server/models/template.py#L41), [server/models/template.py:52](../server/models/template.py#L52). DB: [server/db/database.py:139](../server/db/database.py#L139). Store logic: [server/db/template_store.py:1](../server/db/template_store.py#L1) |

## 6. Code map

Every file below was read directly when this spine was written.

### Server entrypoints and auth

| File | Responsibility |
|---|---|
| [server/app.py](../server/app.py) | Builds the FastAPI app, initializes DB, mounts static frontend assets, wires dashboard auth, and registers routers. |
| [server/dashboard_auth.py](../server/dashboard_auth.py) | Implements the optional signed-cookie dashboard password gate plus `/login`, `/api/auth/login`, and `/api/auth/logout`. |
| [server/auth.py](../server/auth.py) | Enforces bearer-token auth for privileged `/api/worker/*` endpoints. |
| [server/config.py](../server/config.py) | Loads env/config defaults, data paths, database path, and logging setup. |

### Server routes

| File | Responsibility |
|---|---|
| [server/routes/jobs.py](../server/routes/jobs.py) | Public job and chain CRUD surface plus queue counts/recovery and broadcast hooks. |
| [server/routes/worker.py](../server/routes/worker.py) | Worker claim/update/heartbeat endpoints behind `require_worker_token`. |
| [server/routes/profiles.py](../server/routes/profiles.py) | Profile registration, update, fetch, list, and per-profile job listing. |
| [server/routes/characters.py](../server/routes/characters.py) | Character-library CRUD with upload-path normalization and existence checks. |
| [server/routes/templates.py](../server/routes/templates.py) | Workflow-template CRUD plus instantiate-to-chain bridge. |
| [server/routes/tts.py](../server/routes/tts.py) | Edge-TTS synthesis endpoint that writes audio assets under `downloads/tts`. |
| [server/routes/media_cut.py](../server/routes/media_cut.py) | ffmpeg-backed video trim endpoint. |
| [server/routes/media_merge.py](../server/routes/media_merge.py) | ffmpeg-backed multi-source merge endpoint. |
| [server/routes/media_fetch.py](../server/routes/media_fetch.py) | Remote media downloader with validation/SSRF guardrails around `yt-dlp`. |
| [server/routes/retarget.py](../server/routes/retarget.py) | Extracts a representative frame from a source video and queues a `frames-to-video` retarget job. |
| [server/routes/llm.py](../server/routes/llm.py) | LLM-backed prompt helper endpoints for auto-prompt, expansion, and shot lists. |
| [server/routes/prompt_builder.py](../server/routes/prompt_builder.py) | Deterministic prompt-assembly endpoint from structured prompt parts. |
| [server/routes/product_pipeline.py](../server/routes/product_pipeline.py) | Converts a product image + brief into a fixed multi-step chain request. |
| [server/routes/uploads.py](../server/routes/uploads.py) | Validates image uploads by magic bytes and stores them under `FLOW_UPLOAD_DIR`. |
| [server/routes/ws.py](../server/routes/ws.py) | WebSocket job-update channel with keepalive pings and broadcast fan-out. |

### Server model and DB layer

| File | Responsibility |
|---|---|
| [server/models/job.py](../server/models/job.py) | Defines job enums, validators, request/response shapes, and camera preset constants mirrored by the frontend. |
| [server/db/database.py](../server/db/database.py) | Creates SQLite tables/columns on startup and exposes the shared async DB context manager. |
| [server/db/chain_store.py](../server/db/chain_store.py) | Persists immutable chain rows and derives chain status/progress from jobs on read. |
| [server/db/job_store.py](../server/db/job_store.py) | Owns job CRUD, stale-job recovery, claim ordering, child inheritance, and terminal-state release behavior. |
| [server/db/profile_store.py](../server/db/profile_store.py) | Owns profile CRUD and worker-scoped profile selection. |
| [server/db/character_store.py](../server/db/character_store.py) | Owns character CRUD and JSON serialization for `image_paths`. |
| [server/db/template_store.py](../server/db/template_store.py) | Owns template CRUD, placeholder validation/substitution, and template instantiation. |

### Worker

| File | Responsibility |
|---|---|
| [worker/main.py](../worker/main.py) | Worker process entrypoint: claim loop, heartbeat, concurrency bookkeeping, and shutdown flow. |
| [worker/dispatcher.py](../worker/dispatcher.py) | Maps `job.type` to handler, acquires/releases project/profile guards, and translates handler results to update payloads. |
| [worker/profile_manager.py](../worker/profile_manager.py) | Tracks which worker-owned Chrome profiles are available versus busy. |
| [worker/project_lock.py](../worker/project_lock.py) | Prevents concurrent L2+ work on the same `project_url` inside one worker process. |
| [worker/profile_swapper.py](../worker/profile_swapper.py) | Archives burned profiles and swaps in fresh credentials after reCAPTCHA damage. |
| [worker/remote_api.py](../worker/remote_api.py) | Async HTTP client for `/api/worker/*` claim/update/heartbeat calls. |
| [worker/browser_pool.py](../worker/browser_pool.py) | Optional warm browser/client pool keyed by profile to avoid per-job Chrome startup cost. |

### Flow core

| File | Responsibility |
|---|---|
| [flow/client.py](../flow/client.py) | Launches and owns the Playwright/Chrome session for one Flow profile. |
| [flow/login.py](../flow/login.py) | Detects Google sign-in redirects and performs credential/TOTP login when needed. |
| [flow/landing.py](../flow/landing.py) | Recovers from the Flow marketing landing page and CTA misroutes back into the app. |
| [flow/navigation.py](../flow/navigation.py) | Builds Flow URLs and extracts project/media identifiers from URLs. |
| [flow/model_selector.py](../flow/model_selector.py) | Selects the requested Flow model from the live DOM in a version-aware way. |
| [flow/submit.py](../flow/submit.py) | Finds the real generate/submit button and confirms that submission was accepted. |
| [flow/wait.py](../flow/wait.py) | Waits for completion/failure by combining reverse-API, network, DOM, and reCAPTCHA signals. |
| [flow/media_id.py](../flow/media_id.py) | Normalizes and extracts `media_id` values from URLs and filenames. |
| [flow/recaptcha.py](../flow/recaptcha.py) | Detects visible and invisible reCAPTCHA blocks from DOM and network evidence. |
| [flow/failure_capture.py](../flow/failure_capture.py) | Adds non-blocking screenshot/HTML/network capture hooks to failure paths. |
| [flow/download.py](../flow/download.py) | Downloads finished media via Flow APIs first, with UI fallbacks when needed. |

### Flow operations

| File | Responsibility |
|---|---|
| [flow/operations/_base.py](../flow/operations/_base.py) | Shared L2 helpers for navigate-to-edit, clip activation, finalize/store, and media-id propagation. |
| [flow/operations/generate.py](../flow/operations/generate.py) | L1 text-to-video operation from the Flow homepage/new-project flow. |
| [flow/operations/extend.py](../flow/operations/extend.py) | L2 extend-video operation on an existing clip edit page. |
| [flow/operations/insert.py](../flow/operations/insert.py) | L2 insert-object operation with prompt plus bounding box. |
| [flow/operations/remove.py](../flow/operations/remove.py) | L2 remove-object operation with bounding box targeting. |
| [flow/operations/camera.py](../flow/operations/camera.py) | L2 camera-move operation that selects a visual preset instead of typing a prompt. |
| [flow/operations/frames_to_video.py](../flow/operations/frames_to_video.py) | L1 frames-to-video operation using start/end frames in the Flow composer. |
| [flow/operations/ingredients.py](../flow/operations/ingredients.py) | L1 ingredients-to-video operation using multiple reference images. |
| [flow/operations/image.py](../flow/operations/image.py) | L1 text-to-image operation using Flow image output mode and image-model selection. |

### Frontend shell

| File | Responsibility |
|---|---|
| [frontend/index.html](../frontend/index.html) | Declares route anchors, sidebar nav, shared app shell, and script load order for every page module. |
| [frontend/js/app.js](../frontend/js/app.js) | Hash router, page registry, modal/toast helpers, and shared UI utilities. |
| [frontend/js/api.js](../frontend/js/api.js) | Browser-side REST client wrappers for jobs, chains, profiles, and uploads. |
| [frontend/js/ws.js](../frontend/js/ws.js) | Browser-side WebSocket client with reconnect and event fan-out. |
| [frontend/js/constants.js](../frontend/js/constants.js) | Mirrors job types, models, aspect ratios, and camera presets from backend/Flow code. |

### Frontend pages

| File | Responsibility |
|---|---|
| [frontend/js/pages/home.js](../frontend/js/pages/home.js) | Flow-style recent-output landing page with WS-driven refresh. |
| [frontend/js/pages/dashboard.js](../frontend/js/pages/dashboard.js) | High-level counts and recent jobs dashboard with recover/delete actions. |
| [frontend/js/pages/create-job.js](../frontend/js/pages/create-job.js) | Single-job creator plus prompt-batch creator for supported L1 types. |
| [frontend/js/pages/chain-builder.js](../frontend/js/pages/chain-builder.js) | Visual builder for ordered chain steps posted to `/api/chains`. |
| [frontend/js/pages/profiles.js](../frontend/js/pages/profiles.js) | Profile list/add UI, still carrying some stale quarantine/activate client calls. |
| [frontend/js/pages/settings.js](../frontend/js/pages/settings.js) | Health/config snapshot and admin recovery/job-control page. |
| [frontend/js/pages/characters.js](../frontend/js/pages/characters.js) | Character library CRUD/editor with upload support. |
| [frontend/js/pages/workflows.js](../frontend/js/pages/workflows.js) | Template runner plus LLM prompt-helper UI. |
| [frontend/js/pages/media-tools.js](../frontend/js/pages/media-tools.js) | UI for media cut, merge, fetch-url, and retarget endpoints. |
| [frontend/js/pages/tts.js](../frontend/js/pages/tts.js) | Text-to-speech UI. |
| [frontend/js/pages/jobs.js](../frontend/js/pages/jobs.js) | Full job history with filters, live refresh, retry, and delete flows. |
| [frontend/js/pages/gallery.js](../frontend/js/pages/gallery.js) | Completed-media browser with filters and preview modal. |
| [frontend/js/pages/batch-queue.js](../frontend/js/pages/batch-queue.js) | Bulk queue UI for many prompt-driven L1 jobs with live local/server state. |
| [frontend/js/pages/job-detail.js](../frontend/js/pages/job-detail.js) | Single-job detail view with parent/children context and retry/delete actions. |
| [frontend/js/pages/chain-tree.js](../frontend/js/pages/chain-tree.js) | Top-down visualization of chain dependencies and chain/job drill-down. |
| [frontend/js/pages/engine-status.js](../frontend/js/pages/engine-status.js) | Live ops dashboard for workers, profiles, queue health, and recent failures. |

### Dev entrypoints and scripts

| File | Responsibility |
|---|---|
| [run_server.py](../run_server.py) | Simple local uvicorn launcher for the server. |
| [run_worker.py](../run_worker.py) | Simple local launcher for `worker.main`. |
| [scripts/warm_profile.py](../scripts/warm_profile.py) | Opens a visible Chrome session and warms/logs in a named profile. |
| [scripts/check_profiles_ultra.py](../scripts/check_profiles_ultra.py) | Lints `profiles_ultra.txt` and reports profile readiness/health. |

## 7. API surface

Request/response details live in the model files and route modules; this section is the handler index only.

### App-level auth and health

| Method | Path | Brief | Handler |
|---|---|---|---|
| `GET` | `/login` | Serve the dashboard login page when auth is enabled. | [server/dashboard_auth.py:281](../server/dashboard_auth.py#L281) |
| `POST` | `/api/auth/login` | Verify password and mint the signed dashboard cookie. | [server/dashboard_auth.py:285](../server/dashboard_auth.py#L285) |
| `POST` | `/api/auth/logout` | Clear the dashboard session cookie. | [server/dashboard_auth.py:300](../server/dashboard_auth.py#L300) |
| `GET` | `/health` | Return health JSON used by ops/settings pages. | [server/app.py:159](../server/app.py#L159) |

### `server/routes/jobs.py`

| Method | Path | Brief | Handler |
|---|---|---|---|
| `POST` | `/api/jobs` | Queue one job. | [server/routes/jobs.py:55](../server/routes/jobs.py#L55) |
| `POST` | `/api/chains` | Create a chain and materialize its jobs. | [server/routes/jobs.py:87](../server/routes/jobs.py#L87) |
| `GET` | `/api/chains/{chain_id}` | Return aggregate chain status/progress plus job ids. | [server/routes/jobs.py:124](../server/routes/jobs.py#L124) |
| `GET` | `/api/jobs/counts` | Return queue counts grouped by status. | [server/routes/jobs.py:133](../server/routes/jobs.py#L133) |
| `POST` | `/api/jobs/recover` | Requeue stale claimed/running jobs. | [server/routes/jobs.py:139](../server/routes/jobs.py#L139) |
| `GET` | `/api/jobs` | List jobs with filters/pagination. | [server/routes/jobs.py:154](../server/routes/jobs.py#L154) |
| `GET` | `/api/jobs/{job_id}` | Fetch one job by id. | [server/routes/jobs.py:174](../server/routes/jobs.py#L174) |
| `GET` | `/api/jobs/{job_id}/children` | Fetch direct child jobs of one parent job. | [server/routes/jobs.py:183](../server/routes/jobs.py#L183) |
| `DELETE` | `/api/jobs/{job_id}` | Cancel/delete one job. | [server/routes/jobs.py:192](../server/routes/jobs.py#L192) |

### `server/routes/worker.py`

| Method | Path | Brief | Handler |
|---|---|---|---|
| `POST` | `/api/worker/claim` | Claim the next eligible job for a worker/profile set. | [server/routes/worker.py:44](../server/routes/worker.py#L44) |
| `PUT` | `/api/worker/jobs/{job_id}` | Report worker-side status/result updates for one job. | [server/routes/worker.py:59](../server/routes/worker.py#L59) |
| `POST` | `/api/worker/heartbeat` | Refresh worker liveness in the in-memory tracker. | [server/routes/worker.py:71](../server/routes/worker.py#L71) |
| `GET` | `/api/worker/workers` | List current workers from the in-memory tracker. | [server/routes/worker.py:78](../server/routes/worker.py#L78) |

### `server/routes/profiles.py`

| Method | Path | Brief | Handler |
|---|---|---|---|
| `GET` | `/api/profiles` | List registered profiles. | [server/routes/profiles.py:15](../server/routes/profiles.py#L15) |
| `POST` | `/api/profiles` | Register a new profile. | [server/routes/profiles.py:21](../server/routes/profiles.py#L21) |
| `PUT` | `/api/profiles/{name}` | Update mutable profile fields. | [server/routes/profiles.py:31](../server/routes/profiles.py#L31) |
| `GET` | `/api/profiles/{name}` | Fetch one profile by name. | [server/routes/profiles.py:41](../server/routes/profiles.py#L41) |
| `GET` | `/api/profiles/{name}/jobs` | List jobs associated with one profile. | [server/routes/profiles.py:50](../server/routes/profiles.py#L50) |

### `server/routes/characters.py`

| Method | Path | Brief | Handler |
|---|---|---|---|
| `POST` | `/api/characters` | Create a reusable character record. | [server/routes/characters.py:58](../server/routes/characters.py#L58) |
| `GET` | `/api/characters` | List all characters. | [server/routes/characters.py:73](../server/routes/characters.py#L73) |
| `GET` | `/api/characters/{character_id}` | Fetch one character. | [server/routes/characters.py:79](../server/routes/characters.py#L79) |
| `PUT` | `/api/characters/{character_id}` | Update one character. | [server/routes/characters.py:88](../server/routes/characters.py#L88) |
| `DELETE` | `/api/characters/{character_id}` | Delete one character. | [server/routes/characters.py:110](../server/routes/characters.py#L110) |

### `server/routes/templates.py`

| Method | Path | Brief | Handler |
|---|---|---|---|
| `POST` | `/api/templates` | Create a workflow template. | [server/routes/templates.py:27](../server/routes/templates.py#L27) |
| `GET` | `/api/templates` | List workflow templates. | [server/routes/templates.py:39](../server/routes/templates.py#L39) |
| `GET` | `/api/templates/{template_id}` | Fetch one workflow template. | [server/routes/templates.py:44](../server/routes/templates.py#L44) |
| `PUT` | `/api/templates/{template_id}` | Replace one workflow template. | [server/routes/templates.py:52](../server/routes/templates.py#L52) |
| `DELETE` | `/api/templates/{template_id}` | Delete one workflow template. | [server/routes/templates.py:70](../server/routes/templates.py#L70) |
| `POST` | `/api/templates/{template_id}/instantiate` | Materialize template vars into a concrete chain. | [server/routes/templates.py:78](../server/routes/templates.py#L78) |

### `server/routes/tts.py`

| Method | Path | Brief | Handler |
|---|---|---|---|
| `POST` | `/api/tts` | Synthesize one audio file from text. | [server/routes/tts.py:56](../server/routes/tts.py#L56) |

### Media routers

| Method | Path | Brief | Handler |
|---|---|---|---|
| `POST` | `/api/media/cut` | Cut one local video clip by start/end time. | [server/routes/media_cut.py:87](../server/routes/media_cut.py#L87) |
| `POST` | `/api/media/merge` | Merge multiple local video clips into one output. | [server/routes/media_merge.py:150](../server/routes/media_merge.py#L150) |
| `POST` | `/api/media/fetch-url` | Download remote media into local storage. | [server/routes/media_fetch.py:170](../server/routes/media_fetch.py#L170) |

### `server/routes/retarget.py`

| Method | Path | Brief | Handler |
|---|---|---|---|
| `POST` | `/api/retarget` | Extract a reference frame from a video and queue a retarget job. | [server/routes/retarget.py:82](../server/routes/retarget.py#L82) |

### `server/routes/llm.py`

| Method | Path | Brief | Handler |
|---|---|---|---|
| `POST` | `/api/llm/auto-prompt` | Generate a first-pass prompt from a topic/style pair. | [server/routes/llm.py:82](../server/routes/llm.py#L82) |
| `POST` | `/api/llm/expand-prompt` | Expand a short idea into a fuller prompt. | [server/routes/llm.py:95](../server/routes/llm.py#L95) |
| `POST` | `/api/llm/shot-list` | Generate a structured shot list for a scene. | [server/routes/llm.py:108](../server/routes/llm.py#L108) |

### `server/routes/prompt_builder.py`

| Method | Path | Brief | Handler |
|---|---|---|---|
| `POST` | `/api/prompt-builder/assemble` | Assemble a deterministic prompt string from structured fields. | [server/routes/prompt_builder.py:92](../server/routes/prompt_builder.py#L92) |

### `server/routes/product_pipeline.py`

| Method | Path | Brief | Handler |
|---|---|---|---|
| `POST` | `/api/product-pipeline/` | Queue the fixed product-ad workflow chain. | [server/routes/product_pipeline.py:59](../server/routes/product_pipeline.py#L59) |

### `server/routes/uploads.py`

| Method | Path | Brief | Handler |
|---|---|---|---|
| `POST` | `/api/uploads` | Validate and persist one uploaded image asset. | [server/routes/uploads.py:57](../server/routes/uploads.py#L57) |

### `server/routes/ws.py`

| Method | Path | Brief | Handler |
|---|---|---|---|
| `WS` | `/ws/jobs` | Stream `job_update` events and periodic keepalive `ping` events. | [server/routes/ws.py:52](../server/routes/ws.py#L52) |

## 8. UI map

Hash-route anchors live in [frontend/index.html:15-31](../frontend/index.html#L15), and page scripts are loaded in [frontend/index.html:113-132](../frontend/index.html#L113).

| File | Page | Hash route | Backend endpoints consumed |
|---|---|---|---|
| [frontend/js/pages/home.js](../frontend/js/pages/home.js) | Home | `#home` | `GET /api/jobs`, `GET /api/jobs/{id}`, `WS /ws/jobs` |
| [frontend/js/pages/dashboard.js](../frontend/js/pages/dashboard.js) | Dashboard | `#dashboard` | `GET /api/jobs/counts`, `GET /api/jobs?limit=20`, `POST /api/jobs/recover`, `GET /api/jobs/{id}`, `DELETE /api/jobs/{id}`, `WS /ws/jobs` |
| [frontend/js/pages/create-job.js](../frontend/js/pages/create-job.js) | Create Job | `#create` | `GET /api/profiles`, `POST /api/jobs`, `POST /api/uploads` |
| [frontend/js/pages/chain-builder.js](../frontend/js/pages/chain-builder.js) | Chain Builder | `#chains` | `GET /api/profiles`, `POST /api/chains` |
| [frontend/js/pages/profiles.js](../frontend/js/pages/profiles.js) | Profiles | `#profiles` | `GET /api/profiles`, `POST /api/profiles`, client also calls `POST /api/profiles/{name}/quarantine` and `POST /api/profiles/{name}/activate` |
| [frontend/js/pages/settings.js](../frontend/js/pages/settings.js) | Settings | `#settings` | `GET /health`, `GET /api/jobs/counts`, `POST /api/jobs/recover`, `GET /api/jobs`, `DELETE /api/jobs/{id}` |
| [frontend/js/pages/characters.js](../frontend/js/pages/characters.js) | Characters | `#characters` | `GET/POST /api/characters`, `GET/PUT/DELETE /api/characters/{id}`, `POST /api/uploads` |
| [frontend/js/pages/workflows.js](../frontend/js/pages/workflows.js) | Workflows | `#workflows` | `GET /api/templates`, `POST /api/templates/{id}/instantiate`, `POST /api/llm/auto-prompt`, `POST /api/llm/expand-prompt`, `POST /api/prompt-builder/assemble` |
| [frontend/js/pages/media-tools.js](../frontend/js/pages/media-tools.js) | Media Tools | `#media-tools` | `GET /api/profiles`, `POST /api/media/cut`, `POST /api/media/merge`, `POST /api/media/fetch-url`, `POST /api/retarget` |
| [frontend/js/pages/tts.js](../frontend/js/pages/tts.js) | Text to Speech | `#tts` | `POST /api/tts` |
| [frontend/js/pages/jobs.js](../frontend/js/pages/jobs.js) | Jobs | `#jobs` and `#jobs/{chain_id}` | `GET /api/jobs`, `GET /api/jobs/{id}`, `POST /api/jobs`, `DELETE /api/jobs/{id}`, `POST /api/jobs/recover`, `GET /api/profiles`, `WS /ws/jobs` |
| [frontend/js/pages/gallery.js](../frontend/js/pages/gallery.js) | Gallery | `#gallery` | `GET /api/jobs?status=completed`, `GET /api/profiles`, `GET /api/jobs/{id}`, `WS /ws/jobs` |
| [frontend/js/pages/batch-queue.js](../frontend/js/pages/batch-queue.js) | Batch Queue | `#batch-queue` | `GET /api/profiles`, `POST /api/jobs`, `WS /ws/jobs` |
| [frontend/js/pages/job-detail.js](../frontend/js/pages/job-detail.js) | Job Detail | `#job-detail/{job_id}` and alias `#job/{job_id}` | `GET /api/jobs/{id}`, `GET /api/jobs/{id}/children`, `POST /api/jobs`, `DELETE /api/jobs/{id}`, `WS /ws/jobs` |
| [frontend/js/pages/chain-tree.js](../frontend/js/pages/chain-tree.js) | Chain Tree | `#chain-tree` | `GET /api/jobs`, `GET /api/chains/{id}`, `GET /api/jobs?chain_id=...` |
| [frontend/js/pages/engine-status.js](../frontend/js/pages/engine-status.js) | Engine Status | `#engine-status` | `GET /health`, `GET /api/jobs/counts`, `GET /api/profiles`, `GET /api/jobs?status=pending`, `GET /api/jobs?status=failed`, `GET /api/jobs/{id}`, `WS /ws/jobs` |

Current frontend/backend drift to know before editing:

- `profiles.js` still calls quarantine/activate endpoints, but current `master` exposes only `GET/POST/PUT /api/profiles` plus `GET /api/profiles/{name}/jobs`; there is no matching backend route today. See [frontend/js/pages/profiles.js:117](../frontend/js/pages/profiles.js#L117) versus [server/routes/profiles.py:14](../server/routes/profiles.py#L14).
- `frontend/js/api.js` still defines `API.chains.list()` as `GET /api/chains`, but current backend exposes only `POST /api/chains` and `GET /api/chains/{chain_id}`. `chain-tree.js` works around this by using jobs plus per-chain fetches. See [frontend/js/api.js:118](../frontend/js/api.js#L118) versus [server/routes/jobs.py:86](../server/routes/jobs.py#L86).

## 9. Deferred / archived scope

| Scope | State | Reason | Source |
|---|---|---|---|
| `audio-to-video` | Archived; removed from FlowEngine | Deliberately removed in commit `cfead65` on 2026-04-28 and not re-added during the public cutover. Current `JobType` enum does not expose it. | [cutover report:117](session-reports/2026-05-01_web-ai-hassio-flowengine-cutover.md#L117), [server/models/job.py:46](../server/models/job.py#L46) |
| `remix-video` | Archived legacy scope only | Legacy engine had only a stub / `ImportError` fallback; there is no real implementation to port on current `master`. | [cutover report:118](session-reports/2026-05-01_web-ai-hassio-flowengine-cutover.md#L118) |
| `shorten-video` | Archived legacy scope only | Same as `remix-video`: stub-only in legacy engine, no real FlowEngine implementation. | [cutover report:119](session-reports/2026-05-01_web-ai-hassio-flowengine-cutover.md#L119) |
| Legacy `image-to-video` bundle | Replaced, do not resurrect as one merged op | Current engine matches the real Flow UI split: `frames-to-video` and `ingredients-to-video` are separate L1 operations and are the intended replacement. | [cutover report:73-74](session-reports/2026-05-01_web-ai-hassio-flowengine-cutover.md#L73), [server/models/job.py:46-50](../server/models/job.py#L46) |

## 10. How to add a new feature

1. Branch from `master` using `claude/<descriptive-slug>`; keep PR base explicit as `master`.
2. If the feature adds a new Flow operation, update `JobType` and validation in [server/models/job.py](../server/models/job.py), add `flow/operations/<name>.py`, wire it into `HANDLER_MAP` in [worker/dispatcher.py:430](../worker/dispatcher.py#L430), and add focused tests following the current flat `tests/test_<name>.py` pattern on `master` (there is no `tests/operations/` package today).
3. If the feature adds a new API route, create `server/routes/<name>.py`, register it in [server/app.py:117-131](../server/app.py#L117), add/update a model under `server/models/` if needed, and add tests.
4. If the feature adds a new UI page, follow the existing page-module IIFE pattern in `frontend/js/pages/<name>.js`, add the nav `<li>` in [frontend/index.html:67-81](../frontend/index.html#L67), add the `<script>` tag in [frontend/index.html:113-132](../frontend/index.html#L113), and add a route-anchor `<span id="...">` in [frontend/index.html:15-31](../frontend/index.html#L15).
5. If the feature touches job chains, re-read [docs/SPEC.md](SPEC.md) first and preserve `INV-1` through `INV-5`; do not trust stale frontend inputs for L2 context.
6. Run `python -m pytest -q` before push.
7. Use `gh pr create --base master` explicitly; do not rely on repo defaults.
8. For non-trivial PRs, get 2 independent Codex reviews before merge, per project convention.
9. If the change affects live Flow behavior, do a real browser/live verification pass before calling it done; this gate is captured in memory `feedback_live_verify_gates_done`.
10. Update this spine if the feature changed architecture, deploy topology, route surface, data model, code map, or UI map.

## 11. Glossary

| Term | Meaning |
|---|---|
| `L1` | A level-1 job that starts a new Flow project (`text-to-video`, `frames-to-video`, `ingredients-to-video`, or `text-to-image`). |
| `L2+` | Any descendant job that operates on an existing project/clip context inherited from an upstream job. |
| `Profile` | One Chrome profile directory, which in practice means one Google account identity. |
| `project_url` | Flow project grid URL, e.g. `/tools/flow/project/{project_id}`. |
| `media_id` | Flow clip/image UUID used to target one specific asset inside a project. |
| `edit_url` | The direct `/edit/{media_id}` URL for one target clip; this is the only supported targeting path. |
| `chain` | An ordered set of jobs connected by `parent_job_id` and grouped by `chain_id`. |
| `parent_job_id` | Direct upstream job id for one child job. |
| `chain_id` | Shared chain identifier across all jobs in the same chain. |
| `generation_id` | Flow generation identifier captured after completion and stored on the job row. |
| `bbox` | Normalized bounding box `{x,y,w,h}` in the 0-1 range, used by insert/remove operations. |
| `direction` | Camera preset label for `camera-move`, such as `Dolly in` or `Center`. |
| `composer chip` | The Flow UI chip/menu used to switch composer mode or sub-mode (`Video`, `Frames`, `Ingredients`, image mode, aspect chips, etc.). |
| `Veo Lite LP` | `veo-3.1-lite-lp`, the current default video model; `LP` means lower priority. |
| `Veo Fast LP` | `veo-3.1-fast-lp`, another lower-priority/free-tier Veo variant still used as a route sentinel on the text-to-image path. |
| `ProjectLock` | The worker-side in-memory mutex keyed by `project_url` that prevents parallel edits to one Flow project. |
| `ProfileSwapper` | Worker helper that archives a burned profile and replaces it with the next fresh credential. |
| `capture_failure` | Diagnostic bundle capture path used on browser failures; surfaced to users as `[cap=...]` when available. |
| `Flow landing` | The marketing/CTA page at `labs.google/fx/...` that sometimes appears instead of the real Flow app and must be recovered from. |

## 12. Pointers

- [CLAUDE.md](../CLAUDE.md) - session canon for Claude/Codex work in this repo.
- [docs/DESIGN.md](DESIGN.md) - full architecture, design decisions, and historical rationale.
- [docs/SPEC.md](SPEC.md) - invariants (`INV-N`), code rules, and test contract.
- [docs/WORKPLAN.md](WORKPLAN.md) - roadmap and active work plan.
- [docs/FLOW_MULTILEVEL_JOBS.md](FLOW_MULTILEVEL_JOBS.md) - chain behavior and multilevel Flow specifics.
- [docs/FLOW_PIPELINE_KNOWLEDGE.md](FLOW_PIPELINE_KNOWLEDGE.md) - pipeline/domain knowledge gathered from live probing.
- [docs/FLOW_UI_REFERENCE.md](FLOW_UI_REFERENCE.md) - selector and UI-structure evidence from Google Flow.
- [docs/CHROME_LAUNCH_SECURITY.md](CHROME_LAUNCH_SECURITY.md) - Chrome anti-detection and launch-security notes.
- [docs/session-reports/INDEX.md](session-reports/INDEX.md) - chronological session report index.
- Latest public-cutover report: [docs/session-reports/2026-05-01_web-ai-hassio-flowengine-cutover.md](session-reports/2026-05-01_web-ai-hassio-flowengine-cutover.md).
- Private user memory: `~/.claude/projects/D--AI-FlowEngine/memory/MEMORY.md` (not in repo).
