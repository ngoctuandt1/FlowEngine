# Session report: Canvas recovery + perf hardening (2026-05-08)

**Master head:** `be9ab96`  
**Commits this session:** `838bcf5` → `be9ab96` (10 commits, 2 merge commits)  
**Deployed:** `be9ab96` live on `debian` (`/opt/flowengine`)

---

## Changes landed

### 1. Flow Canvas page detection + recovery (`838bcf5`, `558cfcc`)

**Problem:** Google Flow introduced a new Canvas/Workflow builder UI (`/chain/...`). Workers that navigated to `labs.google/fx/tools/flow` sometimes landed on this Canvas page instead of the project list, causing infinite time-outs waiting for the "+ New project" button which doesn't exist there.

**Canvas signals (any one is sufficient):**
- Text: "No chain nodes yet" / "This chain does not have any jobs to render yet" / "Open gallery" / "Run Workflow" / "Batch Run"
- URL contains `/chain/`

**Files changed:**
- `flow/landing.py` — added `is_flow_canvas_page(url, page_text)` sync helper and `recover_from_flow_canvas_page(page, logger, homepage_url)` async helper. Recovery strategy: try clicking nav Gallery/Open-jobs if visible, then fall back to `page.goto(homepage_url)` + reload + `dismiss_flow_marketing_landing` call.
- `flow/operations/generate.py` — after `dismiss_flow_marketing_landing(...)` call, added canvas recovery step: `await recover_from_flow_canvas_page(page, logger, homepage_url)`.
- `tests/test_landing.py` — 4 new unit tests: `is_flow_canvas_page` parametric detection, ERR_ABORTED continue, marketing dismiss after homepage reload.

**Note:** Canvas recovery is only wired into `generate.py`. The other L1 ops (`image.py`, `frames_to_video.py`, `ingredients.py`) could also land on Canvas, but they don't have a homepage navigation step — they'd need a separate recovery hook. Tracked as follow-up.

---

### 2. GZip middleware — media path exclusion (`1283e0b`)

**Problem (R1 finding 3):** The original `app.add_middleware(GZipMiddleware, minimum_size=1000)` would compress `Accept-Ranges` responses for `/downloads/` and `/uploads/`. Starlette's GZip bypass for `StaticFiles` only covers the `pathsend` ASGI message type; 206 Partial Content responses (HTML5 video seeking) use `http.response.body` which goes through `apply_compression`, corrupting Content-Range byte offsets and breaking video playback.

**Fix:** Replaced vanilla `GZipMiddleware` with `_APIGZipMiddleware` subclass in `server/app.py`. The subclass checks `scope["path"]` against `MEDIA_PREFIXES` (`"/downloads/"`, `"/uploads/"`) and short-circuits to `self.app(scope, receive, send)` for those paths, leaving API/asset responses compressed normally.

---

### 3. Composite DB index + explicit API limits (`0547a2c`)

- `server/db/database.py`: added `CREATE INDEX IF NOT EXISTS idx_jobs_status_created ON jobs(status, created_at DESC)` — speeds up the common `WHERE status = ? ORDER BY created_at DESC` claim + list query.
- Frontend pages (`jobs.js`, `gallery.js`, `engine-status.js`, `dashboard.js`, `batch-queue.js`): hardcoded explicit `limit=` params on `GET /api/jobs` calls instead of relying on backend silently ignoring the param.

---

### 4. Home gallery unification (`76fdf18`, `20c2eb6`, `69a493a`)

- `frontend/js/pages/home.js`: unified gallery renders one tile per chain (grouped by `chain_id` via `groupByChain`), showing a type badge and the L1 prompt for context. Previously it rendered individual job tiles which made chains look noisy.
- Deep review fixes: server-side limit respected, L1 prompt extracted from chain root, WS debounce prevents double-render, CSS truncation for long prompts.

---

### 5. Event-driven settle waits — PR #204 (`2b927a0`, `be9ab96`)

**Problem:** Four L1 operation files used fixed `asyncio.sleep(2/3/5)` settle waits after page navigation or DOM mutations. These waits were conservative estimates that burned real wall-time on fast/warm sessions.

**Fix:** Replaced all fixed settle sleeps with Playwright event-driven waits (`page.wait_for_selector`, `page.wait_for_function`). Files changed:

| File | Replacements |
|---|---|
| `flow/operations/generate.py` | `sleep(2)` after `goto` → `wait_for_selector` on New Project button; `_wait_for_composer` rewritten to try 3 CSS selectors + placeholder-text `wait_for_function`; post-URL-change sleeps removed |
| `flow/operations/image.py` | `sleep(2)` goto settle → `wait_for_selector` on New Project button; `sleep(3)` pre-overlay → `wait_for_selector` on gallery title; post-URL sleeps removed |
| `flow/operations/frames_to_video.py` | Same goto settle fix; `sleep(3)` before `_dismiss_overlays` → `wait_for_selector`; fallback sleeps reduced |
| `flow/operations/ingredients.py` | Goto settle fix; ingredient upload `sleep(5)` → `wait_for_function` probe on upload button state; `sleep(0.5)` micro-waits → `sleep(0.15)` |

**Expected gain:** ~6–10s per L1 job on warm sessions (pre-loaded profile, no auth redirect).

---

## Test status

```
pytest tests/ -q --tb=short
# All existing tests pass
# 4 new tests in tests/test_landing.py added for canvas recovery
```

---

## Deployment

```bash
ssh debian
cd /opt/flowengine
git pull
sudo systemctl restart flowengine-server flowengine-worker
```

Deployed at `be9ab96`. No migration needed (index is `CREATE IF NOT EXISTS`).

---

## Follow-up items

| Item | Priority | Notes |
|---|---|---|
| Canvas recovery in `image.py`, `frames_to_video.py`, `ingredients.py` | Medium | These ops can also land on Canvas if Flow A/Bs during their homepage nav steps. Current code has no recovery hook for them. |
| Home gallery 400-job window | Low | `home.js` uses `limit=400` which will silently lose older jobs as volume grows. Needs pagination or a dedicated `/api/jobs/recent` endpoint capped at a reasonable horizon. |
| Live test 5× `text-to-image` | Pending | `ngoctuandt20` hit reCAPTCHA (burned). Needs rewarm or a fresh profile with Flow quota before this can run. |
| LP → Lite model fallback | Deadline 2026-05-10 | `Veo Fast LP` EOL May 10. `model_selector.py` needs auto-fallback to Lite when LP option is absent. See memory `project_lp_deprecation_2026_10_05.md`. |
