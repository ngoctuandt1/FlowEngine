# FlowEngine — Supervisor Handoff (2026-04-20)

> **Scope.** Session-to-session handoff for the *supervisor* Claude session that
> coordinates FlowEngine work. Distinct from `docs/HANDOFF.md` (which is a
> specific Run 19 L2-unblock playbook). Read this first when opening a fresh
> supervisor session on this repo.
>
> **Last updated:** 2026-04-20 after CDP-fingerprint analysis + new doc
> `docs/CHROME_LAUNCH_SECURITY.md`.

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
2. `C:/Users/Tuan/.claude/projects/D--AI-FlowEngine/memory/MEMORY.md` — 12-entry index; **memory supersedes any doc when they conflict**
3. `docs/SPEC.md` §D.4 — B1-B37 ledger (B38 entry pending), invariants INV-1 through INV-5
4. `docs/FLOW_ENGINEERING_NOTES.md` — 497-line consolidated supervisor reference (§15 = in-flight state)
5. `docs/HANDOFF.md` — Run 19 L2-unblock playbook (Step 1 full-reset policy 2026-04-20)
6. `docs/CHROME_LAUNCH_SECURITY.md` — launch-path architecture + Google detection signals + H1/H2 Run 19 hypothesis (read before touching any launch code)
7. `docs/E2E_RESULTS_PHASE_A.md` — append-only Run log (most-recent-first, Runs 1-15)
8. `docs/session-reports/2026-04-19_Tier2_Run19_L2_chain_blocked.md` — current blocker diagnosis
9. `docs/FLOW_BUTTON_EXACT.md` + `FLOW_UI_REFERENCE.md` + `FLOW_MULTILEVEL_JOBS.md` — selector catalog + UI ref + chain history

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

### P0 — L2 chain RESOLVED (Run 20 passed, 2026-04-20)

L2 extend completed end-to-end (20 MB, 1920×1080) in Run 20 — see
`docs/session-reports/2026-04-20_Tier2_Run20_L2_extend_H1_confirmed.md`.

- **H1 confirmed** as the primary cause: temp-clone user-data-dir was
  dropping cookies; worker on base profile reads the same dir the warm
  session writes.
- **Landing recovery** (`flow/landing.py`, commit `d856bf6`) is the
  second required component — Flow marketing variant still renders
  mid-run even on base profile; the recovery hook clicks the CTA back
  into the editor.
- **H2 not dominant.** Option 2 (pipe) / Option 3 (stealth) from
  `docs/CHROME_LAUNCH_SECURITY.md` §4 remain unused.

**Established worker configuration (default going forward):**

```
FLOW_USE_BASE_PROFILE=1 WORKER_PROFILES=<profile> python run_worker.py
```

`project_lock.py` serializes same-project chains; cross-project
parallelism on the same profile would still need a policy decision.

5 commits landed from Run 20: `a5009af` (B38 UI 1080p), `d856bf6`
(landing recovery), `1154ffc` (wait+login), `a4c4888` (warm_profile
Workspace bridge), `849834e` (claim direct-parent inheritance —
supersedes B30/B32). Tests 123 → 144.

---

### Historical — Run 20 plan (kept for context; superseded by outcome above)

Run 19 landed worker on Flow's marketing page instead of the editor SPA.
Two hypotheses — Run 20 discriminated H1.

- **H1 (confirmed):** cookies did not survive warm → worker transition.
- **H2 (analysis, not confirmed):** Google fingerprints the CDP-attached
  Chrome + temp-cloned user-data-dir. Full analysis + detection signals:
  `docs/CHROME_LAUNCH_SECURITY.md` §2 + §5.

Run 20 plan (H1 vs H2 discriminator):

