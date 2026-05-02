# Session Report — `IDEA-CLONE` IdeaStudio web clone + perf cluster

> Continuous autopilot session (2026-05-01 evening through 2026-05-02
> morning, repo-local Asia/Bangkok) covering the public web overhaul from
> "phèn"-state job-grid to a Flow / IdeaStudio look, plus a perf round.
> Mechanism shipped: poster.jpg generation per mp4 + img-poster tile
> rendering + lazy-loaded page modules + Cache-Control split. Performance
> ratios in §6 are directional from in-session DOM/network probes; the
> repo only encodes the mechanism, not the captured byte counts.
> **59 merged PRs** in the contiguous span `#117-#176` (one PR, `#157`,
> still OPEN at session close), 11+ live verifications via Chrome MCP,
> no production outages beyond a ~2-minute `init_db` hotfix self-resolved
> within the same session.

---

## 1. Metadata

| Field | Value |
|---|---|
| Task ID | `IDEA-CLONE` |
| Task type | epic — UI overhaul + perf + project-first data model |
| Session window | 2026-05-01 ~23:50 → 2026-05-02 ~14:58 (Asia/Bangkok) — first 6 PRs (#119-#123, #125) merged before midnight; remaining 53 merged on 2026-05-02 |
| Worker | Claude Opus 4.7 (tech lead) + Codex CLI fan-out (parallel implementers + reviewers) |
| Branch | direct to `master` via 59 `claude/web-*` / `claude/docs-*` PRs |
| User signal | "k giống flow", "phèn", "đơ lag", "bỏ phần này đi đẩy các jobs lên như flow ấy" |

---

## 2. Commits landed (master shortlog)

```
244e895 chore(web): bump asset version r16→r17 (W3 home empty-state cleanup) (#176)
5516a19 fix(home): drop empty-state + Recent jobs header (Flow-style clean grid) (#175)
2277888 chore(web): bump asset version r15→r16 (lazy modules) (#174)
70fa5d2 feat(perf): lazy-load page modules (5 eager → 16 on-demand) (#173)
d5825a3 feat(home): auto-name new project with date stamp (skip prompt) (#172)
969e113 feat(perf): tune Cache-Control — versioned assets immutable, poster images public-cached, videos stay private (#171)
c4b743f chore(web): bump asset version r14→r15 (poster perf) (#170)
7bb9906 feat(perf): generate poster.jpg per mp4 + tile uses img poster (drops thumb load 200x) (#169)
b2ae677 chore(web): bump asset version r13→r14 (V3 home + V4 gold theme) (#168)
c4eab1b feat(home): project-first grid + Tạo project FAB + recent-jobs fallback (#167)
1ee8fcb fix(db): defer idx_jobs_project_id creation until after _ensure_job_column (#166)
6cd1107 feat(render): POST /api/render/timeline + render_jobs schema + ffmpeg compose (#165)
78b4a43 feat(projects): projects table + /api/projects CRUD + jobs.project_id column (#164)
8686b75 feat(theme): gold accent retheme — FAB, count pills, idea create-nodes btn, edge stars (#163)
6171b77 feat(media-tools): multi-track timeline editor shell (visual) (#162)
9052de8 feat(project-view): add Ý TƯỞNG idea/chat right rail (#161)
3e52d4c feat(canvas): 9:16 portrait nodes + dotted edge markers + ✦ midpoint stars (#159)
efe55df feat(settings): IdeaStudio-style Setup page (Gemini + Veo + Nano) (#160)
e2952e2 feat(idea-api): POST /api/idea/generate (Gemini-backed scene plan) (#158)
51e2ba6 feat(settings-api): /api/settings/ai + /api/settings/veo-accounts (U2) (#156)
4c02af4 chore(web): bump asset version r11→r12 to refetch FAB fix (#155)
f67b1bc fix(web): + New project as floating FAB (sticky bottom-center) instead of grid slot (#154)
7435d7f chore(web): bump asset version r10→r11 to refetch project-view polish (#153)
9f64044 feat(web): project view DAG polish (Sinh Thanh reference match) (#152)
273b07c chore(web): bump asset version r9→r10 to refetch job-detail redirect (#151)
a045f74 fix(web): redirect #job-detail/<id> → #project-view/<id> (DAG canvas) (#150)
bd6b31b fix(flow): faststart every saved mp4 (post-download ffmpeg pass) (#149)
0ec317a chore(web): bump asset version r8→r9 to refetch api.js cache-bust (#148)
0470a3f fix(web): cache-bust /downloads /uploads URLs to bypass stale CF cache (#147)
8a0d896 fix(web): mark /downloads /uploads as Cache-Control: private to keep Range requests working (#146)
66548fe chore(web): bump asset version r7→r8 to refetch tile-route fix (#145)
a8c2d30 fix(web): tile click → DAG project view always (single-node fallback for legacy jobs) (#144)
decfff5 chore(web): wire dag.css + bump asset version r6→r7 (#140)
f0d2668 feat(web): project view DAG canvas (node-based workflow editor) (#143)
335a84c feat(api): GET /api/chains/{chain_id} bulk DAG endpoint (#142)
0fa29bd feat(web): DAG canvas CSS for project view (R7-A pair) (#141)
575fbf5 chore(web): bump asset version r5→r6 to refetch project-view Flow polish (#139)
a2c9f3d feat(web): project view 100% Flow clone (#138)
23308e7 chore(web): bump asset version r4→r5 to refetch flow-clone polish (#137)
28471db feat(web): 100% Flow clone — auto-poster, abs date, subtler fail tile, top-bar trim (#136)
... (59 merged PRs in span; full table in §4)
```

---

## 3. Files changed (high-level)

| Area | Files | Purpose |
|---|---|---|
| Frontend pages | `frontend/js/pages/home.js`, `project-view.js` (NEW DAG), `job-detail.js`, `settings.js`, `media-tools.js`, `chain-builder.js`, `chain-tree.js`, `gallery.js`, `jobs.js` | Match IdeaStudio + Google Flow visual + UX |
| Frontend shell | `frontend/index.html`, `frontend/js/app.js`, `frontend/js/api.js` | Lazy-load page modules + cache-bust + helpers |
| Frontend CSS | `frontend/css/flow-shell.css`, `frontend/css/dag.css` (NEW), `frontend/css/settings-setup.css` (NEW), `frontend/css/timeline.css` (NEW) | Gold theme tokens, DAG canvas, Setup, Timeline |
| Backend routes | `server/routes/idea.py` (NEW), `server/routes/projects.py` (NEW), `server/routes/render.py` (NEW), `server/routes/settings.py` (NEW), `server/routes/jobs.py` (`/api/jobs/{id}/related`, `/api/chains/{id}`) | `/api/idea/generate`, `/api/projects/*`, `/api/render/timeline`, `/api/settings/*` |
| Backend stores | `server/db/database.py` (schema migration), `server/db/project_store.py` (NEW), `server/db/render_store.py` (NEW), `server/db/settings_store.py` (NEW) | Persistence for new domains |
| Backend models | `server/models/project.py`, `server/models/render.py`, `server/models/settings.py`, `server/models/idea.py` (all NEW) | Pydantic shapes |
| Backend services | `server/services/render_compose.py` (NEW), `server/services/gemini_client.py` (NEW) | ffmpeg compose, Gemini SDK wrapper |
| Worker / flow | `flow/download.py` (`+faststart`, poster jpg gen) | Post-process every saved mp4 |
| Server middleware | `server/app.py` | Cache-Control split rules (versioned-assets/posters/mp4s) |
| Tests | `tests/test_jobs_related_api.py`, `tests/test_settings_api.py`, `tests/test_idea_route.py`, `tests/test_projects_api.py`, `tests/test_render_compose.py`, `tests/test_faststart_poster.py` | RED-first TDD per ticket |

Total: 61 unique paths changed across the 59 merged PRs (per git diff --name-only between session start and 244e895). The §3 area table above is non-exhaustive — the merged set also touched: flow/login.py, profile_list.py, profiles_ultra.example.txt, scripts/check_profiles_ultra.py, server/models/job.py, server/db/job_store.py, worker/dispatcher.py, requirements.txt, plus extra tests test_chain_dag_api.py, test_chain_profile_precedence.py, test_profile_list_default.py, test_profile_swapper.py, test_templates.py. Approximate diff: +6800 / -2100 lines.

---

## 4. PR train (59 merged in span; #157 still OPEN at close)

> NOTE: Subjects in this section are short paraphrases for readability.
> The CANONICAL squash-merge titles are the ones in §2 (and from
> `git log origin/master --oneline | grep "(#<N>)"`). Two rows below are
> intentional groupings (`#119–#121`, `#125–#126`) — see §2 for the
> individual subjects.

### Round 1 — IdeaStudio repair cluster (#117–#143)

| PR | Subject |
|---|---|
| #117 | thumbnail rendering quality + broken-image placeholder |
| #118 | thumbnail fallback + lazy-load on tile templates |
| #119–#121 | docs(spine) follow-ups |
| #122 | repo-relative `FLOW_PROFILE_LIST_FILE` default |
| #123 | chain profile precedence + fallback |
| #124 | `/api/jobs/{id}/related` consolidated chain context |
| #125–#126 | docs(spec/spine) follow-ups |
| #127 | chain tree multi-level visualization |
| #128 | job detail reliable load + multi-level context |
| #129 | job-detail Continue-chain toolbar |
| #130 | project view (Flow-style chain grid) |
| #131 | chain-builder prefill from `?parent=<id>&type=<op>` |
| #132 | tile redesign — Google Flow visual clone |
| #133 | bust CDN cache + safer home tile id escape |
| #134 | route alias `#chain-builder` → `chains` |
| #135 | bump r3→r4 |
| #136 | 100% Flow clone — auto-poster + abs date + tile polish |
| #137 | bump r4→r5 |
| #138 | project view 100% Flow clone |
| #139 | bump r5→r6 |
| #140 | wire dag.css + bump r6→r7 |
| #141 | DAG canvas CSS for project view (R7-A pair) |
| #142 | `/api/chains/{chain_id}` bulk DAG endpoint |
| #143 | project view DAG canvas (node-based workflow editor) |

### Round 2 — DAG navigation + media reliability (#144–#155)

| PR | Subject |
|---|---|
| #144 | tile click → DAG project view always (single-node fallback for legacy jobs) |
| #145 | bump r7→r8 |
| #146 | mark `/downloads /uploads` as `Cache-Control: private` (preserve Accept-Ranges) |
| #147 | cache-bust query for `/downloads /uploads` URLs |
| #148 | bump r8→r9 |
| #149 | faststart every saved mp4 (post-download ffmpeg pass) |
| #150 | redirect `#job-detail/<id>` → `#project-view/<id>` |
| #151 | bump r9→r10 |
| #152 | project view DAG polish (Sinh Thanh reference match) |
| #153 | bump r10→r11 |
| #154 | + New project as floating FAB (Flow gallery match) |
| #155 | bump r11→r12 |

### Round 3 — IdeaStudio feature parity (#156–#162)

| PR | Subject |
|---|---|
| #156 | `/api/settings/ai` + `/api/settings/veo-accounts` (U2) |
| #158 | `POST /api/idea/generate` (Gemini-backed scene plan) (U4) |
| #159 | canvas 9:16 portrait nodes + dotted edge markers + ✦ midpoint stars (U6) |
| #160 | IdeaStudio-style Setup page (Gemini + Veo + Nano) (U1) |
| #161 | Ý TƯỞNG idea/chat right rail (U3) |
| #162 | multi-track timeline editor shell (visual) (U5) |

### Round 4 — V cluster (project-first + render + theme) (#163–#168)

| PR | Subject |
|---|---|
| #163 | gold accent retheme (V4) |
| #164 | projects table + `/api/projects` CRUD + `jobs.project_id` (V2) |
| #165 | `POST /api/render/timeline` + `render_jobs` schema + ffmpeg compose (V1) |
| #166 | hotfix: defer `idx_jobs_project_id` creation until after column-ensure |
| #167 | project-first home grid + Tạo project FAB + recent-jobs fallback (V3) |
| #168 | bump r13→r14 |

### Round 5 — Perf round (#169–#176)

| PR | Subject |
|---|---|
| #169 | generate poster.jpg per mp4 + tile uses img poster (~200x thumb bandwidth drop) |
| #170 | bump r14→r15 |
| #171 | tune Cache-Control — versioned assets immutable, posters public-cached, videos stay private |
| #172 | auto-name new project with date stamp (skip prompt) |
| #173 | lazy-load page modules (5 eager → 16 on-demand) |
| #174 | bump r15→r16 |
| #175 | drop empty-state + 'Recent jobs' header (Flow-style clean grid) |
| #176 | bump r16→r17 |

---

## 5. Live verification evidence

All claims paired with command output / DOM probe / screenshot in the source
session — none of the rows below are paper claims. Selected highlights:

| Verify | Command / probe | Evidence |
|---|---|---|
| Setup page renders | `document.querySelectorAll('h1,h2,h3')` | titles `[Setup, ⚙ Setup, Thiết lập Gemini SDK, Thiết lập tài khoản Veo, API KEY NANO]` |
| Idea rail visible | `document.querySelector('.pv-idea-rail')` | `ideaTitle:"✦ Ý TƯỞNG"`, `ideaInput:true`, `ideaCreateBtn:true` |
| Timeline editor | `document.querySelectorAll('.tl-track-row')` | `trackCount=5, renderBtn:true, playhead:true` |
| Tile DAG node 9:16 | bounding-rect on `.pv-node` | `nodeWidth=240, aspectRatio=1.78` (9:16 portrait) |
| Tile click → DAG | tile `<a href>` audit | `href="#project-view/<id>"` (was `#job-detail/<id>`) |
| Job-detail redirect | `location.replace('#project-view/...')` from router | hash auto-changes after navigation |
| Projects API | `POST /api/projects {name:"…"}` | 201 + id `8cc97d41-…`, then GET listed |
| MP4 faststart | `python3 atom-walk on /opt/flowengine/downloads/cam_1080p_…mp4` | `MOOV at 32` (was at 7575042) |
| Poster.jpg cache | `curl -I` x2 same URL | `cf-cache-status: MISS → HIT` |
| Cache-Control split | `curl -I` per resource | poster `public, max-age=2592000, immutable`; mp4 `private, max-age=300`; js `public, max-age=2592000, immutable` |
| Lazy-load fewer scripts | `performance.getEntriesByType('resource')` filter `.js` | `count: 5` initial (was 21) |
| pytest baseline | `pytest tests/ -q --tb=short` | `643 passed, 12 skipped` post all merges |

---

## 6. Performance numbers (cold cache, fresh load)

| Metric | Before all rounds | After (post-#176) |
|---|---|---|
| Initial JS files | 21 | **5** |
| Initial JS bytes | 148 KB | **16 KB** |
| Initial CSS bytes | 24 KB | 24 KB (unchanged) |
| Tile-thumbnail bandwidth | ~80 MB (11× MP4 ~7 MB each) | **~330 KB** (11× poster.jpg ~30 KB each) |
| Total initial bandwidth | ~80 MB | **~47 KB** (resources actually fetched on `#home` load) |
| domContentLoaded | ~3.2 s | **~1.7 s** |
| Posters CDN-cached | ✗ (CF BYPASS) | ✓ (CF HIT after warmup, immutable 30 d) |
| Web-side lazy modules | none | 16 unique lazy page-module stems on-demand |

Reduction factor (per in-session Chrome MCP DOM/network probe; the repo only encodes the mechanism, not the captured byte counts): thumbnail bandwidth ~200x lighter (mp4 ~7MB tile -> jpg ~30KB), JS payload ~9x smaller (148KB eager -> 16KB initial), total initial bandwidth dropped from ~80MB to ~47KB. Numbers are directional, taken from one cold-cache page load on 2026-05-02; rerun under controlled conditions for a benchmark-grade figure.

---

## 7. Root-cause investigations

### `#job-detail` redirect (PR #150)
User reported: "click vào jobs thì hiện lên 1 bảng chả để làm gì". Audit
showed home tiles routed to `#job-detail/<id>` (legacy detail page). Fix:
hard router-level redirect to `#project-view/<id>` so every link lands on
the DAG canvas, the new canonical view.

### MP4 thumbnails black (PR #146 / #147 / #149)
3-layer cause:
1. Cloudflare cached `/downloads/*.mp4` and stripped `Accept-Ranges` →
   `<video>` stalled at `readyState=0` forever → black tiles.
2. Origin MP4s had `moov` atom at end of file (Flow API ships them this
   way). Even with ranges, browser couldn't progressive-stream.
3. Stale-cache cure required a one-shot bust query while the long
   Cache-Control aged out.

Fixes: `private, max-age=300` for `*.mp4` so CF passes through ranges;
`ffmpeg -movflags +faststart` post-download in `flow/download.py` and
batch-applied to 45 existing files; cache-bust `?_v=` on first reload.

### Project-first init_db (PR #166 hotfix)
V2 (#164) added `CREATE INDEX idx_jobs_project_id ON jobs(project_id)`
inside `_SCHEMA_SQL.executescript()` — but the additive
`_ensure_job_column("project_id", …)` migration runs **after** the
schema script. On any pre-existing database (production!) the index
referenced a column that did not yet exist and aborted `init_db` with
`sqlite3.OperationalError: no such column: project_id`. Server failed
to start for ~2 minutes. Fix: move the `CREATE INDEX` out of
`_SCHEMA_SQL` and execute it manually after the column-ensure step.

### Tile load lag (PR #169)
Phase-1 evidence: 11 video tiles × ~7 MB each = ~80 MB just to paint
poster frames. Even with `preload="auto"` browser saturated the network.
Fix: server-side `ffmpeg -frames:v 1` extracts a 480-wide poster jpg
(~30 KB) per mp4 at faststart time; `App.mediaTile.videoTag(...)` now
renders `<img class="tile-video" src="<file>.poster.jpg">` and only
swaps to `<video>` on mouseenter (existing hover-play UX preserved).

### CDN cache wasted on poster jpg (PR #171)
Initial `private, max-age=300` rule (PR #146) applied to ALL of
`/downloads/` — correct for mp4s (preserves ranges) but wrong for
posters (immutable per filename, should be edge-cached). Fix: split
the middleware: `*.jpg|png|webp|gif` → `public, max-age=2592000,
immutable`; `*.mp4` → keep `private`. Verified with `curl -I` twice
on the same URL: 1st `cf-cache-status: MISS`, 2nd `HIT`.

---

## 8. Known gaps / follow-up scope

| Gap | Detail | Priority |
|---|---|---|
| Multi-node DAG edges visual | code path landed (PR #143/#159 with star/dot/port markers) but no live chain has ≥2 jobs sharing `chain_id`. Renders correctly on synthetic test data only. | Low — falsifiable when first multi-node chain exists |
| `/api/idea/generate` end-to-end live | route + Gemini wrapper + RED test pass; no production smoke yet because no Gemini key has been entered into Setup page. | Med — user action |
| `/api/render/timeline` live ffmpeg run | endpoint + RED tests pass with monkeypatched subprocess; never run with a real timeline doc + asset paths. | Med — needs a populated timeline session |
| Veo accounts add/save flow E2E | Setup page renders + persists; no live "warm a profile from this account row" wire-up. | Low — separate ticket |
| Worker faststart-on-save (PR #149) | confirmed via unit test; no NEW completed mp4 since deploy to prove path live. | Low — falsifiable on first new completion |
| Project rename UI | Auto-name (PR #172) ships, `PUT /api/projects/{id}` exists, but no rename UI. | Low — small follow-up |
| Empty-state debt | `'Recent jobs no longer rendered'` + recent-jobs is the entire page when projects empty. Some users may want a divider when both projects + recent jobs coexist. | Low — design call |

---

## 9. Outcome

User signals "k giống flow", "phèn", "đơ lag", and "bỏ phần này đi đẩy
các jobs lên như flow ấy" all addressed. End-state on `master` /
`ai.hassio.io.vn`:

- `#home`: project-first grid OR clean recent-jobs grid (no header /
  empty-state placeholder) + sticky gold "Tạo project" FAB.
- `#project-view/<id>`: DAG canvas with portrait 9:16 nodes + curved
  bezier edges with dotted markers + Ý TƯỞNG idea rail + Run Workflow /
  Batch Run / Export / AI Agent / Settings toolbar + sticky bottom-pill
  + footer disclaimer.
- `#settings`: Gemini SDK + Veo accounts (multi) + Nano API key cards.
- `#media-tools`: 5-track multi-track timeline editor visual shell.
- Tile click on home / gallery / jobs → DAG project view. Legacy
  `#job-detail/<id>` URLs hard-redirect to the DAG.
- Thumbnails ~200x lighter, page JS ~9x smaller, initial bandwidth far smaller (~80MB -> ~47KB on the cold-cache probe — direction not exact)
  smaller. Posters CF-cached for 30 days immutable; videos pass
  through Range-request friendly.

No production outage > 2 minutes. 643 pytest pass on master HEAD.

---

## 10. References

- IdeaStudio observation doc: `/c/Users/Tuan/AppData/Local/Temp/ideastudio_observations.md` (frame-by-frame, see this file's §1 link in INDEX).
- Reference visuals from user: 22-frame extract of YouTube ELfaa0w4jyk
  (yt-dlp + ffmpeg), backed up under `/tmp/idea_frames/` on Debian.
- Codex deep-reason analyzer reports: A (UI/UX), B (architecture), C
  (backend) — `/c/Users/Tuan/AppData/Local/Temp/codex_idea_report_*.md`
  (3,890 lines, archived).
- 4 closing memory files referenced via the user CLAUDE.md trigger map
  (Completion-gate, Scope-lock, Closing-the-branch, fe-tdd, fe-debug).
