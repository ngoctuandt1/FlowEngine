# FlowEngine — Supervisor Handoff (2026-04-20)

> **Scope.** Session-to-session handoff for the *supervisor* Claude session that
> coordinates FlowEngine work. Distinct from `docs/HANDOFF.md` (which is a
> specific Run 19 L2-unblock playbook). Read this first when opening a fresh
> supervisor session on this repo.
>
> **Last updated:** 2026-04-20 after commit `58f47e7`.

---

## 1. What the project is

Browser-automation engine for Google Flow (`labs.google/fx/tools/flow`, Veo 3.1).
Python + Playwright + FastAPI + SQLite + vanilla-JS SPA. Creates and chains
video operations (text-to-video, extend, insert-object, remove-object,
camera-move) across multiple Google accounts in parallel. Worker claims jobs
from server, drives Chrome via Playwright, reports results back.

Repo: `D:/AI/FlowEngine` — branch `master`. Latest hash verified with `git log -1`.

---

## 2. Read first (authoritative, in order)

1. `CLAUDE.md` — top-of-repo project context + code-quality directive
2. `C:/Users/Tuan/.claude/projects/D--AI-FlowEngine/memory/MEMORY.md` — 11-entry index; **memory supersedes any doc when they conflict**
3. `docs/SPEC.md` §D.4 — B1-B37 ledger (B38 entry pending), invariants INV-1 through INV-5
4. `docs/FLOW_ENGINEERING_NOTES.md` — 497-line consolidated supervisor reference (§15 = in-flight state)
5. `docs/HANDOFF.md` — Run 19 L2-unblock playbook (Step 1 full-reset policy 2026-04-20)
6. `docs/E2E_RESULTS_PHASE_A.md` — append-only Run log (most-recent-first, Runs 1-15)
7. `docs/session-reports/2026-04-19_Tier2_Run19_L2_chain_blocked.md` — current blocker diagnosis
8. `docs/FLOW_BUTTON_EXACT.md` + `FLOW_UI_REFERENCE.md` + `FLOW_MULTILEVEL_JOBS.md` — selector catalog + UI ref + chain history

Worst-case catch-up from these ≈ 15 min.

---

## 3. Architecture (30-second)

```
frontend (vanilla JS SPA) ↔ server (FastAPI + SQLite, port 8080) ↔ worker (claim loop + Playwright) ↔ Chrome profiles
```

- `server/app.py` — FastAPI app, routes under `server/routes/`
- `worker/main.py` — claim loop; `worker/dispatcher.py` routes job type → handler
- `flow/client.py` — FlowClient Playwright wrapper; captures network via `_record_media_id` / `_video_urls`
- `flow/operations/{generate,extend,insert,remove,camera}.py` — one per job type
- `flow/{submit,wait,download,upscale,media_id,navigation,model_selector,login}.py` — primitives
- 5 invariants — INV-1 account binding, INV-2 edit_url nav, INV-3 store everything, INV-4 serial per project, INV-5 media_id context-dependent per op

---

## 4. Bug ledger status

B1-B37 FIXED + live-verified. B38 (UI 1080p upscale) implemented but
uncommitted — gated on L2 live validation. See `FLOW_ENGINEERING_NOTES.md §8`
for per-bug hashes + one-liners.

Recent runs:

| Run | Date | Result |
|---|---|---|
| 13 | 2026-04-19 | ✅ B35 force x1 — ngoctuandt20 baseline |
| 14 | 2026-04-19 | ⚠️ B37 harvest bug surfaced (fixed same session) |
| 15 | 2026-04-19 | ✅ 3/3 t2v — B37 + B35 verified across prompts |
| 17/18 | 2026-04-19 | ✅ B38 UI 1080p upscale at L1 |
| 19 | 2026-04-19 | ❌ L2 extend BLOCKED by profile-auth (marketing landing) |

---

## 5. Uncommitted state (gated on L2)

Main tree master has 4 `.py` files modified from Run 17/19 parallel session,
**NOT committed** per HANDOFF rule:

| File | Purpose |
|---|---|
| `flow/upscale.py` (new, 405 lines) | B38 UI 1080p primary path |
| `flow/download.py` | B38 integration — `quality=="1080p"` routes UI first, else 720p API |
| `flow/wait.py` | POLICY regex tightened + screenshot/HTML dump on DOM error |
| `flow/login.py` | Stuck-detection + `page.reload` per memory `feedback_login_stuck_reload.md` |

Commit ONLY after L2 extend completes end-to-end. Recommended split — 3 logical commits:

1. `fix(download): UI-driven 1080p upscale primary path (B38, P0)` — `flow/upscale.py` + `flow/download.py`
2. `fix(wait): tighten POLICY regex; dump screenshot+HTML on DOM error` — `flow/wait.py`
3. `fix(login): reload page after 3 stuck iterations` — `flow/login.py`

---

## 6. Open blockers (prioritized)

### P0 — L2 chain blocked

Worker profile `ngoctuandt20` not authenticated. Full reset was done (backup
preserved at `chrome-profiles/ngoctuandt20.bak-*`).

Next action — follow `docs/HANDOFF.md` Steps 1-7, with STEP 3 updated to
auto-login (commit `58f47e7`):

1. `powershell -NoProfile -File scripts/kill_engine_chrome.ps1` (selective kill, leaves user's personal Chrome alive)
2. `rm -rf chrome-profiles/ngoctuandt20/` (already done — backup preserved)
3. `python scripts/warm_profile.py ngoctuandt20` — auto-login via `flow.login.handle_login_redirect`, no user interaction
4. Restart server + worker
5. Query DB for full UUIDs: `sqlite3 data/flowengine.db "SELECT id, parent_job_id, chain_id, project_url, media_id FROM jobs WHERE chain_id LIKE '1871a218%' ORDER BY created_at DESC LIMIT 5"`
6. POST new L2 extend under chain `1871a218` / parent `4a032d83` / media `df78a409`
7. Monitor for `extend_1080p_*.mp4` (≥2 MB, `ffprobe` confirms 1920×1080)

### P2 — B38 session report missing

`docs/session-reports/2026-04-19_Tier2_Run17_B38_UI_upscale.md` doesn't exist
despite `docs/HANDOFF.md` referencing it. Parallel session never wrote it.
Backfill when someone validates the code path.

### P3 — 1080p upscale API endpoint permanently 404

B38 finding supersedes B34b retry bump. `FLOW_UPSCALE_MAX_RETRIES` is now dead
code for the 1080p path (only useful for 720p transient recovery). Not a bug
— documented tech debt.

---

## 7. Rules of engagement (NON-NEGOTIABLE)

Each is backed by a memory file + (where applicable) trip-wire test. See memory
`feedback_locked_items_require_user_approval.md` — "**cái gì đã cố định vào
rồi, thì k được tự ý sửa, trừ khi tao bảo**".

| # | Rule | Memory | Trip-wire |
|---|---|---|---|
| 1 | Flow accounts MUST be EN locale | `feedback_english_locale.md` | — |
| 2 | Every L1 t2v MUST force x1 (B35) | `feedback_output_count_x1.md` | `tests/test_output_count.py` |
| 3 | `/edit/` nav via `tile.click`, NEVER `page.goto` | `feedback_flow_edit_nav_click.md` | — |
| 4 | `warm_profile.py` = `mail.google.com` entry + auto-login via `flow.login` | `feedback_warm_profile_manual_gmail.md` | `tests/test_warm_profile.py` (4 cases) |
| 5 | Never blanket `taskkill //F //IM chrome.exe` — use `scripts/kill_engine_chrome.ps1` | `feedback_chrome_kill_selective.md` | — |
| 6 | Warm Chrome crash → FULL DELETE profile dir, skip cache-preserve bisect | `feedback_profile_full_reset.md` | — |
| 7 | Login overlay stuck → `page.reload` after 3 iterations | `feedback_login_stuck_reload.md` | — |
| 8 | Supervisor hands out prompts for parallel sessions, doesn't execute heavy work inline | `feedback_prompt_delivery_workflow.md` | — |
| 9 | Cross-check memory BEFORE pasting HANDOFF/playbook into a new-session prompt | `feedback_cross_check_memory_before_paste.md` | — |
| 10 | Code passes senior-reviewer bar — user runs codex on every change | `feedback_code_quality_codex_review.md` | — |
| 11 | Locked items (memory or trip-wire) cannot be changed without explicit user approval this turn | `feedback_locked_items_require_user_approval.md` | — |

**Additional locked code contracts** (memory indirect — trip-wires only):

| Contract | Trip-wire |
|---|---|
| B37 harvest key `evt["mid"]` + `_video_urls` list-of-dicts unwrap | `tests/test_download.py` |
| B34/B34b upscale poll window ≥ 300 s | `tests/test_download.py` |
| B35 `text_to_video` must call `_set_output_count` | `tests/test_output_count.py` |
| Bbox/camera/aspect/submit/model-selector selector contracts | various `tests/test_*.py` |

---

## 8. Commit style

- `fix(scope): one-liner (Bn, priority)` for bug fixes with bug number
- `feat(scope): ...` for new features
- `docs(scope): ...` for docs-only
- `ops(scope): ...` for ops/scripts
- `refactor(scope): ...` for no-behavior refactors
- Body explains WHY not WHAT. Cite memory file names when the fix reflects a locked rule.
- Every commit ends with:
  ```
  Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
  ```
- `git add <explicit files>` — NEVER `git add -A` (parallel sessions may have their own uncommitted work).

---

## 9. Testing

```
python -W error::DeprecationWarning -m pytest tests/ -q
```

- Must be green.
- Current: **123 passed**. After PR#18 (frontend-optim, pending merge): +2 = 125.
- Source trip-wires lock contracts via `inspect.getsource()` / `Path.read_text()`. Standard pattern for new locked items.
- Adding more locks ≡ adding memory + trip-wire TOGETHER (2-layer defense per 2026-04-20 user rule).

---

## 10. Immediate next action — pick one

**A) Finish L2 unblock (continues Run 19)**
- Follow `docs/HANDOFF.md` Steps 1-7 (already updated with full-reset + selective-kill + STEP 3 auto-login)
- `warm_profile.py` auto-drives sign-in — no manual Gmail step needed
- Post new L2 extend, monitor `extend_1080p_*.mp4`
- On pass: commit the 4 in-flight `.py` files as 3 logical commits + Run 20 session report + update `E2E_RESULTS`

