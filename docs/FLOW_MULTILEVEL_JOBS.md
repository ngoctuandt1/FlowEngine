# Flow Multi-Level Job System — Design & Known Issues

> Last updated: 2026-04-16
> Purpose: Complete reference for multi-level job chaining, data flow, and bugs to fix
> Source: code trace of app.py, jobs.py, generation.py, dispatcher.py + live browser testing

## 1. Concept: Multi-Level Jobs

A single "finished video" may require multiple sequential operations:

```
Level 1: text-to-video     → creates video from prompt
Level 2: extend-video      → extends the video with new scene
Level 2: insert-object     → adds object to the video
Level 2: remove-object     → removes object from the video
Level 2: camera-control    → applies camera motion preset
Level 2: (repeat as needed)
```

All Level-2 operations work on an EXISTING video within an EXISTING project.
They require: correct Google account + correct project URL + correct video target.

## 2. Data Model (jobs.py)

Each job stores these multi-level fields:

```python
{
    "id": "job-abc123",
    "type": "extend-video",           # canonical job type
    "job_level": 2,                    # 1 = standalone, 2 = depends on parent
    "job_category": "extend",          # sub-category (e.g., "camera-move" for camera jobs)
    "parent_job_id": "job-xyz789",     # link to parent job
    "project_url": "https://labs.google/fx/.../project/{uuid}",
    "media_ids": [],                   # accumulated media IDs (list)
    "generation_id": "",               # Flow generation ID
    "profile": "v25n30t3bh24h144",     # Google Chrome profile name
    "bbox": {"x": 0.3, "y": 0.1, "w": 0.4, "h": 0.5},  # normalized coords
    "video_index": 0,                  # which card in grid to target (0-based)
    # ... standard fields: prompt, model, status, etc.
}
```

### Helper functions in jobs.py:
- `get_parent_job(job_id)` → returns parent job dict
- `get_child_jobs(parent_job_id)` → returns all child jobs
- `update_job_media_ids(job_id, media_ids, generation_id)` → append media IDs

## 3. Job Creation API Endpoints (app.py)

### How project_url is resolved

All Level-2 endpoints accept 3 ways to specify the target:

```python
# Priority order (from _resolve_project_url_from_parent):
1. body["project_url"]      → use directly
2. body["video_media_id"]   → construct URL from media ID
3. body["parent_job_id"]    → look up parent job, inherit its project_url
# If none → 400 error
```

### Endpoint comparison

| Field | text-to-video | extend-video | insert-object | remove-object | camera-move |
|---|---|---|---|---|---|
| **Route** | `/api/text-to-video` | `/api/extend-video` | `/api/insert-object` | `/api/remove-object` | `/api/camera-move` |
| **Stored job_type** | text-to-video | extend-video | insert-object | remove-object | extend-video (!) |
| **job_category** | — | extend | insert-object | remove-object | camera-move |
| **Needs project_url** | NO | YES | YES | YES | YES |
| **prompt required** | YES | no | YES | NO | no (= direction) |
| **job_level** | 1 (default) | 2 if parent | 2 if parent | 2 if parent | 2 if parent |
| **bbox** | NO | NO | YES {x,y,w,h} | YES {x,y,w,h} | NO |
| **video_index** | NO | YES (default 0) | NO (!) | NO (!) | NO |
| **model** | YES | YES | NO | NO | NO |
| **direction** | NO | NO | NO | NO | YES |
| **Has local dispatch** | YES | YES | NO | NO | YES (broken) |

### Notable issues in endpoints:
1. **camera-move** stores `type="extend-video"` — differentiated only by `job_category`
2. **insert/remove** don't accept `video_index` from API — hardcoded to 0 in bg_* function
3. **insert/remove** have NO local dispatch — queue-only, need Docker worker

## 4. Engine Execution (generation.py → pipeline)

### How engine identifies which video to operate on

**ALL Level-2 operations use `video_index` (DOM card position) — NOT media_id.**

```python
# In _edit_object_common() and extend_video():
cards = page.locator("[data-tile-id]").all()
target_card = cards[min(video_index, len(cards) - 1)]
target_card.click()  # opens edit view
```

