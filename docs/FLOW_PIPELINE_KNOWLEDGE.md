# Flow Engine Pipeline — Complete Technical Knowledge Base

> Last updated: 2026-04-16
> Purpose: Handoff document for AI agents working on this codebase
> Source: hands-on browser testing + full code trace of 41 flow modules

## 1. Architecture Overview

```
User (Web UI)  →  Server (app.py)  →  Queue (jobs.py)  →  Engine Worker  →  Dispatcher  →  generation.py  →  FlowClient (Playwright)  →  Google Flow UI
                                                                                                                      ↓
                                                                                                              flow_generation_pipeline_steps.py
                                                                                                              flow_submit_steps.py
                                                                                                              flow_wait_steps.py
                                                                                                              flow_download_steps.py
                                                                                                              flow_model_steps_v2.py
                                                                                                              flow_media_id_steps.py
                                                                                                              flow_upscale_ui.py
                                                                                                              ... (41 modules total)
```

### Key Components
- **app.py**: FastAPI server, job creation API endpoints, queue management
- **engine_worker.py**: Poll loop, claim jobs, dispatch to ThreadPoolExecutor
- **modules/dispatcher.py**: `dispatch_job()` routes job_type → correct bg_* function
- **modules/generation.py**: `bg_text_to_video`, `bg_extend_video`, `bg_insert_object`, `bg_remove_object` — each wraps FlowClient
- **flow_client.py**: Playwright browser automation wrapper, manages Chrome profile
- **modules/flow_generation_pipeline_steps.py**: Core pipeline — `generate_video()`, `extend_video()`, `insert_object()`, `remove_object()`

## 2. Job Types and Their Pipeline

### 2.1 text-to-video (Level 1)

**API**: `POST /api/text-to-video`

**Required fields**: `prompt`
**Optional**: `model` (default "veo-3.1-fast-lp"), `aspect_ratio` (default "9:16"), `variations` (default "x1"), `free_mode` (default true), `style`, `negative_prompt`

**Execution flow**:
```
bg_text_to_video()
  → FlowClient.start() — launch Chrome with profile
  → client.generate_video_from_prompt()
    → flow_generation_pipeline_steps.generate_video()
      1. check_account() — verify ULTRA tier + credits
      2. Ensure project canvas (sticky_project or new)
      3. select_model() — open model dropdown, pick Veo 3.1 LP
      4. Upload reference frames/ingredients (if any)
      5. Set aspect ratio
      6. Type prompt in composer
      7. _submit_with_confirmation() — click submit + verify via network/DOM
      8. _wait_done(timeout=900) — poll progress %, network calls, DOM changes
      9. upscale_and_download() — get 1080p video file
  → update_job() with output paths, project_url, profile
```

**Data stored after completion**: `output` (file paths), `project_url`, `profile`, `model`, `aspect_ratio`, `variations`

### 2.2 extend-video (Level 2)

**API**: `POST /api/extend-video`

**Required fields**: one of `project_url`, `video_media_id`, or `parent_job_id`
**Optional**: `prompt`, `model`, `video_index` (default 0), `free_mode`, `aspect_ratio`, `variations`

**Execution flow**:
```
bg_extend_video()
  → Validate project_url contains /tools/flow/project/
  → FlowClient.start()
  → client.extend_video(project_url, video_index, prompt, ...)
    → flow_generation_pipeline_steps.extend_video()
      1. page.goto(project_url) — navigate to project grid
      2. _scan_extend_surface() — scan DOM for video/img/tile cards
      3. Click card at video_index position
      4. Click "Extend"/"Mở rộng" button (3 text variants + aria fallback)
      5. Type extend prompt (optional)
      6. _submit_with_confirmation()
      7. _wait_done(timeout=600) — with stale-progress retry (up to 3x)
      8. upscale_and_download()
  → update_job() with output, profile
```

**Data stored after completion**: `output`, `profile` only (**MISSING**: project_url, media_id, model)

### 2.3 insert-object (Level 2)

**API**: `POST /api/insert-object`

**Required fields**: `prompt` + one of `project_url`/`video_media_id`/`parent_job_id`
**Optional**: `bbox` ({x, y, w, h} floats 0-1), `video_index` (default 0)

**Execution flow**:
```
bg_insert_object()
  → Validate project_url
  → FlowClient.start()
  → client.insert_object(project_url, prompt, bbox, video_index)
    → _edit_object_common(mode="insert")
      1. page.goto(project_url)
      2. Find [data-tile-id] cards, click card at video_index
      3. Click "Insert" button (English only — NO Vietnamese fallback!)
      4. Draw bbox on canvas (mouse drag with normalized coords)
      5. Type prompt
      6. Click Create/Generate button
      7. _wait_done(timeout=300) — progress=100 accepted as success
      8. Exit edit mode (Done/Close/Back)
      9. upscale_and_download()
  → update_job() with output, profile
```

