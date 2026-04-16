# FlowEngine — Project Plan

> Created: 2026-04-16
> Status: Planning phase
> Old project reference: `D:/AI/AI-Engine3-Project/`

## 1. Why Rebuild?

Old engine (AI-Engine3) grew organically over months — 41 flow modules, monolithic app.py (3,767 lines), single-file web UI (9,615 lines). Has 6 critical bugs in multi-level job system that require architectural changes, not patches.

**Key problems in old engine:**
1. media_id never stored after operations — chains break
2. Level-2 jobs don't store project_url back — chain breaks at level 3
3. video_index (DOM position) used for targeting — fragile, breaks when grid changes
4. camera-move handler doesn't exist — bg_camera_move missing
5. No profile pinning — chain can run on wrong Google account
6. No project serialization — concurrent conflicts on same project

## 2. Core Design Principles

### 2.1 Account Binding = Foundation
Every video project belongs to ONE Google account. Multi-level jobs MUST run on the same account. This is THE fundamental constraint the entire system is built around.

### 2.2 Navigate by edit_url, NOT video_index
Direct URL: `/edit/{media_id}` — reliable 100% (tested). No more counting DOM cards.

### 2.3 Store Everything After Every Operation
After any operation completes: store project_url + media_id + profile + generation_id.

### 2.4 Clean Separation of Concerns
- **Server** = API + job queue + web serving (no browser automation)
- **Worker** = browser automation only (Playwright + Chrome profiles)
- **Web UI** = standalone frontend (modern, modular)

### 2.5 Lean Modules
Old engine: 41 flow_*.py files (16K lines). New engine: consolidate to ~10 focused modules.

## 3. Architecture

```
┌─────────────────────────────────────────────────────────┐
│                      Web UI (frontend/)                  │
│  Job dashboard │ Chain builder │ Profile manager │ Logs  │
└────────────────────────┬────────────────────────────────┘
                         │ REST API + WebSocket
┌────────────────────────┴────────────────────────────────┐
│                    Server (server/)                       │
│  FastAPI │ Job Queue (SQLite) │ Auth │ WebSocket hub     │
└────────────────────────┬────────────────────────────────┘
                         │ HTTP (claim/update jobs)
┌────────────────────────┴────────────────────────────────┐
│                   Worker (worker/)                        │
│  Claim loop │ Profile manager │ Project lock │ Dispatch  │
└────────────────────────┬────────────────────────────────┘
                         │ Playwright
┌────────────────────────┴────────────────────────────────┐
│                 Flow Automation (flow/)                   │
│  FlowClient │ Operations │ Download │ Submit │ Wait     │
└─────────────────────────────────────────────────────────┘
                         │ Chrome + Google Flow UI
```

### 3.1 Server (server/)
- **FastAPI** app with clean route modules
- **Job queue**: SQLite-backed, supports parent/child relationships
- **WebSocket**: Real-time job status push to web UI
- **Auth**: API key or simple token auth
- **No browser code** — server never touches Playwright

### 3.2 Worker (worker/)
- **Claim loop**: Poll server for available jobs, filtered by profile
- **Profile manager**: Track which Chrome profiles are available, which are busy
- **Project lock**: Only one job per project_url at a time
- **Dispatcher**: Route job_type → correct operation handler
- Can run on PC (Windows) or Docker (Linux)

### 3.3 Flow Automation (flow/)
- **FlowClient**: Playwright browser wrapper, Chrome profile launch
- **Operations**: generate, extend, insert, remove, camera — each a clean function
- **Download**: API-driven (primary) + UI-driven (fallback) 
- **Submit**: Button click + confirmation detection
- **Wait**: Progress monitoring + completion detection
- **Model**: LP model selector + credit verification

### 3.4 Web UI (frontend/)
- Modern single-page app (HTML/CSS/JS — no framework, keep it simple)
- **Pages**: Dashboard, Job Creator, Chain Builder, Profile Manager, Settings
- Responsive design, dark theme
- WebSocket for real-time updates

## 4. Directory Structure