This is FRAGILE because:
- Card order in grid can change (new generations shift positions)
- If video_index=0 but another video was added first → wrong target
- No verification that clicked card matches intended media_id

### Dispatcher routing (modules/dispatcher.py)

```python
dispatch_job() routes by job_type:
  "text-to-video"    → bg_text_to_video()
  "extend-video"     → bg_extend_video()     # includes camera-move!
  "insert-object"    → bg_insert_object()
  "remove-object"    → bg_remove_object()
```

**camera-move is routed to bg_extend_video** because its type is "extend-video".
The `direction` field is stored on the job but bg_extend_video NEVER reads it → camera intent is LOST.

## 5. Data Flow Through a Chain

### What SHOULD happen:

```
Job A (t2v) completes:
  → MUST store: project_url, media_id, profile, generation_id
  
Job B (extend) receives from A:
  → NEEDS: project_url (to navigate), media_id (to target correct video), profile (same Google account)
  → Completes, MUST store: project_url, media_id (same), profile
  
Job C (insert) receives from B:
  → NEEDS: project_url, media_id, profile, bbox
  → Same chain continues...
```

### What ACTUALLY happens (current bugs):

```
Job A (t2v) completes:
  → STORES: project_url ✅, profile ✅, model ✅
  → MISSING: media_id ❌, generation_id ❌

Job B (extend) receives from A:
  → Gets project_url from parent ✅ (via _resolve_project_url_from_parent)
  → Uses video_index=0 instead of media_id ⚠️ (fragile)
  → Completes:
    → STORES: output ✅, profile ✅
    → MISSING: project_url ❌, media_id ❌, model ❌

Job C (insert) tries to get data from B:
  → parent_job_id=B, but B has no project_url stored → CHAIN BREAKS ❌
  → Must fall back to grandparent A (if explicitly coded)
  → No media_id anywhere in the chain ❌
```

## 6. CRITICAL BUGS — Must Fix

### BUG 1: media_id Never Stored on Jobs

**Severity**: HIGH
**Impact**: Cannot reliably target specific videos in multi-level chains

**Current state**: `flow_media_id_steps.py` collects media_ids during FlowClient runtime into `client._media_id_events`, but this data is NEVER written back to the job via `update_job()`.

**Fix needed**: After each operation completes, extract media_id from:
- URL: `/edit/{media_uuid}` → parse UUID from URL
- Network: `_media_id_events` collected during wait phase
- DOM: `<video>` element src URLs containing `?name={media_id}`

Then call: `jobs.update_job(job_id, status, media_id=extracted_id)`

### BUG 2: Level-2 Jobs Don't Store project_url Back

**Severity**: HIGH  
**Impact**: Chain breaks after 2nd level — Job C can't find project_url from Job B

**Current state**: `bg_extend_video`, `bg_insert_object`, `bg_remove_object` all call `update_job()` with only `output` and `profile`. They do NOT store `project_url` even though they HAVE it as a parameter.

**Fix needed**: Add `project_url=project_url` to every Level-2 `update_job()` call.

### BUG 3: video_index Instead of media_id for Targeting

**Severity**: MEDIUM
**Impact**: Wrong video targeted if project has multiple videos and grid order changes

**Current state**: Engine navigates to project grid, counts DOM cards, clicks card at position `video_index`.

**Better approach**: Navigate directly to `/edit/{media_id}` URL instead of going through grid.
This skips the fragile card-index step entirely.

### BUG 4: camera-move Direction Lost

**Severity**: HIGH
**Impact**: Camera presets never work — engine does normal extend instead

**Current state**: 
- `app.py` stores `direction` on job and `job_category="camera-move"`
- `dispatcher.py` routes to `bg_extend_video()` (because type="extend-video")
- `bg_extend_video()` never reads `direction` or `job_category`
- `bg_camera_move()` doesn't exist in generation.py

**Fix needed**: Either:
a) Create `bg_camera_move()` in generation.py and route to it, OR
b) Make `bg_extend_video()` check `job_category=="camera-move"` and handle differently