1. `powershell -NoProfile -File scripts/kill_engine_chrome.ps1` — selective kill; leaves user's personal Chrome alive.
2. Selective python kill by port 8080 + `run_worker.py` cmdline match; do NOT blanket `taskkill //F //IM python.exe`.
3. Reset stuck-claimed job `7c41a24a-182b-4fc8-920d-0fa1e9883cc2` in DB (otherwise `project_lock` blocks the new L2 on the same `project_url`).
4. `python scripts/warm_profile.py ngoctuandt20` — auto-login via `flow.login.handle_login_redirect` (no user interaction).
5. Start server; start worker with **`FLOW_USE_BASE_PROFILE=1 WORKER_PROFILES=ngoctuandt20 python run_worker.py`** — this skips the temp-clone so the worker reads the same `chrome-profiles/ngoctuandt20/` the warm session wrote. Removes the cloned-user-data-dir signal. `project_lock` already serializes per project.
6. Query DB for full UUIDs: `sqlite3 data/flowengine.db "SELECT id, parent_job_id, chain_id, project_url, media_id FROM jobs WHERE chain_id LIKE '1871a218%' ORDER BY created_at DESC LIMIT 5"`.
7. POST new L2 extend under chain `1871a218` / parent `4a032d83` / media `df78a409`.
8. Monitor for `extend_1080p_*.mp4` (≥2 MB, `ffprobe` confirms 1920×1080).

Outcomes:

| Result | Conclusion | Next |
|---|---|---|
| Editor loads, L2 completes | H1 confirmed (temp-clone dropped cookies) | Commit the 4 in-flight `.py` + `FLOW_USE_BASE_PROFILE=1` default in worker env docs |
| Marketing landing again | H1 partial; H2 in play | Escalate to `CHROME_LAUNCH_SECURITY.md` §4 Option 2 (pipe) or Option 3 (stealth) — user decides |
| New failure mode | Re-diagnose from logs | Session report; do not speculate in handoff |

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
| 12 | Chrome launch must mimic a real user — Mode A only (subprocess + `connect_over_cdp`); no `--disable-blink-features=AutomationControlled` or other bot-hider flags; stealth/pipe-mode need user approval | `feedback_chrome_launch_real_user.md` + `docs/CHROME_LAUNCH_SECURITY.md` | — |

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

**A) Finish L2 unblock (continues Run 19 — H1/H2 discriminator)**
- Follow §6 P0 Run 20 plan (supersedes `docs/HANDOFF.md` Steps 1-7 where
  the two disagree). Key change vs HANDOFF: worker runs with
  `FLOW_USE_BASE_PROFILE=1` to skip temp-clone and eliminate the
  cloned-user-data-dir signal.
- `warm_profile.py` auto-drives sign-in — no manual Gmail step needed.
- Post new L2 extend, monitor `extend_1080p_*.mp4`.
- On pass (H1): commit the 4 in-flight `.py` files as 3 logical commits +
  Run 20 session report + update `E2E_RESULTS` + document
  `FLOW_USE_BASE_PROFILE=1` as the default worker env.
- On marketing-landing again (H2): do NOT commit. Collect diagnostics per
  the Run 20 playbook's fail path; user decides Option 2 vs Option 3
  from `docs/CHROME_LAUNCH_SECURITY.md` §4.

**B) Backfill B38 Run 17/18 session report** — docs-only, unblocks tag bump to `v0.7.0`.

**C) Review PR#18 (frontend-optim)** — incremental WS dashboard + shared form constants + bulk-delete endpoint; 4 commits, 121/121 tests. Merge decision if OK.

**D) Something the user names directly.**

---

## 11. Meta

- Main session is a **supervisor** — produces prompts, reviews diffs, syncs docs. Does NOT run heavy work (live Tier 2 + LP credits + long refactors) inline unless user says "chạy ngay" / "execute".
- Claude-in-Chrome MCP is available for read-only DOM probes if a gap surfaces.
- Ghost Chrome cleanup uses `scripts/kill_engine_chrome.ps1` (filters `--user-data-dir` against owned paths; leaves user's personal Chrome alone).
- Current memory count: **12**. Trip-wire tests for 5 locked domains.
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
