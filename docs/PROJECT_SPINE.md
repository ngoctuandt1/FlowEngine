# PROJECT_SPINE

> This is the canonical spine. Read first. Update whenever architecture, code map, or deploy topology changes.

- Scope: tracks current `master` plus the 2026-05-01 public cutover state.
- Purpose: one 5-minute sync doc for future feature work.
- Not here: deep rationale lives in [docs/DESIGN.md](DESIGN.md), invariants/test contract in [docs/SPEC.md](SPEC.md), and roadmap in [docs/WORKPLAN.md](WORKPLAN.md).

## 1. What is FlowEngine

FlowEngine is a browser-automation engine for Google Flow (`https://labs.google/fx/tools/flow`): a FastAPI server queues jobs, one or more workers claim them over HTTP, and Playwright-driven Chrome profiles execute Flow operations such as text-to-video, frames-to-video, ingredients-to-video, text-to-image, extend, insert, remove, and camera-move. It is multi-account by design, chain-aware by design, and currently exposed publicly at `https://ai.hassio.io.vn`.

## 2. Architecture

FlowEngine is four layers with a strict split: the frontend is a vanilla JS SPA, the server owns HTTP/API/SQLite plus static mounts, the worker owns claim/dispatch/profile state, and the `flow/` package owns Google Flow browser automation.

```text
frontend/ (index.html + js/pages/*)
    | HTTP GET/POST/DELETE to /api/*
    | WS subscribe only to /ws/jobs
    v
server/ (FastAPI + SQLite + static mounts + WS broadcaster)
    ^  POST /api/worker/claim
    |  PUT  /api/worker/jobs/{id}
    |  POST /api/worker/heartbeat
worker/ (claim loop + dispatcher + profile/project guards)
    | Playwright / FlowClient calls
    v
flow/ (login + navigation + submit + wait + download + operations)
    | Chrome profile + Google Flow DOM/network
    v
labs.google/fx/tools/flow
```

WS reality that matters before changing live-update code:

- `WS /ws/jobs` is server-push only. The worker never talks WS directly; it reports over `/api/worker/*`, then the server broadcasts to dashboards.
- Current `server/routes/ws.py` emits only `{event: "job_update", data: ...}` and keepalive `{event: "ping", ts: ...}` frames.
- Client messages are only absorbed to keep the socket open; worker heartbeat is not rebroadcast over WS.

Component summary:

- `server/`: FastAPI app, route registration, dashboard auth gate, SQLite schema/init, REST surface, static mounts, and WS fan-out.
- `worker/`: polling worker that claims the next eligible job, acquires profile/project guards, dispatches by job type, and reports completion/failure.
- `flow/`: browser automation layer around Google Flow, including login recovery, URL/media-id helpers, stable selectors, wait logic, downloads, and operation modules.
- `frontend/`: static SPA served by the FastAPI app; route state is hash-based and pages consume `frontend/js/api.js`, `frontend/js/ws.js`, and some direct raw-socket listeners.

## 3. Job system invariants