### BUG 5: No Profile Pinning for Job Chains

**Severity**: HIGH
**Impact**: Level-2 job runs on different Google account → project doesn't exist

**Current state**: Engine worker assigns profiles based on availability, not based on which profile the parent job used. A chain could be:
- Job A (t2v) runs on profile "alpha" → creates project on alpha's Google account
- Job B (extend) assigned to profile "beta" → navigates to project URL → 404 (project belongs to alpha)

**Fix needed**: Job chain MUST run on the same profile. When creating Level-2 job:
- Inherit `profile` from parent job
- Engine worker must respect profile pinning when claiming

### BUG 6: No Serialization for Same-Project Jobs

**Severity**: MEDIUM
**Impact**: Two workers operating on same project simultaneously → UI conflicts

**Current state**: Engine worker claims jobs without checking if another worker is already operating on the same project_url. Two extend jobs on the same project could run in parallel.

**Fix needed**: Project-level lock — only one worker per project_url at a time.

## 7. Correct Chain Implementation (Target Design)

```
Step 1: Job A (t2v) created
  → type: text-to-video
  → no parent, job_level=1

Step 2: Job A executes
  → Creates project, generates video
  → Stores: project_url, media_id (from URL), profile, generation_id

Step 3: Job B (extend) created  
  → type: extend-video
  → parent_job_id: A
  → Inherits: project_url from A, profile from A
  → job_level: 2

Step 4: Engine claims Job B
  → MUST use same profile as Job A
  → MUST NOT claim if another job on same project_url is active
  → Navigates directly to /edit/{media_id} (from parent A)
  → Clicks Extend, types prompt, submits
  → Stores: project_url, media_id (same), profile

Step 5: Job C (insert) created
  → parent_job_id: B (or A)
  → Inherits: project_url, media_id, profile
  → Gets bbox from user input

Step 6: Engine claims Job C
  → Same profile, same project lock
  → Navigate to /edit/{media_id}
  → Click Insert, draw bbox, type prompt, submit
  → Stores all metadata back

...and so on for any number of levels
```

## 8. Key Invariants for Multi-Level Jobs

1. **Same account**: All jobs in a chain MUST use the same Google profile
2. **Same project**: project_url is created at Level 1, inherited by all Level 2+
3. **Same media_id**: Operations update in-place — media_id does NOT change
4. **Sequential execution**: Jobs on same project MUST NOT run in parallel
5. **Navigate by media_id**: Use `/edit/{media_id}` URL, NOT grid card index
6. **Store everything**: Every job must store project_url + media_id + profile after completion
7. **History = version count**: Each operation adds 1 entry to history panel — can verify completion

## 9. Quick Reference: What Each Operation Needs

| Operation | project_url | media_id | profile | prompt | model | bbox | direction |
|---|---|---|---|---|---|---|---|
| text-to-video | creates new | creates new | any available | YES | YES | — | — |
| extend-video | from parent | from parent | SAME as parent | optional | YES (LP) | — | — |
| insert-object | from parent | from parent | SAME as parent | YES | — | YES (optional) | — |
| remove-object | from parent | from parent | SAME as parent | — | — | YES (required) | — |
| camera-control | from parent | from parent | SAME as parent | — | — | — | YES (preset name) |

## 10. Live Test Results — Multi-Level Module Test (2026-04-16)

Tested on English Chrome profile ("ngoctuandt2"), each operation as a SEPARATE module job — navigate away between each, use only metadata to come back.

### Test Data
- **project_url**: `https://labs.google/fx/tools/flow/project/5b2553ab-e048-48ab-acfd-62936219ceb6`
- **media_id**: `1eb6fea7-f1d4-4fcc-a25f-7ca3e06470be`
- **edit_url**: `https://labs.google/fx/tools/flow/project/5b2553ab-e048-48ab-acfd-62936219ceb6/edit/1eb6fea7-f1d4-4fcc-a25f-7ca3e06470be`

### Results

