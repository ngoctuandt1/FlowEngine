# FlowEngine — Claude Context

First read: `docs/PROJECT_SPINE.md`

> **Code-quality bar (non-negotiable):** hãy viết code như 1 cao thủ, vì user cho codex review code này. Mọi diff (tự code hoặc handed-out qua prompt) phải qua được senior-reviewer bar. Xem memory `feedback_code_quality_codex_review.md`.

## 1. Project Overview

FlowEngine is a browser-automation engine for **Google Flow** (`labs.google/fx/tools/flow`).
It runs headless Chrome via Playwright to create and chain video operations (text-to-video,
extend, insert-object, remove-object, camera-move) across multiple Google accounts in parallel.

Rebuilt from scratch from `D:/AI/AI-Engine3-Project/` (old engine, 41 modules, monolithic app.py)
to fix 6 critical multi-level job bugs. The key invariant: **a video project belongs to one Google
account — all jobs in a chain must run on that same account's Chrome profile.**

---

## 2. Architecture

```
frontend/          Vanilla JS SPA (dark theme) — dashboard, job creator, chain builder
    ↕ REST + WebSocket
server/            FastAPI + SQLite job queue — no browser code here
    ↕ HTTP (claim / update jobs)
worker/            Claim loop + profile manager + project lock + dispatcher
    ↕ Playwright
flow/              FlowClient + 5 operation modules (generate, extend, insert, remove, camera)
    ↕ Chrome profiles
```

**Server** (`run_server.py` -> `server/app.py`) - FastAPI, lifespan init, mounts frontend static files, and enables the signed-cookie dashboard gate when `DASHBOARD_PASSWORD` is set.<br>
**Worker** (`run_worker.py` → `worker/main.py`) — polls server, claims jobs, dispatches to `flow/`.  
Communication is plain HTTP — worker calls `worker/remote_api.py` to claim jobs and post results.

---

## 3. Key Files

| File | Purpose |
|---|---|
| `server/app.py` | FastAPI app, lifespan, CORS, static mount |
| `server/dashboard_auth.py` | Signed-cookie dashboard gate (`/login`, `/api/auth/*`); active when `DASHBOARD_PASSWORD` is set |
| `server/routes/jobs.py` | Job CRUD: `POST /api/jobs`, `POST /api/chains` |
| `server/routes/worker.py` | `POST /api/worker/claim`, `PATCH /api/jobs/{id}` |
| `server/db/job_store.py` | SQLite CRUD (aiosqlite), `_row_to_job` helper |
| `server/models/job.py` | Pydantic `Job`, `JobCreate`, `JobUpdate`, `BBox` |
| `worker/dispatcher.py` | Routes `job.type` → async handler; creates `FlowClient` |
| `worker/profile_manager.py` | Tracks available/busy Chrome profiles |
| `worker/project_lock.py` | Ensures only one job per `project_url` at a time |
| `flow/client.py` | `FlowClient` — Playwright browser lifecycle, context manager |
| `flow/operations/generate.py` | `run_generate()` — text-to-video |
| `flow/operations/extend.py` | `run_extend()` — extend-video |
| `flow/operations/camera.py` | `run_camera()` — camera-move (uses extend UI, different buttons) |
| `flow/operations/insert.py` | `run_insert()` — insert-object with bbox |
| `flow/operations/remove.py` | `run_remove()` — remove-object with bbox |
| `flow/submit.py` | Submit button click + API-call confirmation |
| `flow/wait.py` | Poll for video completion |
| `flow/media_id.py` | Extract `media_id` from URL / network / DOM |
| `flow/navigation.py` | `edit_url(project_url, media_id)` helper, navigate helpers |
| `flow/model_selector.py` | LP model panel — open, pick, dismiss (click-to-close, not Escape) |

---

## 4. Job System

### Job levels
- **Project**: Flow project = container, identified by `project_url`. Created either via UI (empty) or implicitly by the first L1 in it. Holds N L1 generations.
- **L1** (`job_level=1`): generation inside a project (`text-to-video` / `text-to-image` / `frames-to-video` / `ingredients-to-video`). N L1 can coexist in one project (= **L1 siblings**: same `project_url`, no `parent_job_id`). Mints a new `media_id`.
- **L2** (`job_level=2`): op on a specific L1 output (`extend-video` / `camera-move` / `insert-object` / `remove-object`). Requires `parent_job_id` = L1 job, inherits L1's `project_url` + `media_id`. N L2 sharing the same L1 parent = **L2 siblings**. Must run on **same profile as parent**.
- **L3+** (`job_level≥3`): op stacked on L2/L3 output. `parent_job_id` = the immediate L2/L3 job, same profile + same project as the L1 root.

