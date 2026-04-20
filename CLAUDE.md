# FlowEngine — Claude Context

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

**Server** (`run_server.py` → `server/app.py`) — FastAPI, lifespan init, mounts frontend static files.  
**Worker** (`run_worker.py` → `worker/main.py`) — polls server, claims jobs, dispatches to `flow/`.  
Communication is plain HTTP — worker calls `worker/remote_api.py` to claim jobs and post results.

---

## 3. Key Files

| File | Purpose |
|---|---|
| `server/app.py` | FastAPI app, lifespan, CORS, static mount |
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
- **L1** (`job_level=1`): `text-to-video` — creates a new project; any available profile
- **L2+** (`job_level≥2`): extend / insert / remove / camera — requires `project_url` + `media_id` + **same profile as parent**

### Critical fields on every job
```
type          text-to-video | extend-video | insert-object | remove-object | camera-move
job_level     1 or 2+
parent_job_id link to L1 (or previous L2) job
chain_id      shared across all jobs in one chain
project_url   created at L1, inherited by all L2+
media_id      Re-extracted per op. Extend mints NEW uuid always. Camera-move mints NEW on early-chain (L2 off L1) but preserves on deep-chain. Insert/remove preserve. Child job inherits DIRECT parent's media_id + edit_url together (B22; B30/B32 walk-up superseded 2026-04-20 after Run 20 follow-up). See SPEC INV-5.
profile       Chrome profile dir name (= Google account identity)
bbox          {x,y,w,h} normalized — required for insert/remove
direction     preset string — required for camera-move
```

### Chain invariants
1. **Same profile** on every job in the chain — different account = 404 on project_url
2. **Navigate by `edit_url`** (`/edit/{media_id}`) — never use `video_index` / DOM card counting
3. **Store everything** after completion: `project_url`, `media_id`, `profile`, `generation_id`
4. **Serial per project** — `project_lock.py` ensures no two jobs run on same `project_url`
5. `media_id` is **re-extracted per op** — extend-video always mints NEW; camera-move mints NEW on early-chain (L2 direct off L1) but preserves on deep-chain; insert/remove preserve in-place. Child inherits DIRECT parent's `media_id` + `edit_url` together (B22; B30/B32 walk-up superseded 2026-04-20 — see `849834e` and SPEC §A.1 INV-5).

### Claim flow
Worker `main.py` → `POST /api/worker/claim` with `profiles` list → server returns job where
`job.profile IN profiles` (or `profile IS NULL` for unclaimed L1) → worker locks profile →
dispatches → on completion `PATCH /api/jobs/{id}` with result fields → releases profile.

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
| B4 | — | Deferred (chains table unused, P2) |
| B10 | — | Deferred post-Phase-A (Pydantic `default_factory=datetime.utcnow` residual, P2) |
| B13 | inline `9facbe3` | Resolved inline (docs cleanup) |

**Validation tiers:**
- Tier 1 round 1 (`9facbe3`) — Chrome MCP live DOM probe: B1 ✅; B2/B3 ❌ flipped → B11/B12 created
- Tier 1 round 2 (`db4c746`) — re-probe after fixes: B11/B12 ✅ verified live

**Tag:** `v0.2.0-phase-a` at `db4c746`.

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

**Worktrees don't inherit PATH** — if a worktree shell can't find `gh`, `python`, or `pytest`,
set PATH explicitly. Each worktree is under `.claude/worktrees/<name>/`.

**Model panel close** — the LP model selector panel must be dismissed by clicking outside it,
NOT with Escape. Escape closes the whole editor dialog. See `flow/model_selector.py`.

**Chrome profile dir** — profiles live under `CHROME_USER_DATA_DIR` (env var, default
`./chrome-profiles`). Profile name = subdirectory name = Google account identity.

**submit.py timeout** — `run_submit()` returns `False` on timeout (not exception). Caller
must check return value; only NEW API calls (after submit click) count as confirmation.

**Key docs** (in `docs/`):
- `FLOW_MULTILEVEL_JOBS.md` — complete multi-level design, bugs, test results
- `FLOW_PIPELINE_KNOWLEDGE.md` — Flow UI technical reference
- `FLOW_UI_REFERENCE.md` — UI element selectors (Vietnamese + English)