```
FlowEngine/
├── PLAN.md                          # This file
├── README.md                        # Quick start guide
├── requirements.txt                 # Python dependencies
├── .env.example                     # Environment variables template
├── .gitignore
│
├── docs/                            # Knowledge base (from old project)
│   ├── FLOW_UI_REFERENCE.md         # Copy from old project
│   ├── FLOW_PIPELINE_KNOWLEDGE.md   # Copy from old project
│   └── FLOW_MULTILEVEL_JOBS.md      # Copy from old project
│
├── server/                          # API Server
│   ├── __init__.py
│   ├── app.py                       # FastAPI app + startup
│   ├── routes/
│   │   ├── __init__.py
│   │   ├── jobs.py                  # Job CRUD endpoints
│   │   ├── chains.py                # Multi-level chain endpoints
│   │   ├── profiles.py              # Profile management endpoints
│   │   ├── worker.py                # Worker claim/update endpoints
│   │   └── ws.py                    # WebSocket handler
│   ├── models/
│   │   ├── __init__.py
│   │   ├── job.py                   # Job data model (Pydantic)
│   │   └── profile.py               # Profile data model
│   ├── db/
│   │   ├── __init__.py
│   │   ├── database.py              # SQLite connection + migrations
│   │   ├── job_store.py             # Job CRUD operations
│   │   └── profile_store.py         # Profile CRUD operations
│   └── config.py                    # Server configuration
│
├── worker/                          # Engine Worker
│   ├── __init__.py
│   ├── main.py                      # Worker entry point + claim loop
│   ├── dispatcher.py                # Route job_type → handler
│   ├── profile_manager.py           # Chrome profile tracking
│   ├── project_lock.py              # Per-project serialization
│   └── remote_api.py                # HTTP client for server API
│
├── flow/                            # Flow Automation (Playwright)
│   ├── __init__.py
│   ├── client.py                    # FlowClient — browser lifecycle
│   ├── operations/
│   │   ├── __init__.py
│   │   ├── generate.py              # text-to-video
│   │   ├── extend.py                # extend-video
│   │   ├── insert.py                # insert-object
│   │   ├── remove.py                # remove-object
│   │   └── camera.py                # camera-move
│   ├── submit.py                    # Submit button + confirmation
│   ├── wait.py                      # Wait for completion
│   ├── download.py                  # Download + upscale pipeline
│   ├── model_selector.py            # LP model selection
│   ├── media_id.py                  # Media ID extraction + normalization
│   ├── navigation.py                # URL helpers, project navigation
│   └── account.py                   # Account check, credit verify
│
├── frontend/                        # Web UI
│   ├── index.html                   # Main SPA entry
│   ├── css/
│   │   └── style.css                # Styles (dark theme)
│   ├── js/
│   │   ├── app.js                   # App init + router
│   │   ├── api.js                   # API client
│   │   ├── ws.js                    # WebSocket client
│   │   ├── pages/
│   │   │   ├── dashboard.js         # Job dashboard
│   │   │   ├── create-job.js        # Job creator
│   │   │   ├── chain-builder.js     # Multi-level chain builder
│   │   │   └── profiles.js          # Profile manager
│   │   └── components/
│   │       ├── job-card.js           # Job card component
│   │       ├── chain-view.js         # Chain visualization
│   │       └── status-badge.js       # Status indicator
│   └── assets/
│       └── icons/                   # SVG icons
│
├── scripts/                         # Utility scripts
│   ├── start_server.cmd             # Windows: start server
│   ├── start_worker.cmd             # Windows: start worker
│   ├── start_all.cmd                # Windows: start everything
│   └── setup.cmd                    # Initial setup
│
├── docker/                          # Docker deployment
│   ├── Dockerfile.server
│   ├── Dockerfile.worker
│   └── docker-compose.yml
│
└── tests/                           # Test suite
    ├── test_job_store.py
    ├── test_chain_logic.py
    ├── test_profile_pinning.py
    └── test_api.py
```

## 5. Data Model

### 5.1 Job

```python
class Job:
    id: str                    # UUID
    type: JobType              # text-to-video | extend-video | insert-object | remove-object | camera-move
    status: JobStatus          # pending | claimed | running | completed | failed
    
    # Chain fields
    job_level: int             # 1 = standalone, 2+ = dependent
    parent_job_id: str | None  # Link to parent
    chain_id: str | None       # Group all jobs in same chain
    
    # Account binding (CRITICAL)
    profile: str | None        # Chrome profile name (= Google account)
    project_url: str | None    # Flow project URL
    media_id: str | None       # Flow media UUID
    edit_url: str | None       # Computed: project_url + /edit/ + media_id
    
    # Operation params
    prompt: str | None
    model: str | None          # e.g. "veo-3.1-fast-lp"
    aspect_ratio: str          # "16:9" | "9:16" | "1:1"
    bbox: dict | None          # {x, y, w, h} normalized 0-1
    direction: str | None      # Camera preset name
    
    # Output
    output_files: list[str]    # Downloaded video file paths
    generation_id: str | None  # Flow generation ID
    
    # Worker tracking
    worker_id: str | None      # Which worker claimed this job
    claimed_at: datetime | None
    completed_at: datetime | None
    error: str | None
    
    # Timestamps
    created_at: datetime
    updated_at: datetime
```

### 5.2 Profile