**B) Backfill B38 Run 17/18 session report** — docs-only, unblocks tag bump to `v0.7.0`.

**C) Review PR#18 (frontend-optim)** — incremental WS dashboard + shared form constants + bulk-delete endpoint; 4 commits, 121/121 tests. Merge decision if OK.

**D) Something the user names directly.**

---

## 11. Meta

- Main session is a **supervisor** — produces prompts, reviews diffs, syncs docs. Does NOT run heavy work (live Tier 2 + LP credits + long refactors) inline unless user says "chạy ngay" / "execute".
- Claude-in-Chrome MCP is available for read-only DOM probes if a gap surfaces.
- Ghost Chrome cleanup uses `scripts/kill_engine_chrome.ps1` (filters `--user-data-dir` against owned paths; leaves user's personal Chrome alone).
- Current memory count: **11**. Trip-wire tests for 5 locked domains.
- User is Vietnamese-speaking but fluent in English prompt text. Respond in Vietnamese when user's message is Vietnamese; mixing is OK.
- User runs **codex** agent (parallel) for code review. Code quality bar is high. Ask before touching anything locked.
- When in doubt: grep `memory/` + `docs/` first; ask only when artifacts don't answer.

---

## 12. Session-close checklist (when closing the supervisor session)

- [ ] All docs updates committed (or explicitly listed in `FLOW_ENGINEERING_NOTES.md §15 In-Flight` if intentionally uncommitted)
- [ ] No background server / worker / Chrome processes leaking — run `scripts/kill_engine_chrome.ps1` + `taskkill //F //IM python.exe` if needed
- [ ] `MEMORY.md` index reflects all `feedback_*.md` files in the memory directory
- [ ] This file (`SUPERVISOR_HANDOFF.md`) reflects the latest state if the session introduced new blockers, new locks, or new in-flight work
- [ ] No unapproved changes committed to locked files (check `tests/test_warm_profile.py` / `test_output_count.py` / `test_download.py` pass)

Good luck.