### Critical fields on every job
```
type          text-to-video | frames-to-video | ingredients-to-video | text-to-image | extend-video | insert-object | remove-object | camera-move
job_level     1 or 2+
parent_job_id link to L1 (or previous L2) job
chain_id      shared across all jobs in one chain
project_url   created at L1, inherited by all L2+
media_id      Re-extracted per op. Extend mints NEW uuid always. Camera-move mints NEW on early-chain (L2 off L1) but preserves on deep-chain. L2 insert/remove mint NEW outputs; extraction resolved 2026-04-23 by trusting `/pq/api` network events over DOM tile over URL clip-route (commits `a771d86` / `1183a24` / `0bb9d29` / refactor `b62ac73`). Child job inherits DIRECT parent's media_id + edit_url together (B22; B30/B32 walk-up superseded 2026-04-20 after Run 20 follow-up). See SPEC INV-5.
profile       Chrome profile dir name (= Google account identity)
bbox          {x,y,w,h} normalized — required for insert/remove
direction     preset string — required for camera-move
```

### Chain invariants
1. **Same profile** on every job in the chain — different account = 404 on project_url
2. **Navigate by `edit_url`** (`/edit/{media_id}`) — never use `video_index` / DOM card counting
3. **Store everything** after completion: `project_url`, `media_id`, `profile`, `generation_id`
4. **Serial per project** — `project_lock.py` ensures no two jobs run on same `project_url`
5. `media_id` is **re-extracted per op** — extend-video always mints NEW; camera-move mints NEW on early-chain (L2 direct off L1) but preserves on deep-chain; L2 insert/remove mint NEW outputs and extraction is resolved as of 2026-04-23 (`flow/operations/_base.py:finalize_operation` resolves network-mid → DOM tile → URL fallback; refactored to `resolve_final_media_id` helper in `b62ac73`). Child inherits DIRECT parent's `media_id` + `edit_url` together (B22; B30/B32 walk-up superseded 2026-04-20 — see `849834e` and [2026-04-23_l2-media-id-fix-live-verified.md](docs/session-reports/2026-04-23_l2-media-id-fix-live-verified.md)).

### Claim flow
Worker `main.py` → `POST /api/worker/claim` with `profiles` list → server returns job where
`job.profile IN profiles` (or `profile IS NULL` for unclaimed L1) → worker locks profile →
dispatches → on completion `PATCH /api/jobs/{id}` with result fields → releases profile.

### Failure / recovery path
- `flow/wait.py` raises `RecaptchaError` on visible reCAPTCHA or on network-level `403/429` reCAPTCHA signals.
- Dispatcher treats that as a **burned profile**. The active job fails as `recaptcha_<kind>_burned_<profile>`.
- With `FLOW_AUTO_REPLACE_PROFILES=1` (default), `worker/profile_swapper.py` archives `<profile>.burned-*`, warms the next fresh credential from `FLOW_PROFILE_LIST_FILE` / `profiles_ultra.txt`, and resumes the worker pool on the next claim cycle.
- Replacement restores worker capacity for future claims on the fresh account; it does **not** move an in-flight chain to a different Google account.

---

## 5. Dev Conventions

### Branches
```
claude/bug-N-slug        bug fix for issue #N
claude/<adjective-name>  general / exploratory worktrees (auto-named)
```

### Commit / PR format
```
fix(scope): short description   ← commit message
fix(#N): short description      ← PR title when closing an issue
Body: "Closes #N"
```

### Tests
- Location: `tests/`
- Run: `pytest tests/`
- Currently minimal — unit tests for dispatcher routing and job_store helpers

---

## 6. Epic History