```python
class Profile:
    name: str                  # Chrome profile directory name
    google_account: str        # Google email
    locale: str                # "en" | "vi"
    tier: str                  # "ultra" | "free"
    status: str                # "available" | "busy" | "quarantined"
    current_job_id: str | None # Job currently running on this profile
    worker_id: str | None      # Which worker owns this profile
```

### 5.3 Chain

```python
class Chain:
    id: str                    # UUID
    profile: str               # PINNED profile for entire chain
    project_url: str | None    # Set after first job completes
    media_id: str | None       # Set after first job completes
    jobs: list[str]            # Ordered list of job IDs
    status: str                # pending | in_progress | completed | failed
```

## 6. API Endpoints

### Jobs
```
POST   /api/jobs                    # Create single job
POST   /api/chains                  # Create job chain (multi-level)
GET    /api/jobs                    # List jobs (filterable)
GET    /api/jobs/{id}               # Get job detail
GET    /api/jobs/{id}/children      # Get child jobs
DELETE /api/jobs/{id}               # Cancel job
```

### Worker
```
POST   /api/worker/claim            # Claim next available job (filtered by profile)
PUT    /api/worker/jobs/{id}        # Update job status + metadata
POST   /api/worker/heartbeat        # Worker keepalive
```

### Profiles
```
GET    /api/profiles                # List all profiles
PUT    /api/profiles/{name}         # Update profile status
GET    /api/profiles/{name}/jobs    # Jobs for this profile
```

### WebSocket
```
WS     /ws/jobs                     # Real-time job status updates
```

## 7. Key Algorithms

### 7.1 Job Claim (Profile-Aware)

```python
def claim_next_job(worker_id: str, available_profiles: list[str]) -> Job | None:
    """
    Claim the highest-priority unclaimed job that matches worker's profiles.
    
    Priority:
    1. Level-2+ jobs where parent.profile in available_profiles
    2. Level-1 jobs (any profile)
    
    Constraints:
    - Only one active job per project_url
    - Profile must not be busy with another job
    - Parent job must be completed before child can be claimed
    """
    # First: try Level-2+ jobs (profile-pinned)
    for job in pending_jobs_ordered_by_created:
        if job.job_level >= 2:
            parent = get_job(job.parent_job_id)
            if parent.status != "completed":
                continue  # Parent not done yet
            if parent.profile not in available_profiles:
                continue  # Wrong profile
            if is_project_locked(parent.project_url):
                continue  # Another job running on same project
            if not is_profile_available(parent.profile):
                continue  # Profile busy
            return claim(job, worker_id, parent.profile)
    
    # Then: Level-1 jobs (any available profile)
    for job in pending_jobs_ordered_by_created:
        if job.job_level == 1:
            profile = get_any_available_profile(available_profiles)
            if profile:
                return claim(job, worker_id, profile)
    
    return None
```

### 7.2 Job Completion (Store Everything)

```python
def on_job_completed(job_id: str, result: dict):
    """
    After any operation completes, store ALL metadata back.
    This is the fix for Bugs #1 and #2 from old engine.
    """
    update_job(job_id,
        status="completed",
        project_url=result["project_url"],     # ALWAYS store
        media_id=result["media_id"],           # ALWAYS store (from URL)
        edit_url=result["edit_url"],           # ALWAYS store
        profile=result["profile"],             # ALWAYS store
        output_files=result["output_files"],
        generation_id=result.get("generation_id"),
        completed_at=now()
    )
    
    # Update chain metadata
    if job.chain_id:
        update_chain(job.chain_id,
            project_url=result["project_url"],
            media_id=result["media_id"]
        )
    
    # Release project lock
    release_project_lock(result["project_url"])
    
    # Release profile
    set_profile_available(result["profile"])
    
    # Notify web UI via WebSocket
    broadcast_job_update(job_id)
```

### 7.3 Operation Execution Pattern

```python
async def execute_operation(client: FlowClient, job: Job) -> dict:
    """
    Common pattern for all Level-2 operations.
    Navigate by edit_url — NOT video_index.
    """
    # 1. Navigate directly to video
    edit_url = job.edit_url or f"{job.project_url}/edit/{job.media_id}"
    await client.page.goto(edit_url)
    await client.wait_for_video_loaded()
    
    # 2. Verify we're on the right video
    current_url = client.page.url
    assert job.media_id in current_url, "Wrong video!"
    
    # 3. Execute operation-specific steps
    # (overridden by each operation type)
    
    # 4. Wait for completion
    await wait_for_completion(client, timeout=300)
    
    # 5. Extract media_id from current URL (should be same)
    media_id = extract_media_id_from_url(client.page.url)
    
    # 6. Download result
    output_files = await download_video(client, media_id)
    
    # 7. Return ALL metadata
    return {
        "project_url": job.project_url,
        "media_id": media_id,
        "edit_url": client.page.url,
        "profile": client.profile_name,
        "output_files": output_files,
        "generation_id": extract_generation_id(client)
    }
```