**Data stored**: `output`, `profile` only (**MISSING**: project_url, media_id)

**NOTE**: No local dispatch in app.py — queue-only, Docker/remote workers only.

### 2.4 remove-object (Level 2)

**API**: `POST /api/remove-object`

**Required fields**: one of `project_url`/`video_media_id`/`parent_job_id`
**Optional**: `bbox`, `video_index` (default 0)

**Execution flow**: Same as insert, with `mode="remove"`:
- Clicks "Remove" button instead of "Insert"
- Bbox is REQUIRED (no prompt needed)
- No prompt parameter in create_job

**Data stored**: `output`, `profile` only

**NOTE**: No local dispatch — queue-only.

### 2.5 camera-move (Level 2) — BROKEN

**API**: `POST /api/camera-move`

**Stored as**: `type="extend-video"`, `job_category="camera-move"`, `direction=<preset>`

**CRITICAL BUG**: `bg_camera_move` does NOT exist in `modules/generation.py`. 
- app.py imports it → ImportError at runtime for local dispatch
- Dispatcher routes by job_type="extend-video" → calls `bg_extend_video` → `direction` field is LOST
- Engine does a normal extend instead of camera preset selection

## 3. Download + Upscale Pipeline

### 3.1 Two Download Paths

**PATH 1: API-driven (primary)** — `download_generated_video()`
```
1. Collect media_ids from: DOM <video> elements, network captures, _media_id_events
2. For each media_id:
   a. Request: media.getMediaUrlRedirect?name={media_id}_upsampled  → 1080p
   b. Poll every 10s, max 180s (FLOW_UPSCALE_MAX_WAIT_SEC)
   c. If fails 3 rounds → fallback: ?name={media_id}  → 720p
3. Download via browser fetch() with credentials
4. Save to: {output_dir}/{prefix}_{quality}_{index}_{timestamp}.mp4
```

**PATH 2: UI-driven (fallback)** — `download_1080p_ui()`
```
1. Restore project canvas
2. Scan DOM for video cards, match by tile_id or media_id
3. Right-click card → "Download" menu → "1080p" submenu
4. Wait for "Upscaling complete" popup (up to 180s)
5. Retry up to 3x on upscale failure
6. Capture browser auto-download
```

**FALLBACK CHAIN** (if both paths fail):
```
blob: URL → fetch in-browser → save as 720p
  ↓ fail
Direct HTTP URL → requests.Session download with cookies
  ↓ fail  
Click Flow download button ([aria-label*="ownload"])
  ↓ fail
Report error
```

### 3.2 Upscale Mechanism

- Upscale = converting 720p generation → 1080p (or 2k/4k)
- Triggered by appending `_upsampled` suffix to media_id in API call
- Can also be triggered by UI right-click menu
- `flow_upscale_ui.py` — `ScopedUpscaleMenu` class automates the right-click menu
- `upscale_unified.py` — `trigger_upscale_signal_only()` fires upscale without downloading

### 3.3 Key Env Vars for Download/Upscale

| Variable | Default | Purpose |
|---|---|---|
| `FLOW_DOWNLOAD_QUALITY` | `1080p` | Target quality |
| `FLOW_UPSCALE_TRY_1080` | `0` | Enable 1080p API polling |
| `FLOW_UPSCALE_MAX_WAIT_SEC` | `180` | Max poll wait |
| `FLOW_UPSCALE_POLL_INTERVAL_SEC` | `10` | Poll interval |
| `FLOW_UPSCALE_1080_FAILS_BEFORE_720` | `3` | Rounds before 720p fallback |
| `FLOW_UPSCALE_UI_TRIGGER_FIRST` | `1` | Click UI upscale before API poll |
| `FLOW_UPSCALE_REQUIRE_UI_SIGNAL` | `1` | Require popup confirmation |

## 4. Submit Detection

`flow_submit_steps.py` — `submit_with_confirmation()`:

1. **Click submit button** — priority bucket system: DOM-marked prompt target → composer testid → extension selectors → ARIA/submit selectors
2. **Keyboard fallback** — Ctrl+Enter if click fails
3. **Verify submission** — watches for:
   - Network POST to generation API
   - Card count increase in DOM
   - Progress bar appearance
   - "Generating" text in DOM
4. **Handles**: prompt re-typing, reCAPTCHA detection, page session recovery

## 5. Wait/Completion Detection

`flow_wait_steps.py` — `wait_done()`:

Default timeout: 300s (video), configurable per job type (t2v=900s, extend=600s, edit=300s)

**Three parallel detection methods**:
1. **Reverse API inspection** — network calls for operations/progress JSON, video/image URL captures
2. **DOM element detection** — new `<video>` or `<img>` elements appearing
3. **Injected JS observer** — tracks progress %, "Generating" text, failure states