These are the chain rules that future work must preserve. Do not restate or fork them elsewhere; the source of truth is [docs/SPEC.md](SPEC.md), especially [INV-1 at `docs/SPEC.md:52`](SPEC.md#L52), [INV-2 at `docs/SPEC.md:63`](SPEC.md#L63), [INV-3 at `docs/SPEC.md:73`](SPEC.md#L73), [INV-4 at `docs/SPEC.md:83`](SPEC.md#L83), and [INV-5 at `docs/SPEC.md:90`](SPEC.md#L90).

- `INV-1` + `INV-3`: L1 creates the project; every L2+ child inherits the completed parent's `project_url`, `media_id`, `edit_url`, and `profile` at claim time. Main enforcement: [server/db/job_store.py](../server/db/job_store.py), [server/models/job.py](../server/models/job.py), [docs/SPEC.md:73](SPEC.md#L73).
- `INV-1`: the same `profile` must hold across the entire chain. Different Google account means different project ownership and produces Flow 404 / redirect failures. Main enforcement: [server/db/job_store.py](../server/db/job_store.py), [docs/SPEC.md:52](SPEC.md#L52).
- `INV-2`: target a clip only via `edit_url` (`/edit/{media_id}`); never via `video_index`, generic grid-card order, or DOM card counting. Main enforcement: [flow/navigation.py](../flow/navigation.py), [flow/operations/_base.py](../flow/operations/_base.py), [docs/SPEC.md:63](SPEC.md#L63).
- `INV-4`: execution is serial per `project_url`. There are two guards: claim-time SQL refuses a second active job on the same project, and the worker still acquires `ProjectLock` before L2+ work. Main enforcement: [server/db/job_store.py](../server/db/job_store.py), [worker/project_lock.py](../worker/project_lock.py), [docs/SPEC.md:83](SPEC.md#L83).
- `INV-5`: `media_id` is re-extracted after every operation and stored back; downstream work must consume the final stored value, not assume stability across ops. Main enforcement: [flow/operations/_base.py](../flow/operations/_base.py), [docs/SPEC.md:90](SPEC.md#L90).

Read before changing chain logic:

- [docs/SPEC.md](SPEC.md)
- [docs/FLOW_MULTILEVEL_JOBS.md](FLOW_MULTILEVEL_JOBS.md) (historical / pre-INV-2 for targeting)
- [docs/FLOW_PIPELINE_KNOWLEDGE.md](FLOW_PIPELINE_KNOWLEDGE.md) (historical / pre-INV-2 for `video_index` targeting notes)

### Job create matrix

This matrix reflects `server/models/job.py::JobCreate` validation plus the minimum chain-target context expected by runtime code. Image-input path fields (`start_image_path`, `end_image_path`, `ingredient_image_paths`, `ref_image_path`) are usually `uploads/...` paths rooted at `FLOW_UPLOAD_DIR`.

| Job type | Level | API-required inputs | Target context / notes |
|---|---|---|---|
| `text-to-video` | L1 | none beyond `type` | Commonly carries `prompt`, `model`, `aspect_ratio`, and optional `profile` pin. |
| `frames-to-video` | L1 | `start_image_path` | `start_image_path` is usually an `uploads/...` path under `FLOW_UPLOAD_DIR`; `end_image_path` is optional and follows the same rule. |
| `ingredients-to-video` | L1 | `ingredient_image_paths` with at least 1 item | `ingredient_image_paths` are usually `uploads/...` paths under `FLOW_UPLOAD_DIR`; usually also carries `prompt`. |
| `text-to-image` | L1 | none beyond `type` | Route keeps a text-to-image sentinel default and maps it to the image model path; optional `ref_image_path` is usually an `uploads/...` path under `FLOW_UPLOAD_DIR`. |
| `extend-video` | L2+ | none at model layer | Needs a completed parent or explicit target context (`project_url` plus resolved/inherited `media_id`). |
| `insert-object` | L2+ | `bbox` | Usually also carries `prompt`; must target an existing clip via parent or explicit target context. |
| `remove-object` | L2+ | `bbox` | BBox-only op; must target an existing clip via parent or explicit target context. |
| `camera-move` | L2+ | `direction` from `CAMERA_PRESETS` | Must target an existing clip via parent or explicit target context. |

## 4. Local quickstart

### Minimal boot sequence

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Edit `.env` or export the same vars in your shell, then start the server:

```powershell
$env:FLOW_DOWNLOAD_DIR = "D:\AI\FlowEngine\downloads"
$env:FLOW_UPLOAD_DIR = "D:\AI\FlowEngine\uploads"
python run_server.py
```

Verify the app is up at `http://localhost:8080/` or `http://localhost:8080/health`, then start the worker in a second shell:

```powershell
.\.venv\Scripts\Activate.ps1
$env:SERVER_URL = "http://localhost:8080"
$env:API_KEY = "dev-key"
$env:WORKER_PROFILES = "ngoctuandt20"
$env:FLOW_DOWNLOAD_DIR = "D:\AI\FlowEngine\downloads"
$env:FLOW_UPLOAD_DIR = "D:\AI\FlowEngine\uploads"
$env:CHROME_USER_DATA_DIR = "D:\AI\FlowEngine\chrome-profiles"
$env:FLOW_USE_BASE_PROFILE = "1"
python run_worker.py
```

### Worker prerequisites

- `CHROME_USER_DATA_DIR` should be an absolute path to the real Chrome user-data root, not a fresh worktree-local directory.
- For local/same-host runs, set the same absolute `FLOW_UPLOAD_DIR` and `FLOW_DOWNLOAD_DIR` in both the server and worker shells. On split-host deploys, both processes still need those vars pointed at the same shared or synced media roots from their own OS view, or `uploads/...` inputs and dashboard media links will break.
- `FLOW_USE_BASE_PROFILE=1` reuses the warmed profile directory verbatim and avoids temp-clone auth drift.
- Warm at least one profile before starting the worker: `python scripts/warm_profile.py <profile>`.
- Warming requires a matching credential entry in `FLOW_PROFILE_LIST_FILE` (default `profiles_ultra.txt`); use `python scripts/check_profiles_ultra.py` to verify the file/profile inventory.
- If Chrome is not installed in a default location, set `CHROME_PATH` for worker runs and `FLOW_WARM_CHROME_PATH` (or `CHROME_PATH`) for `scripts/warm_profile.py`.
- `WORKER_PROFILES` must list warmed subdirectory names under `CHROME_USER_DATA_DIR`; the worker preflight will exit if the profile dir is missing or has no cookies.

Example warm-up:

```powershell
$env:CHROME_USER_DATA_DIR = "D:\AI\FlowEngine\chrome-profiles"
python scripts/warm_profile.py ngoctuandt20
```

### Core env vars

| Component | Var | Why it matters | Typical local value |
|---|---|---|---|
| server | `SERVER_PORT` | `run_server.py` binds here. | `8080` |
| server | `API_KEY` | Bearer token for `/api/worker/*`. | `dev-key` |
| server | `DATABASE_PATH` | SQLite file path. | `./data/flowengine.db` |
| shared | `FLOW_DOWNLOAD_DIR` | Worker writes outputs here and the server mounts it at `/downloads`; same-host values should match exactly, and split-host values must point at the same shared media root. | `D:\AI\FlowEngine\downloads` |
| shared | `FLOW_UPLOAD_DIR` | Uploaded image inputs are resolved from here and the server mounts it at `/uploads`; same-host values should match exactly, and split-host values must point at the same shared media root. | `D:\AI\FlowEngine\uploads` |
| worker | `SERVER_URL` | Base URL for claim/update/heartbeat requests. | `http://localhost:8080` |
| worker | `WORKER_PROFILES` | Comma-separated warmed profile names. | `ngoctuandt20` |
| worker | `CHROME_USER_DATA_DIR` | Absolute Chrome user-data root. | `D:\AI\FlowEngine\chrome-profiles` |
| worker | `FLOW_USE_BASE_PROFILE` | Reuse the warmed profile dir verbatim; avoids temp-clone auth drift. | `1` |
| worker / warm tooling | `FLOW_PROFILE_LIST_FILE` | Credential source for warming and profile replacement; defaults to `profiles_ultra.txt` when unset. | `D:\AI\FlowEngine\profiles_ultra.txt` |
| worker | `MAX_CONCURRENT_JOBS` | Caps concurrent claims per worker process. | `1` |
| worker | `POLL_INTERVAL_SEC` | Claim-loop poll interval. | `5` |

## 5. Production deploy topology

This section is the current runtime topology, not just the repo template. The repo tracks the server service template; the public cutover details come from the 2026-05-01 session report plus operator state.

| Item | Current state | Source |
|---|---|---|
| Public URL | `https://ai.hassio.io.vn` | [session report:1-3](session-reports/2026-05-01_web-ai-hassio-flowengine-cutover.md#L1) |
| Dashboard password | `1` | Operator-stated current runtime |
| Edge routing | Cloudflare Tunnel, token-mode, dashboard-managed | Operator-stated current runtime; cutover kept the existing tunnel route on port `8899` per [session report:10-12](session-reports/2026-05-01_web-ai-hassio-flowengine-cutover.md#L10) |
| Tunnel target | `192.168.86.42:8899` | Operator-stated current runtime |
| Server bind used for public cutover | FlowEngine bound on `0.0.0.0:8899` to avoid moving the tunnel route | [session report:53-56](session-reports/2026-05-01_web-ai-hassio-flowengine-cutover.md#L53), [CLAUDE.md](../CLAUDE.md) |
| systemd units | `flowengine-server`, `flowengine-worker`, `flowengine-xvfb` on Debian | Operator-stated current runtime; repo only ships the server unit template at [deploy/debian/flowengine-server.service](../deploy/debian/flowengine-server.service) |
| Install path | `/opt/flowengine` | [deploy/debian/flowengine-server.service](../deploy/debian/flowengine-server.service), [deploy/debian/README.md](../deploy/debian/README.md) |
| Env file | `/etc/flowengine/flowengine.env` | [deploy/debian/flowengine-server.service](../deploy/debian/flowengine-server.service), [deploy/debian/README.md](../deploy/debian/README.md) |
| Database path | `DATABASE_PATH=/var/lib/flowengine/flowengine.db` | [deploy/debian/flowengine.env.example](../deploy/debian/flowengine.env.example) |
| Media roots | `FLOW_DOWNLOAD_DIR=/var/lib/flowengine/downloads`, `FLOW_UPLOAD_DIR=/var/lib/flowengine/uploads` | [deploy/debian/flowengine.env.example](../deploy/debian/flowengine.env.example), [deploy/debian/README.md](../deploy/debian/README.md) |
| Log root | `LOG_DIR=/var/log/flowengine` | [deploy/debian/flowengine.env.example](../deploy/debian/flowengine.env.example) |
| Dashboard auth switch | `DASHBOARD_PASSWORD` enables signed-cookie auth and middleware | [server/dashboard_auth.py](../server/dashboard_auth.py), [server/app.py](../server/app.py) |
| Proxy handling | `TRUST_PROXY_HEADERS=1` plus uvicorn proxy-header support are required so auth sees HTTPS correctly | [server/dashboard_auth.py](../server/dashboard_auth.py), [deploy/debian/flowengine-server.service](../deploy/debian/flowengine-server.service), [CLAUDE.md](../CLAUDE.md) |
| Downloads/uploads sharing | The server mounts `FLOW_DOWNLOAD_DIR` at `/downloads` and `FLOW_UPLOAD_DIR` at `/uploads`. In split-host deploys, the worker must resolve the same shared media roots (or equivalent synced storage) or dashboard media links and worker input-asset paths will break. | [server/app.py](../server/app.py), [server/routes/uploads.py](../server/routes/uploads.py), [deploy/debian/README.md](../deploy/debian/README.md) |
| Archived old engine | `/opt/_archive/video-ai-studio.20260501` (and `/opt/_archive/video-ai.20260501`) | [session report:61-65](session-reports/2026-05-01_web-ai-hassio-flowengine-cutover.md#L61) |

Important distinction:

- Repo template: [deploy/debian/flowengine-server.service](../deploy/debian/flowengine-server.service) still shows `127.0.0.1:8080` behind nginx/Caddy.
- Public runtime cutover: moved FlowEngine onto `0.0.0.0:8899` to preserve the existing Cloudflare Tunnel route for `ai.hassio.io.vn`.

## 6. Data model

Top fields only. For any schema change, read both the Pydantic model and the backing store/DDL before editing API, worker, or UI logic.

### Job models

| Model | Fields / notes |
|---|---|
| `BBox` | Normalized `x`, `y`, `w`, `h` floats in the 0-1 range. |
| `JobCreate` | Request body: `type`, optional `prompt`, `model`, `aspect_ratio`, `profile`, `parent_job_id`, `chain_id`, `project_url`, `media_id`, `bbox`, `direction`, `start_image_path`, `end_image_path`, `ingredient_image_paths`, `ref_image_path`. Image-input path fields are usually `uploads/...` paths rooted at `FLOW_UPLOAD_DIR`. |
| `Job` | Full record: `id`, `type`, `status`, `job_level`, `parent_job_id`, `chain_id`, `profile`, `project_url`, `media_id`, `edit_url`, all create-time operation fields, `output_files`, `generation_id`, `worker_id`, `claimed_at`, `completed_at`, `error`, `created_at`, `updated_at`. |
| `JobUpdate` | Worker update payload only: `status`, `project_url`, `media_id`, `edit_url`, `profile`, `output_files`, `generation_id`, `error`, `completed_at`. |

DB notes:

- `jobs` stores the same shape in SQLite-friendly form: `id/type/status TEXT`, `job_level INTEGER`, `parent_job_id/chain_id/profile/project_url/media_id/edit_url TEXT`, `bbox_json/ingredient_image_paths_json/output_files_json TEXT`, `generation_id/worker_id/error TEXT`, and timestamp text columns.
- Current `jobs` DDL still includes an unused `safety_filter TEXT` column in fresh databases, but the live job API does not wire or persist that field. `TemplateStep` still accepts the legacy enum. See [docs/SAFETY_FILTER_NOTE.md](SAFETY_FILTER_NOTE.md).

### Chain models

| Model | Fields / notes |
|---|---|
| `ChainCreate` | Request body: `jobs: list[JobCreate]`, optional `profile`. Current `POST /api/chains` is linear-chain only: each step becomes a child of the previous step, explicit per-step `parent_job_id` values are overwritten, and chain-level `profile` wins over any per-step `JobCreate.profile`. |
| `Chain` | Immutable metadata row: `id`, optional `profile`, `created_at`, `updated_at`. |
| `ChainProgress` | Aggregate counts: `completed`, `total`. |
| `ChainAggregate` | `GET /api/chains/{id}` response: `id`, optional `profile`, `created_at`, derived `status`, derived `progress`, and ordered `jobs: list[str]`. |
| `ChainCreateResponse` | `POST /api/chains` and template instantiate response: `chain_id`, `jobs: list[Job]`. |

DB notes:

- The `chains` table still has legacy `project_url`, `media_id`, and `status` columns, but current store logic treats the row as immutable metadata and computes status/progress from `jobs` on read.

### Profile models

| Model | Fields / notes |
|---|---|
| `Profile` | `name`, optional `google_account`, `locale`, `tier`, `status`, optional `current_job_id`, optional `worker_id`, optional `last_used_at`, `created_at`. |
| `ProfileUpdate` | Mutable fields only: optional `status`, `current_job_id`, `worker_id`, `google_account`, `locale`, `tier`. |

### Character models

| Model | Fields / notes |
|---|---|
| `CharacterCreate` | `name`, optional `description`, `image_paths`. |
| `CharacterUpdate` | Optional `name`, optional `description`, optional `image_paths`. |
| `Character` | Full record: `id`, `name`, optional `description`, `image_paths`, `created_at`, `updated_at`. |

### Template models

| Model | Fields / notes |
|---|---|
| `TemplateStep` | Placeholder-friendly step fields: `type`, `prompt`, `model`, `aspect_ratio`, `parent_job_id`, `bbox`, `direction`, `start_image_path`, `end_image_path`, `ref_image_path`, `ingredient_image_paths`, optional `safety_filter` legacy enum. Explicit `parent_job_id` values are accepted in stored templates but ignored at instantiate time because steps are rewritten into the same linear-chain shape. |
| `TemplateCreate` | Request body for create/update: `name`, optional `description`, `steps`. |
| `Template` | Stored template: `id`, `name`, optional `description`, `steps`, `created_at`, `updated_at`. |
| `TemplateInstantiate` | Instantiate request: `template_id`, `vars`. |

Template note:

- `TemplateStep.safety_filter` is a live model remnant, not a live job-API contract. Current runtime guidance is still "do not wire or persist" a 3-level safety filter through the live job API. See [docs/SAFETY_FILTER_NOTE.md](SAFETY_FILTER_NOTE.md).

## 7. Code map

Every file below was read directly when this spine was written.

### Server entrypoints and auth

| File | Responsibility |
|---|---|
| [server/app.py](../server/app.py) | Builds the FastAPI app, initializes DB, mounts static frontend assets plus `/downloads` and `/uploads`, wires dashboard auth, and registers routers. |
| [server/dashboard_auth.py](../server/dashboard_auth.py) | Implements the optional signed-cookie dashboard password gate plus `/login`, `/api/auth/login`, and `/api/auth/logout`. |
| [server/auth.py](../server/auth.py) | Enforces bearer-token auth for privileged `/api/worker/*` endpoints. |
| [server/config.py](../server/config.py) | Loads env/config defaults, data paths, database path, and logging setup. |

### Server routes

| File | Responsibility |
|---|---|
| [server/routes/jobs.py](../server/routes/jobs.py) | Public job and chain CRUD surface plus queue counts/recovery and WS broadcast hooks. |
| [server/routes/worker.py](../server/routes/worker.py) | Worker claim/update/heartbeat endpoints behind bearer auth. |
| [server/routes/profiles.py](../server/routes/profiles.py) | Profile registration, update, fetch, quarantine/activate status changes, list, and per-profile job listing. |
| [server/routes/characters.py](../server/routes/characters.py) | Character-library CRUD with upload-path normalization and existence checks. |
| [server/routes/templates.py](../server/routes/templates.py) | Workflow-template CRUD plus instantiate-to-chain bridge. |
| [server/routes/tts.py](../server/routes/tts.py) | Edge-TTS synthesis endpoint that writes audio assets under `downloads/tts`. |
| [server/routes/media_cut.py](../server/routes/media_cut.py) | ffmpeg-backed video trim endpoint. |
| [server/routes/media_merge.py](../server/routes/media_merge.py) | ffmpeg-backed multi-source merge endpoint. |
| [server/routes/media_fetch.py](../server/routes/media_fetch.py) | Remote media downloader with validation/SSRF guardrails around `yt-dlp`. |
| [server/routes/retarget.py](../server/routes/retarget.py) | Extracts a representative frame from a source video and queues a `frames-to-video` retarget job. |
| [server/routes/llm.py](../server/routes/llm.py) | LLM-backed prompt helper endpoints for auto-prompt, expansion, and shot lists. |
| [server/routes/prompt_builder.py](../server/routes/prompt_builder.py) | Deterministic prompt-assembly endpoint from structured prompt parts. |
| [server/routes/product_pipeline.py](../server/routes/product_pipeline.py) | Converts a product image plus brief into a fixed multi-step chain request. |
| [server/routes/uploads.py](../server/routes/uploads.py) | Validates image uploads by magic bytes and stores them under `FLOW_UPLOAD_DIR`. |
| [server/routes/ws.py](../server/routes/ws.py) | `/ws/jobs` broadcaster; pushes `job_update` and `ping` only. |

### Server model and DB layer

| File | Responsibility |
|---|---|
| [server/models/job.py](../server/models/job.py) | Defines job enums, validators, `JobCreate` / `Job` / `JobUpdate`, `ChainCreate`, and camera preset constants mirrored by the frontend. |
| [server/models/chain.py](../server/models/chain.py) | Defines chain metadata/aggregate/response models: `Chain`, `ChainProgress`, `ChainAggregate`, and `ChainCreateResponse`. |
| [server/models/profile.py](../server/models/profile.py) | Defines profile record and profile-update models. |
| [server/models/character.py](../server/models/character.py) | Defines character create/update/record models. |
| [server/models/template.py](../server/models/template.py) | Defines template step/create/store/instantiate models. |
| [server/db/database.py](../server/db/database.py) | Creates SQLite tables/columns on startup and exposes the shared async DB context manager. |
| [server/db/chain_store.py](../server/db/chain_store.py) | Persists immutable chain rows and derives chain status/progress from jobs on read. |
| [server/db/job_store.py](../server/db/job_store.py) | Owns job CRUD, stale-job recovery, claim ordering, child inheritance, and terminal-state release behavior. |
| [server/db/profile_store.py](../server/db/profile_store.py) | Owns profile CRUD and worker-scoped profile selection. |
| [server/db/character_store.py](../server/db/character_store.py) | Owns character CRUD and JSON serialization for `image_paths`. |
| [server/db/template_store.py](../server/db/template_store.py) | Owns template CRUD, placeholder validation/substitution, and template instantiation. |

### Worker

| File | Responsibility |
|---|---|
| [worker/main.py](../worker/main.py) | Worker process entrypoint: preflight profile checks, claim loop, heartbeat, concurrency bookkeeping, and shutdown flow. |
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
| [frontend/js/app.js](../frontend/js/app.js) | Base hash router, page registry, modal/toast helpers, shared route loading, and generic job-type icon helpers. |
| [frontend/js/api.js](../frontend/js/api.js) | Browser-side REST client wrappers for jobs, chains, profiles, and uploads. |
| [frontend/js/ws.js](../frontend/js/ws.js) | Reconnect helper around the raw WebSocket; parses `{event,data}` frames, emits named callbacks such as `WS.on('job_update', handler)`, and also exposes a generic `message` event. |
| [frontend/js/constants.js](../frontend/js/constants.js) | Mirrors job types, models, aspect ratios, and camera presets from backend/Flow code. |

### Known frontend/API drift

- `jobs.js`, `gallery.js`, `engine-status.js`, `job-detail.js`, and `batch-queue.js` still bypass `frontend/js/ws.js` and attach raw `message` listeners that parse both `event||type` and `data||payload` for compatibility. `home.js` is already on `WS.on('job_update', ...)`.
- `dashboard.js` and `home.js` still pass `GET /api/jobs?limit=...`, but current `server/routes/jobs.py` accepts only `status`, `type`, `profile`, and `chain_id`; `limit` is ignored.
- `frontend/js/api.js::API.chains.list()` still points at `GET /api/chains`, but current backend exposes only `POST /api/chains` and `GET /api/chains/{chain_id}`. `chain-tree.js` works around this with job lists plus per-chain fetches.

### Frontend pages

| File | Responsibility |
|---|---|
| [frontend/js/pages/home.js](../frontend/js/pages/home.js) | Flow-style recent-output landing page with recent jobs and live-refresh intent. |
| [frontend/js/pages/dashboard.js](../frontend/js/pages/dashboard.js) | High-level counts and recent jobs dashboard with recover/delete actions. |
| [frontend/js/pages/create-job.js](../frontend/js/pages/create-job.js) | Single-job creator plus prompt-batch creator for supported L1 types. |
| [frontend/js/pages/chain-builder.js](../frontend/js/pages/chain-builder.js) | Visual builder for ordered linear chain steps posted to `/api/chains`. |
| [frontend/js/pages/profiles.js](../frontend/js/pages/profiles.js) | Profile list/add UI with quarantine and activate status actions. |
| [frontend/js/pages/settings.js](../frontend/js/pages/settings.js) | Health/config snapshot and admin recovery/job-control page. |
| [frontend/js/pages/characters.js](../frontend/js/pages/characters.js) | Character library CRUD/editor with upload support. |
| [frontend/js/pages/workflows.js](../frontend/js/pages/workflows.js) | Template runner plus LLM prompt-helper UI. |
| [frontend/js/pages/media-tools.js](../frontend/js/pages/media-tools.js) | UI for media cut, merge, fetch-url, and retarget endpoints. |
| [frontend/js/pages/tts.js](../frontend/js/pages/tts.js) | Text-to-speech UI. |
| [frontend/js/pages/jobs.js](../frontend/js/pages/jobs.js) | Full job history with filters, live refresh, retry, and delete flows. |
| [frontend/js/pages/gallery.js](../frontend/js/pages/gallery.js) | Completed-media browser with filters and preview modal. |
| [frontend/js/pages/batch-queue.js](../frontend/js/pages/batch-queue.js) | Bulk queue UI for many prompt-driven L1 jobs with live local/server state. |
| [frontend/js/pages/job-detail.js](../frontend/js/pages/job-detail.js) | Single-job detail view with parent/children context, router patching for dynamic job hashes, and retry/delete actions. |
| [frontend/js/pages/chain-tree.js](../frontend/js/pages/chain-tree.js) | Top-down visualization of chain dependencies and chain/job drill-down. |
| [frontend/js/pages/engine-status.js](../frontend/js/pages/engine-status.js) | Live ops dashboard for workers, profiles, queue health, and recent failures. |

### Dev entrypoints and scripts

| File | Responsibility |
|---|---|
| [run_server.py](../run_server.py) | Simple local uvicorn launcher for the server. |
| [run_worker.py](../run_worker.py) | Simple local launcher for `worker.main`. |
| [scripts/warm_profile.py](../scripts/warm_profile.py) | Opens a visible Chrome session and warms/logs in a named profile. |
| [scripts/check_profiles_ultra.py](../scripts/check_profiles_ultra.py) | Lints `profiles_ultra.txt` and reports profile readiness/health. |

## 8. API surface

Request/response details live in the model files and route modules; this section is the handler index plus the route-family dependencies that are easy to forget.

### Route-family dependency notes

| Route family | Extra dependency / env | Behavior when unavailable |
|---|---|---|
| `/api/llm/*` | `httpx`, `LLM_DISABLED`, `LLM_BASE_URL`, `LLM_API_KEY` or `NINEROUTER_API_KEY`, optional `LLM_MODEL` | Returns `503` when disabled or client library is missing; `502` on upstream failures / invalid responses. |
| `/api/tts` | `edge-tts`, `FLOW_DOWNLOAD_DIR` | Returns `503` if `edge-tts` is not installed; writes output under `downloads/tts`. |
| `/api/media/cut` | `ffmpeg`, `FLOW_DOWNLOAD_DIR`, `FLOW_UPLOAD_DIR` | Returns `500` if `ffmpeg` is missing and `504` on timeout. |
| `/api/media/merge` | `ffmpeg`, optional `ffprobe`, `FLOW_DOWNLOAD_DIR`, `FLOW_UPLOAD_DIR` | Returns `500` if `ffmpeg` is missing; skips duration enforcement when `ffprobe` is absent. |
| `/api/media/fetch-url` | `yt-dlp`, outbound network access, `FLOW_DOWNLOAD_DIR` | Returns `502` on fetch/download failure; rejects loopback/private/internal targets. |
| `/api/retarget` | `ffmpeg`, `FLOW_DOWNLOAD_DIR`, `FLOW_UPLOAD_DIR` | Returns `500` if frame extraction fails, then queues a `frames-to-video` job. |
| `/api/uploads` | `Pillow`, `FLOW_UPLOAD_DIR` | Rejects unsupported types with `415` and files over 10 MB with `413`. |

### App-level auth and health

| Method | Path | Brief | Handler |
|---|---|---|---|
| `GET` | `/login` | Serve the dashboard login page when auth is enabled. | [server/dashboard_auth.py](../server/dashboard_auth.py) |
| `POST` | `/api/auth/login` | Verify password and mint the signed dashboard cookie. | [server/dashboard_auth.py](../server/dashboard_auth.py) |
| `POST` | `/api/auth/logout` | Clear the dashboard session cookie. | [server/dashboard_auth.py](../server/dashboard_auth.py) |
| `GET` | `/health` | Return health JSON used by ops/settings pages. | [server/app.py](../server/app.py) |

### `server/routes/jobs.py`

| Method | Path | Brief | Handler |
|---|---|---|---|
| `POST` | `/api/jobs` | Queue one job. | [server/routes/jobs.py](../server/routes/jobs.py) |
| `POST` | `/api/chains` | Create a chain and materialize its jobs. | [server/routes/jobs.py](../server/routes/jobs.py) |
| `GET` | `/api/chains/{chain_id}` | Return aggregate chain status/progress plus job ids. | [server/routes/jobs.py](../server/routes/jobs.py) |
| `GET` | `/api/jobs/counts` | Return queue counts grouped by status. | [server/routes/jobs.py](../server/routes/jobs.py) |
| `POST` | `/api/jobs/recover` | Requeue stale claimed/running jobs. | [server/routes/jobs.py](../server/routes/jobs.py) |
| `GET` | `/api/jobs` | List jobs with optional `status`, `type`, `profile`, and `chain_id` filters. | [server/routes/jobs.py](../server/routes/jobs.py) |
| `GET` | `/api/jobs/{job_id}` | Fetch one job by id. | [server/routes/jobs.py](../server/routes/jobs.py) |
| `GET` | `/api/jobs/{job_id}/children` | Fetch direct child jobs of one parent job. | [server/routes/jobs.py](../server/routes/jobs.py) |
| `DELETE` | `/api/jobs/{job_id}` | Cancel/delete one job. | [server/routes/jobs.py](../server/routes/jobs.py) |

### `server/routes/worker.py`

| Method | Path | Brief | Handler |
|---|---|---|---|
| `POST` | `/api/worker/claim` | Claim the next eligible job for a worker/profile set. | [server/routes/worker.py](../server/routes/worker.py) |
| `PUT` | `/api/worker/jobs/{job_id}` | Report worker-side status/result updates for one job. | [server/routes/worker.py](../server/routes/worker.py) |
| `POST` | `/api/worker/heartbeat` | Refresh worker liveness in the in-memory tracker. | [server/routes/worker.py](../server/routes/worker.py) |
| `GET` | `/api/worker/workers` | List current workers from the in-memory tracker. | [server/routes/worker.py](../server/routes/worker.py) |

### `server/routes/profiles.py`

| Method | Path | Brief | Handler |
|---|---|---|---|
| `GET` | `/api/profiles` | List registered profiles. | [server/routes/profiles.py](../server/routes/profiles.py) |
| `POST` | `/api/profiles` | Register a new profile. | [server/routes/profiles.py](../server/routes/profiles.py) |
| `PUT` | `/api/profiles/{name}` | Update mutable profile fields. | [server/routes/profiles.py](../server/routes/profiles.py) |
| `GET` | `/api/profiles/{name}` | Fetch one profile by name. | [server/routes/profiles.py](../server/routes/profiles.py) |
| `GET` | `/api/profiles/{name}/jobs` | List jobs associated with one profile. | [server/routes/profiles.py](../server/routes/profiles.py) |
| `POST` | `/api/profiles/{name}/quarantine` | Set `profile.status` to `quarantined`. | [server/routes/profiles.py](../server/routes/profiles.py) |
| `POST` | `/api/profiles/{name}/activate` | Set `profile.status` to `available`. | [server/routes/profiles.py](../server/routes/profiles.py) |

### `server/routes/characters.py`

| Method | Path | Brief | Handler |
|---|---|---|---|
| `POST` | `/api/characters` | Create a reusable character record. | [server/routes/characters.py](../server/routes/characters.py) |
| `GET` | `/api/characters` | List all characters. | [server/routes/characters.py](../server/routes/characters.py) |
| `GET` | `/api/characters/{character_id}` | Fetch one character. | [server/routes/characters.py](../server/routes/characters.py) |
| `PUT` | `/api/characters/{character_id}` | Update one character. | [server/routes/characters.py](../server/routes/characters.py) |
| `DELETE` | `/api/characters/{character_id}` | Delete one character. | [server/routes/characters.py](../server/routes/characters.py) |

### `server/routes/templates.py`

| Method | Path | Brief | Handler |
|---|---|---|---|
| `POST` | `/api/templates` | Create a workflow template. | [server/routes/templates.py](../server/routes/templates.py) |
| `GET` | `/api/templates` | List workflow templates. | [server/routes/templates.py](../server/routes/templates.py) |
| `GET` | `/api/templates/{template_id}` | Fetch one workflow template. | [server/routes/templates.py](../server/routes/templates.py) |
| `PUT` | `/api/templates/{template_id}` | Replace one workflow template. | [server/routes/templates.py](../server/routes/templates.py) |
| `DELETE` | `/api/templates/{template_id}` | Delete one workflow template. | [server/routes/templates.py](../server/routes/templates.py) |
| `POST` | `/api/templates/{template_id}/instantiate` | Materialize template vars into a concrete chain. | [server/routes/templates.py](../server/routes/templates.py) |

### `server/routes/tts.py`

| Method | Path | Brief | Handler |
|---|---|---|---|
| `POST` | `/api/tts` | Synthesize one audio file from text. | [server/routes/tts.py](../server/routes/tts.py) |

### Media routers

| Method | Path | Brief | Handler |
|---|---|---|---|
| `POST` | `/api/media/cut` | Cut one local video clip by start/end time. | [server/routes/media_cut.py](../server/routes/media_cut.py) |
| `POST` | `/api/media/merge` | Merge multiple local video clips into one output. | [server/routes/media_merge.py](../server/routes/media_merge.py) |
| `POST` | `/api/media/fetch-url` | Download remote media into local storage. | [server/routes/media_fetch.py](../server/routes/media_fetch.py) |

### `server/routes/retarget.py`

| Method | Path | Brief | Handler |
|---|---|---|---|
| `POST` | `/api/retarget` | Extract a reference frame from a video and queue a retarget job. | [server/routes/retarget.py](../server/routes/retarget.py) |

### `server/routes/llm.py`

| Method | Path | Brief | Handler |
|---|---|---|---|
| `POST` | `/api/llm/auto-prompt` | Generate a first-pass prompt from a topic/style pair. | [server/routes/llm.py](../server/routes/llm.py) |
| `POST` | `/api/llm/expand-prompt` | Expand a short idea into a fuller prompt. | [server/routes/llm.py](../server/routes/llm.py) |
| `POST` | `/api/llm/shot-list` | Generate a structured shot list for a scene. | [server/routes/llm.py](../server/routes/llm.py) |

### `server/routes/prompt_builder.py`

| Method | Path | Brief | Handler |
|---|---|---|---|
| `POST` | `/api/prompt-builder/assemble` | Assemble a deterministic prompt string from structured fields. | [server/routes/prompt_builder.py](../server/routes/prompt_builder.py) |

### `server/routes/product_pipeline.py`

| Method | Path | Brief | Handler |
|---|---|---|---|
| `POST` | `/api/product-pipeline/` | Queue the fixed product-ad workflow chain. | [server/routes/product_pipeline.py](../server/routes/product_pipeline.py) |

### `server/routes/uploads.py`

| Method | Path | Brief | Handler |
|---|---|---|---|
| `POST` | `/api/uploads` | Validate and persist one uploaded image asset. | [server/routes/uploads.py](../server/routes/uploads.py) |

### `server/routes/ws.py`

| Method | Path | Brief | Handler |
|---|---|---|---|
| `WS` | `/ws/jobs` | Stream `job_update` events and periodic keepalive `ping` events. | [server/routes/ws.py](../server/routes/ws.py) |

## 9. UI map

Base route anchors live in [frontend/index.html](../frontend/index.html). Dynamic hashes such as `#jobs/{chain_id}` and `#job-detail/{job_id}` ride the generic hash router, while alias `#job/{job_id}` is patched in page code.

| File | Page | Hash route | Backend endpoints consumed | Notes |
|---|---|---|---|---|
| [frontend/js/pages/home.js](../frontend/js/pages/home.js) | Home | `#home` | `GET /api/jobs`, `GET /api/jobs/{id}`, `WS /ws/jobs` | Page still requests `?limit=` through `API.jobs.list()`, which backend ignores on current `master`. |
| [frontend/js/pages/dashboard.js](../frontend/js/pages/dashboard.js) | Dashboard | `#dashboard` | `GET /api/jobs/counts`, `GET /api/jobs`, `POST /api/jobs/recover`, `GET /api/jobs/{id}`, `DELETE /api/jobs/{id}`, `WS /ws/jobs` | Recent-job widget still passes `?limit=20`; backend ignores it. |
| [frontend/js/pages/create-job.js](../frontend/js/pages/create-job.js) | Create Job | `#create` | `GET /api/profiles`, `POST /api/jobs`, `POST /api/uploads` | Top-level nav route. |
| [frontend/js/pages/chain-builder.js](../frontend/js/pages/chain-builder.js) | Chain Builder | `#chains` | `GET /api/profiles`, `POST /api/chains` | Top-level nav route. |
| [frontend/js/pages/profiles.js](../frontend/js/pages/profiles.js) | Profiles | `#profiles` | `GET /api/profiles`, `POST /api/profiles`, `POST /api/profiles/{name}/quarantine`, `POST /api/profiles/{name}/activate` | Top-level nav route. |
| [frontend/js/pages/settings.js](../frontend/js/pages/settings.js) | Settings | `#settings` | `GET /health`, `GET /api/jobs/counts`, `POST /api/jobs/recover`, `GET /api/jobs`, `DELETE /api/jobs/{id}` | Top-level nav route. |
| [frontend/js/pages/characters.js](../frontend/js/pages/characters.js) | Characters | `#characters` | `GET/POST /api/characters`, `GET/PUT/DELETE /api/characters/{id}`, `POST /api/uploads` | Top-level nav route. |
| [frontend/js/pages/workflows.js](../frontend/js/pages/workflows.js) | Workflows | `#workflows` | `GET /api/templates`, `POST /api/templates/{id}/instantiate`, `POST /api/llm/auto-prompt`, `POST /api/llm/expand-prompt`, `POST /api/prompt-builder/assemble` | Top-level nav route. |
| [frontend/js/pages/media-tools.js](../frontend/js/pages/media-tools.js) | Media Tools | `#media-tools` | `GET /api/profiles`, `POST /api/media/cut`, `POST /api/media/merge`, `POST /api/media/fetch-url`, `POST /api/retarget` | Top-level nav route. |
| [frontend/js/pages/tts.js](../frontend/js/pages/tts.js) | Text to Speech | `#tts` | `POST /api/tts` | Top-level nav route. |
| [frontend/js/pages/jobs.js](../frontend/js/pages/jobs.js) | Jobs | `#jobs` plus dynamic `#jobs/{chain_id}` | `GET /api/jobs`, `GET /api/jobs/{id}`, `POST /api/jobs`, `DELETE /api/jobs/{id}`, `POST /api/jobs/recover`, `GET /api/profiles`, `WS /ws/jobs` | `#jobs/{chain_id}` is page-parsed, not a standalone nav anchor. |
| [frontend/js/pages/gallery.js](../frontend/js/pages/gallery.js) | Gallery | `#gallery` | `GET /api/jobs?status=completed`, `GET /api/profiles`, `GET /api/jobs/{id}`, `WS /ws/jobs` | Top-level nav route. |
| [frontend/js/pages/batch-queue.js](../frontend/js/pages/batch-queue.js) | Batch Queue | `#batch-queue` | `GET /api/profiles`, `POST /api/jobs`, `WS /ws/jobs` | Top-level nav route. |
| [frontend/js/pages/job-detail.js](../frontend/js/pages/job-detail.js) | Job Detail | `#job-detail` base anchor plus dynamic `#job-detail/{job_id}` and alias `#job/{job_id}` | `GET /api/jobs/{id}`, `GET /api/jobs/{id}/children`, `POST /api/jobs`, `DELETE /api/jobs/{id}`, `WS /ws/jobs` | `#job-detail` is an off-nav base anchor in `index.html`, `#job-detail/{job_id}` rides the base router, and `job-detail.js` patches routing additionally to accept the off-nav alias `#job/{job_id}`. |
| [frontend/js/pages/chain-tree.js](../frontend/js/pages/chain-tree.js) | Chain Tree | `#chain-tree` | `GET /api/jobs`, `GET /api/chains/{id}`, `GET /api/jobs?chain_id=...` | Top-level nav route. |
| [frontend/js/pages/engine-status.js](../frontend/js/pages/engine-status.js) | Engine Status | `#engine-status` | `GET /health`, `GET /api/jobs/counts`, `GET /api/profiles`, `GET /api/jobs?status=pending`, `GET /api/jobs?status=failed`, `GET /api/jobs/{id}`, `WS /ws/jobs` | Top-level nav route. |

## 10. Deferred / archived scope

| Scope | State | Reason | Source |
|---|---|---|---|
| `audio-to-video` | Archived; removed from FlowEngine | Deliberately removed in commit `cfead65` on 2026-04-28 and not re-added during the public cutover. Current `JobType` enum does not expose it. | [cutover report:117](session-reports/2026-05-01_web-ai-hassio-flowengine-cutover.md#L117), [server/models/job.py](../server/models/job.py) |
| `remix-video` | Archived legacy scope only | Never added to FlowEngine repo (legacy stub / ImportError fallback only). | [cutover report:118](session-reports/2026-05-01_web-ai-hassio-flowengine-cutover.md#L118) |
| `shorten-video` | Archived legacy scope only | Never added to FlowEngine repo (legacy stub / ImportError fallback only). | [cutover report:119](session-reports/2026-05-01_web-ai-hassio-flowengine-cutover.md#L119) |
| Legacy `image-to-video` bundle | Replaced; do not resurrect as one merged op | Current engine matches the real Flow UI split: `frames-to-video` and `ingredients-to-video` are separate L1 operations and are the intended replacement. | [cutover report:73-74](session-reports/2026-05-01_web-ai-hassio-flowengine-cutover.md#L73), [server/models/job.py](../server/models/job.py) |
| `safety_filter` | Archived / legacy only | Removed from live job API at `1dd0e80`; template/schema remnants (`jobs.safety_filter` DDL column, `TemplateStep` field) still exist in current code, so runtime guidance is "do not wire or persist". | [docs/SAFETY_FILTER_NOTE.md](SAFETY_FILTER_NOTE.md), [server/db/database.py](../server/db/database.py) |

## 11. How to add a new feature

1. Branch from `master` using `claude/<descriptive-slug>` and keep the PR base explicit as `master`.
2. If the feature adds a new Flow operation, update `JobType` and `JobCreate` validation in [server/models/job.py](../server/models/job.py), add `flow/operations/<name>.py`, wire it into `HANDLER_MAP` in [worker/dispatcher.py](../worker/dispatcher.py), update per-type timeout/no-signal handling in [flow/wait.py](../flow/wait.py), plumb the literal type through the operation wait call (`wait_for_completion(..., job_type="<new-type>")`) following the pattern in [flow/operations/_base.py](../flow/operations/_base.py), and thread any new fields through `Job`, store serialization, SQLite DDL, and tests.
3. Mirror every new job type in the frontend: update [frontend/js/constants.js](../frontend/js/constants.js), the Create Job selector plus L1/L2/input gating sets and icons in [frontend/js/pages/create-job.js](../frontend/js/pages/create-job.js), the Chain Builder gating lists (`FIRST_TYPE`, `L1_ONLY_TYPES`, `SUBSEQUENT_TYPES`), add-button copy, and validation in [frontend/js/pages/chain-builder.js](../frontend/js/pages/chain-builder.js), and shared icon helpers in [frontend/js/app.js](../frontend/js/app.js). If you add a type without updating those lists, it will appear in the wrong chain position.
4. If the feature adds an API route, extend an existing domain router when possible. If you truly need a new router module, create `server/routes/<name>.py`, export it from [server/routes/__init__.py](../server/routes/__init__.py), then import/include it in [server/app.py](../server/app.py); add/update the matching model under `server/models/` and add tests.
5. If the feature adds a new UI page, copy an existing module such as [frontend/js/pages/create-job.js](../frontend/js/pages/create-job.js) or [frontend/js/pages/batch-queue.js](../frontend/js/pages/batch-queue.js) and keep the page-module IIFE shape:

```js
(() => {
  const ExamplePage = {
    name: 'example',
    title: 'Example',
    async render() { return '<div>...</div>'; },
    mount() {},
    destroy() {},
  };

  App.register(ExamplePage);
})();
```

Frontend pages are plain global scripts, not bundled modules: wrap page-local state in an IIFE, load after `constants.js` / `api.js` / `ws.js` / `app.js`, then call `App.register()`.

6. For a new UI page, `App.register({ name, title, render, mount?, destroy? })` is required; `name` must match the hash route segment. Then add the route anchor in [frontend/index.html](../frontend/index.html), add the nav `<li>` only for top-level pages, and add the `<script>` tag in load order.
7. If the feature touches job chains, re-read [docs/SPEC.md](SPEC.md) first and preserve `INV-1` through `INV-5`; do not trust stale frontend inputs for L2 context.
8. Run `python -m pytest -q` before push.
9. Use `gh pr create --base master` explicitly; do not rely on repo defaults.
10. For non-trivial PRs, get 2 independent Codex reviews before merge, per project convention.
11. If the change affects live Flow behavior, do a real browser verification pass before calling it done: confirm the route/page loads, one end-to-end action succeeds, persisted outputs (`project_url`, `media_id`, `profile`, `output_files` or equivalent artifact path) are correct, dashboard/WS surfaces reflect the change, new job types appear in the Create Job selector, Chain Builder shows them only at the intended root versus subsequent positions, downstream icon rendering is correct, and capture a repo session report if the live Flow/UI behavior changed.
12. Update this spine if the feature changed architecture, deploy topology, route surface, data model, code map, or UI map.

## 12. Glossary

| Term | Meaning |
|---|---|
| `L1` | A level-1 job that starts a new Flow project (`text-to-video`, `frames-to-video`, `ingredients-to-video`, or `text-to-image`). |
| `L2+` | Any descendant job that operates on an existing project/clip context inherited from an upstream job. |
| `Profile` | One Chrome profile directory, which in practice means one Google account identity. |
| `project_url` | Flow project grid URL, for example `/tools/flow/project/{project_id}`. |
| `media_id` | Flow clip/image UUID used to target one specific asset inside a project. |
| `edit_url` | The direct `/edit/{media_id}` URL for one target clip; this is the only supported targeting path. |
| `video_index` | Legacy 0-based grid-card selector from the old chain implementation. Deprecated and forbidden by `INV-2`; never use it for targeting. |
| `chain` | An ordered set of jobs connected by `parent_job_id` and grouped by `chain_id`. |
| `parent_job_id` | Direct upstream job id for one child job. |
| `chain_id` | Shared chain identifier across all jobs in the same chain. |
| `generation_id` | Flow generation identifier captured after completion and stored on the job row. |
| `BBox` | The Pydantic model type for a normalized bounding box with `x`, `y`, `w`, and `h`. |
| `bbox` | A concrete `BBox` value attached to insert/remove operations. |
| `direction` | Camera preset label for `camera-move`, such as `Dolly in` or `Center`. |
| `safety_filter` | Legacy 3-level enum (`block_most`, `block_some`, `block_few`). Current job API guidance is still "do not wire or persist", but fresh `jobs` DDL still includes an unused `safety_filter` column and `TemplateStep` still accepts the enum. |
| `composer chip` | The Flow UI chip/menu used to switch composer mode or sub-mode (`Video`, `Frames`, `Ingredients`, image mode, aspect chips, and similar controls). |
| `Veo Lite LP` | `veo-3.1-lite-lp`, the current default video model; `LP` means lower priority. |
| `Veo Fast LP` | `veo-3.1-fast-lp`, another lower-priority/free-tier Veo variant still used as a route sentinel on the text-to-image path. |
| `ProjectLock` | The worker-side in-memory mutex keyed by `project_url` that prevents parallel edits to one Flow project. |
| `ProfileSwapper` | Worker helper that archives a burned profile and replaces it with the next fresh credential. |
| `capture_failure` | Best-effort async diagnostic-bundle helper that writes screenshot/HTML/network artifacts and may return the screenshot path later surfaced as `[cap=...]`. |
| `Flow landing` | The marketing/CTA page at `labs.google/fx/...` that sometimes appears instead of the real Flow app and must be recovered from. |

## 13. Pointers

- [CLAUDE.md](../CLAUDE.md) - session canon for Claude/Codex work in this repo.
- [docs/DESIGN.md](DESIGN.md) - full architecture, design decisions, and historical rationale.
- [docs/SPEC.md](SPEC.md) - invariants (`INV-N`), code rules, and test contract.
- [docs/WORKPLAN.md](WORKPLAN.md) - roadmap and active work plan.
- [docs/FLOW_MULTILEVEL_JOBS.md](FLOW_MULTILEVEL_JOBS.md) - chain behavior history and multilevel Flow specifics; historical / pre-INV-2 for `video_index` targeting.
- [docs/FLOW_PIPELINE_KNOWLEDGE.md](FLOW_PIPELINE_KNOWLEDGE.md) - pipeline/domain knowledge gathered from live probing; historical / pre-INV-2 where it still describes `video_index` targeting.
- [docs/FLOW_UI_REFERENCE.md](FLOW_UI_REFERENCE.md) - selector and UI-structure evidence from Google Flow.
- [docs/CHROME_LAUNCH_SECURITY.md](CHROME_LAUNCH_SECURITY.md) - Chrome anti-detection and launch-security notes.
- [docs/SAFETY_FILTER_NOTE.md](SAFETY_FILTER_NOTE.md) - why the 3-level safety filter is legacy only.
- [docs/session-reports/INDEX.md](session-reports/INDEX.md) - chronological session report index.
- Latest public-cutover report: [docs/session-reports/2026-05-01_web-ai-hassio-flowengine-cutover.md](session-reports/2026-05-01_web-ai-hassio-flowengine-cutover.md).