## 8. Build Phases

### Phase 1: Foundation (Week 1)
- [ ] Project structure + git setup
- [ ] Server: FastAPI app + SQLite database + job CRUD
- [ ] Data models: Job, Profile, Chain
- [ ] Basic API endpoints: create job, list jobs, get job
- [ ] Worker: claim loop skeleton + remote API client
- [ ] Web UI: basic dashboard showing jobs list

### Phase 2: Single Job Operations (Week 2)
- [ ] Flow: FlowClient (Playwright browser launch)
- [ ] Flow: text-to-video operation (generate.py)
- [ ] Flow: submit + wait + download pipeline
- [ ] Flow: model selector (LP models)
- [ ] Worker: dispatch text-to-video jobs
- [ ] Server: worker claim/update endpoints
- [ ] Web UI: create t2v job form + status tracking

### Phase 3: Multi-Level Chain (Week 3)
- [ ] Flow: extend-video operation
- [ ] Flow: insert-object operation  
- [ ] Flow: remove-object operation
- [ ] Flow: camera-move operation
- [ ] Server: chain creation endpoint
- [ ] Server: profile-aware job claiming
- [ ] Server: project-level locking
- [ ] Worker: profile pinning + project lock
- [ ] Web UI: chain builder + chain status view

### Phase 4: Production Hardening (Week 4)
- [ ] Error handling + retry logic
- [ ] Account check + credit verification
- [ ] reCAPTCHA detection + handling
- [ ] Download fallback chain (API → UI → blob → HTTP)
- [ ] Worker heartbeat + stale job recovery
- [ ] Docker deployment (server + worker)
- [ ] Web UI: profile manager + settings
- [ ] Logging + monitoring

### Phase 5: Polish + Migration (Week 5)
- [ ] Migrate existing profiles from old engine
- [ ] Load testing with multiple workers
- [ ] Documentation update
- [ ] Production deployment

## 9. What to Reuse from Old Engine

### COPY as-is (knowledge docs):
- `docs/FLOW_UI_REFERENCE.md` — UI element reference
- `docs/FLOW_PIPELINE_KNOWLEDGE.md` — Pipeline technical reference  
- `docs/FLOW_MULTILEVEL_JOBS.md` — Multi-level job design + test results

### REFERENCE (read, rewrite cleaner):
- `modules/flow_generation_pipeline_steps.py` — Core pipeline logic (DOM selectors, button clicks)
- `modules/flow_submit_steps.py` — Submit confirmation detection
- `modules/flow_wait_steps.py` — Completion detection methods
- `modules/flow_download_steps.py` — Download + upscale pipeline
- `modules/flow_model_steps_v2.py` — Model selector logic
- `modules/flow_media_id_steps.py` — Media ID extraction
- `flow_client.py` — Browser launch + profile management

### DO NOT COPY:
- `app.py` — Monolithic, rebuild from scratch
- `engine_worker.py` — Redesign with profile pinning
- `web/index.html` — 9,615-line monolith, rebuild
- All tmp_*, test_*, auto_test_* files
- COORD files, debug outputs, log files

## 10. Tech Stack

| Component | Technology | Reason |
|---|---|---|
| Server | FastAPI (Python 3.11+) | Same as old, proven |
| Database | SQLite + aiosqlite | Simple, no separate process |
| Worker | Python + Playwright | Same as old, proven |
| Web UI | Vanilla JS + CSS | Simple, no build step, fast |
| Real-time | WebSocket (native) | Built into FastAPI |
| Deployment | Docker Compose | Server + worker containers |

## 11. Environment Variables

```env
# Server
SERVER_HOST=0.0.0.0
SERVER_PORT=8000
DATABASE_PATH=./data/flowengine.db
API_KEY=your-api-key

# Worker  
SERVER_URL=http://localhost:8000
WORKER_ID=worker-1
CHROME_USER_DATA_DIR=./chrome-profiles
WORKER_PROFILES=profile1,profile2,profile3
POLL_INTERVAL_SEC=5
MAX_CONCURRENT_JOBS=1

# Flow
FLOW_DOWNLOAD_QUALITY=1080p
FLOW_UPSCALE_MAX_WAIT_SEC=180
FLOW_GENERATION_TIMEOUT_SEC=900
FLOW_EXTEND_TIMEOUT_SEC=600
FLOW_EDIT_TIMEOUT_SEC=300
```

## 12. Migration Path

1. New engine runs alongside old engine (different port)
2. Same Chrome profiles can be used (one engine at a time per profile)
3. Web UI at new URL, old web UI stays active
4. Gradually move jobs to new engine
5. Once stable, retire old engine