| Step | Operation | Navigate Away | Navigate Back | Result | History Count |
|---|---|---|---|---|---|
| Job 1 | text-to-video ("golden sunset ocean waves") | — | — | Video created | 1 |
| — | Navigate to homepage | YES | via edit_url | Landed on correct video | — |
| Job 2 | extend ("camera zooms out coastline") | — | — | Extended successfully | 2 |
| — | Navigate to homepage | YES | via edit_url | Landed on correct video | — |
| Job 3 | insert ("flock of seagulls", bbox upper-right sky) | — | — | Seagulls inserted | 3 |
| — | Navigate to homepage | YES | via edit_url | Landed on correct video | — |
| Job 4 | remove (bbox around seagulls) | — | — | Partial removal (AI quality) | 4 |

### Key Findings Confirmed
1. **Navigate by edit_url works 100%** — every time, lands on correct video with full history
2. **media_id stays SAME** across all 4 operations (confirmed in URL bar)
3. **History count increments correctly** (1 → 2 → 3 → 4)
4. **No state loss** between navigate-away-and-back cycles
5. **Each operation is truly independent** — only needs edit_url (= project_url + media_id) to resume
6. **Account binding verified** — all operations on same Google account, project_url only valid for that account

### Implications for Engine
- Engine worker MUST navigate to `/edit/{media_id}` directly — NOT grid card index
- Only metadata needed between jobs: `edit_url` (= `project_url` + `/edit/` + `media_id`) + `profile`
- This proves multi-level chains work as separate module operations
- Account binding is the CRITICAL constraint — see Section 11

## 11. Account Binding — The Core Constraint

> "vì sẽ có rất nhiều account cùng tham gia làm worker nên việc làm sao để luôn có thể làm đa tầng được, đó mới là key. nó gắn chặt với account. chứ k thể video của account này, lại đi làm tiếp đa tầng với acc khác"

### The Problem
With multiple Google accounts as workers:
- Account A creates a project → project_url only works for Account A
- Account B navigates to same project_url → **404 or access denied**
- Multi-level chain BREAKS if Level-2 job runs on different account than Level-1

### Design Requirements
1. **Job Level-1 (t2v) completes** → MUST store `profile` in job metadata
2. **Job Level-2 created** → MUST inherit `profile` from parent job
3. **Engine worker claims job** → MUST filter by profile:
   - Only claim jobs where `job.profile == worker.available_profiles`
   - If no worker has matching profile → job WAITS, never assigned to wrong account
4. **Profile = Google account identity** — one Chrome profile = one Google account
5. **A worker can have multiple profiles** — but each profile is one account

### Claim Algorithm (Target Design)
```python
def can_claim_job(worker, job):
    if job.job_level == 1:
        # Level 1: any available profile
        return worker.has_available_slot()
    else:
        # Level 2+: MUST match parent's profile
        parent = get_parent_job(job.parent_job_id)
        required_profile = parent.profile
        return (required_profile in worker.profiles 
                and worker.profile_available(required_profile)
                and not worker.has_active_job_on_project(job.project_url))
```

### What Happens Without Profile Pinning
```
Worker A (profile: alpha) creates video → project on alpha's Google account
Worker B (profile: beta) claims extend job → navigates to project URL → 404!
→ Job fails, user's chain is broken, must restart from scratch
```

## 12. Related Files

| File | Relevance |
|---|---|
| `app.py` lines 2855-3440 | Job creation endpoints |
| `modules/jobs.py` lines 700-1064 | Job data model, parent/child queries |
| `modules/dispatcher.py` lines 317-560 | Job routing + status relay |
| `modules/generation.py` lines 3216-3940 | bg_* execution functions |
| `modules/flow_generation_pipeline_steps.py` | Core pipeline (generate, extend, edit) |
| `modules/flow_media_id_steps.py` | Media ID extraction utilities |
| `modules/flow_download_steps.py` | Download orchestrator |
| `engine_worker.py` lines 1578-1650 | _run_job + claim loop |
| `docs/FLOW_UI_REFERENCE.md` | Complete UI element reference (VI+EN) |
| `docs/FLOW_PIPELINE_KNOWLEDGE.md` | Pipeline technical reference |