**flow-bugs epic (bugs #2–#8) — all merged to `master`** as of 2026-04-16.

| Bug | Branch | Fix |
|---|---|---|
| #2 | `claude/bug-2-store-media-id` | Store `media_id` after every operation |
| #3 | `claude/bug-3-store-project-url` | L2 jobs store `project_url` back |
| #4 | `claude/bug-4-profile-pinning` | Claim filtered by `profile`, L2 inherits profile |
| #5 | `claude/bug-5-nav-media-id` | Navigate via `edit_url`, remove `video_index` logic |
| #6 | `claude/bug-6-camera-move` | `run_camera()` handler + correct dispatcher routing |
| #7 | `claude/bug-7-project-lock` | `ProjectLock` — one job per `project_url` |
| #8 | `claude/bug-8-lp-credit-leak` | LP model selector: click-to-dismiss, not Escape |

**Phase A (B1-B12) — all merged to `master`** as of 2026-04-17. Docs trilogy workflow
(`docs/DESIGN.md` + `docs/SPEC.md` + `docs/WORKPLAN.md` + `docs/session-reports/`).

| Bug | Commit | Fix |
|---|---|---|
| B7 | `a95c9b5` | Unify server port default 8000 → 8080 |
| B9 | `adca116` | Test foundation (pytest + fixtures + temp DB + api_client) |
| B8 | `573cffd` | Migrate 7× `datetime.utcnow()` → `datetime.now(UTC)` |
| B5 | `4d24c10` | Auto-set `completed_at` on terminal job status |
| B6 | `0118e6d` | Track `profiles.current_job_id` on claim/complete |
| B1 | `b359c84` | Aspect ratio via Radix chip + `[id$="-trigger-PORTRAIT\|LANDSCAPE"]` |
| B2 → B11 | `a165105` → `ce6683a` | Bbox: target largest `<canvas>` (≥300px), pointer-trust verify (was: wrong `<video>` thumbnail) |
| B3 → B12 | `58937d4` → `78d3e40` | Camera preset: verify via `getComputedStyle(labelDiv).color` R+G+B<400 (was: aria-pressed signals never present) |
| B4 | `4dcf50f` | Persist chain metadata + aggregated status API (un-deferred 2026-04-18) |
| B10 | `fe13870` | Migrate `default_factory=datetime.utcnow` → tz-aware (un-deferred 2026-04-18) |
| B13 | inline `9facbe3` | Resolved inline (docs cleanup) |

**Validation tiers:**
- Tier 1 round 1 (`9facbe3`) — Chrome MCP live DOM probe: B1 ✅; B2/B3 ❌ flipped → B11/B12 created
- Tier 1 round 2 (`db4c746`) — re-probe after fixes: B11/B12 ✅ verified live

**Tag:** `v0.2.0-phase-a` at `db4c746`.

**Post-Phase-A (2026-04-20) — merged to `master`**

| PR | Commit | Fix |
|---|---|---|
| #24 | `18a1e74` | Image 2K/4K UI path + async busy/done state machine |
| #25 | `f930739` | Composer-chip fallback for `_switch_to_image_output` after persisted Video mode |
| #26 | `429dad6` | Unified composer-menu selectors across 5 mode-icon variants |
| #27 | `849c39d` | +36 unit tests for the image upscale path |
| #28 | `ef09a13` | Iterate all image `media_ids` in the UI upscale branch |

- Test suite count moved from `153` to `192`.
- Live-verified on 2026-04-20: image 4K `text-to-image` x3 on `ngoctuandt20`; L2 insert + remove on the same project.
- L2 `media_id` extraction bug for insert/remove: **RESOLVED 2026-04-23** (commit `0bb9d29`, refactor `b62ac73`, doc PR `e79405d`/#53). See [2026-04-23_l2-media-id-fix-live-verified.md](docs/session-reports/2026-04-23_l2-media-id-fix-live-verified.md).

**2026-05-01 recovery + diagnostics cluster — merged to `master` at `ebb9569`**

| PR | Commit | Fix |
|---|---|---|
| #75 | `6451e72` | Add `pyotp` dependency for auto-login TOTP flows |
| #76 | `fbc310c` | Accept both `1x` and `x1` output-count chip text in verify logic |
| #77 | `668592e` | Guard Linux root Chrome launches behind `FLOW_ALLOW_ROOT_NO_SANDBOX=1` |
| #78 | `9dd4942` | Redesign the livetest sweep into 4 separate L1-rooted chains |
| #79 | `1d34623` | Detect invisible reCAPTCHA v3 from network signals and prioritize that path in the wait loop |
| #80 | `71c1cf9` | Add `ProfileSwapper` burn-and-replace helper |
| #82 | `1bf6779` | Wire `RecaptchaError` → `ProfileSwapper` in the dispatcher |
| #83 | `b62b878` | Add `flow/diagnostics.py::capture_failure()` forensic capture helper |
| #85 | `ebb9569` | Wire forensic capture into chrome-time raise sites and append `[cap=<path>]` to surfaced errors |

**2026-05-01 public ai.hassio.io.vn cutover - merged to `master` at `6a6b6a3`**

The public `ai.hassio.io.vn` route was migrated from the archived legacy
`video-ai-studio` deployment to FlowEngine via the #90-#108 page/auth/hardening
train: TTS, characters/workflows, media tools, jobs/gallery, engine status,
batch queue, job detail, chain tree, follow-up FE fixes, dashboard password
gate, WS keepalive, upload magic-byte validation, POSIX Chrome reap, and
CORS/ASGI/proxy hardening. Debian kept the existing Cloudflare Tunnel route by
binding FlowEngine on `0.0.0.0:8899`. Full deploy steps, live verification, and
rollback tags are captured in
[2026-05-01_web-ai-hassio-flowengine-cutover.md](docs/session-reports/2026-05-01_web-ai-hassio-flowengine-cutover.md).

**2026-05-01 -> 2026-05-02 SPINE canon sync - merged to `master` at `cf991e0`**

Docs-only `#109-#125` canon-sync sweep: `PROJECT_SPINE.md` established as the repo spine, linked canon docs were synced, and doc review surfaced 6 real bugs fixed on `master`. See [2026-05-02_spine-doc-canon-sync.md](docs/session-reports/2026-05-02_spine-doc-canon-sync.md).

- Current `master` head for this docs sync: `cf991e0`.

For future epics: create `docs/PRD_<EPIC>.md`, open issues on GitHub, branch `claude/bug-N-slug`
per issue, one PR per issue with `Closes #N`.

---

## 7. Common Gotchas

**gh CLI path on Windows (Git Bash / worktrees):**
```bash
export PATH="/c/Program Files/GitHub CLI:$PATH"
# or call directly:
"/c/Program Files/GitHub CLI/gh.exe" pr create ...
```

**Worktrees don't inherit PATH** - if a worktree shell can't find `gh`, `python`, or `pytest`,
set PATH explicitly. Each worktree is under `.claude/worktrees/<name>/`.

**Debian public deploy / Cloudflare Tunnel** - set `TRUST_PROXY_HEADERS=1` in
`/etc/flowengine/flowengine.env` and launch uvicorn with `--proxy-headers --forwarded-allow-ips=*`
so `server/dashboard_auth.py` sees HTTPS and marks the signed cookie `Secure`.
Public cutover keeps FlowEngine on `0.0.0.0:8899` (not `8090`) so the existing
route for `ai.hassio.io.vn` does not move.

### Recovery + diagnostics

- **reCAPTCHA burn-and-replace** — invisible v3 is detected from network `403/429` reCAPTCHA signals and visible v2 still raises from DOM checks. `FLOW_AUTO_REPLACE_PROFILES=1` is the default, so burned profiles are archived and swapped for the next fresh credential automatically. See memory `feedback_recaptcha_wipe_rewarm.md`.
- **Forensic capture** — chrome-time raise sites attempt a forensic bundle under `FLOW_ERROR_CAPTURE_DIR`: `<ts>_<job>_<kind>.png`, `.network.json`, and `.html`. Surfaced errors append `[cap=<path>]` when the screenshot path exists. See memory `feedback_flow_error_screenshot_required.md`.
- **Linux root** — run the worker as a non-root user. `FLOW_ALLOW_ROOT_NO_SANDBOX=1` is the explicit escape hatch that opts into `--no-sandbox`.
- **LP → Lite migration** — deadline `2026-05-10`; see memory `project_lp_deprecation_2026_10_05.md`.

**Model panel close** — the LP model selector panel must be dismissed by clicking outside it,
NOT with Escape. Escape closes the whole editor dialog. See `flow/model_selector.py`.

**Chrome profile dir** — profiles live under `CHROME_USER_DATA_DIR` (env var, default
`./chrome-profiles`). Profile name = subdirectory name = Google account identity.

**submit.py timeout** — `run_submit()` returns `False` on timeout (not exception). Caller
must check return value; only NEW API calls (after submit click) count as confirmation.

**Flow download menu labels (2026-04-20):**
- Video menu — `270pOriginal size / 720p / 1080pUpscaled / 4KUpscaled`. The 50-credit engine targets `1080pUpscaled`, NEVER `4K` (costs credits).
- Image menu — `1K / 2K / 4K` on newline-separated labels. Image `2K`/`4K` UI path is env-gated by `FLOW_IMAGE_QUALITY=2k|4k` (default `original`). See memory `feedback_image_upscale_2k_4k.md`.

**Key docs** (in `docs/`):
- `FLOW_MULTILEVEL_JOBS.md` — complete multi-level design, bugs, test results
- `FLOW_PIPELINE_KNOWLEDGE.md` — Flow UI technical reference
- `FLOW_UI_REFERENCE.md` — UI element selectors (Vietnamese + English)