**Handles**: reCAPTCHA blocks, Google "oops" errors, auto-retry of failed cards, stale-signal rejection, page recovery, no-signal timeouts

## 6. Model Selection

`flow_model_steps_v2.py` — `select_model()`:

1. Open compose-options panel
2. Switch to Image or Video tab
3. For video in free_mode: pick "Lower Priority" model (0 credits)
4. Verify selection via UI chip text
5. Verify "0 tín dụng" / "0 credits" in DOM footer

**LP (Lower Priority) models** = free tier:
- Veo 3.1 - Lite [Lower Priority] → 0 credits
- Veo 3.1 - Fast [Lower Priority] → 0 credits

## 7. Media ID Handling

`flow_media_id_steps.py`:

- `normalize_media_id(mid)` — strips `_upsampled`, `_720p` suffixes, URL-decodes
- `looks_like_media_id(mid)` — validates UUID/hex format
- `extract_media_ids_from_text(text)` — regex extraction from JSON/URLs
- `record_media_id_event(client, mid, source, url)` — deduplicates, appends to `_media_id_events`
- `collect_media_id_candidates(client)` — aggregates from events + network + DOM

**CRITICAL**: media_id is collected during runtime in FlowClient memory but is **NOT stored back on the job** after completion. This breaks multi-level job chains.

## 8. File Map (41 flow modules)

| Module | Purpose |
|---|---|
| `flow_generation_pipeline_steps.py` | Main pipeline: generate, extend, insert, remove |
| `flow_runtime_steps.py` | Login, account check, network observers |
| `flow_submit_steps.py` | Submit button click + confirmation |
| `flow_submit_detection_steps.py` | Post-submit evidence collection |
| `flow_submit_state_steps.py` | Submit state management |
| `flow_submit_target.py` | Target element for submit |
| `flow_wait_steps.py` | Wait for generation completion |
| `flow_download_steps.py` | Download orchestrator (API + UI paths) |
| `flow_download_transport_steps.py` | Low-level HTTP/fetch transport |
| `flow_upscale_ui.py` | Right-click menu upscale automation |
| `flow_model_steps_v2.py` | Model selector automation |
| `flow_model_steps.py` | Legacy model selection |
| `flow_model_credit_selector.py` | Credit/LP model selection |
| `flow_media_id_steps.py` | Media ID extraction + normalization |
| `flow_navigation.py` | URL validation helpers |
| `flow_project_steps.py` | Project creation/reuse |
| `flow_project_state_steps.py` | Project state tracking |
| `flow_workspace_steps.py` | Workspace/canvas management |
| `flow_compose_steps.py` | Composer textarea interaction |
| `flow_prompt_state_steps.py` | Prompt state management |
| `flow_loading_steps.py` | Loading state detection |
| `flow_ratio_selector.py` | Aspect ratio UI selection |
| `flow_reference_steps.py` | Reference image handling |
| `flow_reference_local_steps.py` | Local reference file upload |
| `flow_frame_slot_steps.py` | Start/end frame upload for i2v |
| `flow_retry_steps.py` | Retry logic for failed operations |
| `flow_failed_card_policy.py` | Policy for handling failed gen cards |
| `flow_autofix.py` | Auto-recovery from errors |
| `flow_auth_resume.py` | Auth session resume |
| `flow_cookie_transfer.py` | Cookie transfer between contexts |
| `flow_cookie_cdp_transfer.py` | CDP-based cookie transfer |
| `flow_cookie_transfer_extension.py` | Extension-based cookie transfer |
| `flow_recaptcha_*.py` | reCAPTCHA handling (5 modules) |
| `flow_extension_selectors.py` | Chrome extension DOM selectors |
| `flow_upload_selectors.py` | File upload element selectors |
| `flow_asset_pipeline_steps.py` | Asset pipeline management |

## 9. Google Flow UI Quick Reference

### URL Structure
```
Homepage:     https://labs.google/fx/{locale}/tools/flow
Project:      https://labs.google/fx/{locale}/tools/flow/project/{project_uuid}
Media edit:   https://labs.google/fx/{locale}/tools/flow/project/{project_uuid}/edit/{media_uuid}
```
- `locale` = determined by Google account language (vi or empty for en)
- No language switcher in Flow UI

### Critical UI Facts
- media_id = UUID in URL `/edit/{uuid}` — NOT visible in UI info panel
- Operations (extend/insert/remove/camera) update video IN-PLACE — same media_id
- Each operation adds 1 entry to history panel (version timeline)
- Generation shows blurry gradient + % progress counter
- Insert/Remove buttons only have English labels in engine code — needs EN Chrome profile
- Camera mode replaces composer with visual preset grid — no text prompt
- LP model = 0 credits, verify by checking "0 tín dụng" in DOM

See `docs/FLOW_UI_REFERENCE.md` for complete VI/EN label mapping.
