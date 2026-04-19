# Session Report — `B38` UI-driven 1080p upscale (Tier 2 Run 17)

> Cherry-picked + async-ported `upscale_unified.py` from `AI-Engine3-Project`
> into FlowEngine. Replaces broken `_upsampled` API poll path
> (permanent 404 per 2026-04-19 probe §5.4). Live-verified on Run 17f.

---

## 1. Metadata

| Field | Value |
|---|---|
| Task ID | `B38` |
| Task type | bug-fix (P0 — 1080p output never succeeded live pre-B38) |
| Session started | 2026-04-19 19:15 local (UTC+7) |
| Session ended | 2026-04-19 20:36 local |
| Duration actual | ~1h20m |
| Worker | Claude Opus 4.7 |
| Branch | `claude/blissful-almeida-59b7fc` (worktree off `master` @ `26ca413`) |
| Profile | `ngoctuandt20` |

---

## 2. Files changed

```
flow/upscale.py       +273 / -0     NEW — cherry-picked UI upscale, async port, tile.click SPA nav
flow/download.py       +35 / -5     B38 wiring: call upscale_and_download_1080p as primary for 1080p
flow/login.py          +24 / -1     stuck-detect: reload URL after 3× same-step failure (user feedback)
```

Also:
- `docs/session-reports/2026-04-19_Tier2_Run17_B38_UI_upscale.md` (this file) — NEW
- `docs/SPEC.md` §D.4 — prepend B38 above B37
- `docs/E2E_RESULTS_PHASE_A.md` — prepend Run 17 block

Total code: `3 files, +332 / -6`.

---

## 3. Root cause recap

1. **`_upsampled` API endpoint is permanently 404** (probe §5.4). Every B34/B34b poll-window extension was architecturally dead — polling longer never helps. Pre-B38 engine never produced a single `t2v_1080p_*.mp4` live (Run 15 3/3, Run 16 same pattern).
2. **Modern Flow UI** triggers upscale via `POST aisandbox-pa.googleapis.com/v1/flow/uploadImage` — only reachable by clicking the icon-only `<button><i>download</i></button>` on `/edit/{routing_slug}` → Radix menu item `^1080pUpscaled$`.
3. **Post-L1 page is on `/project/{pid}` root**, not `/edit/`. Must navigate first.
4. **UUID dualism + SPA-router coupling** (probe §5.5 + Run 17d/17e evidence): neither the API `media_id` nor the tile's `data-tile-id="fe_id_{X}"` suffix is the `/edit/` routing slug. `page.goto(/edit/{either})` is bounced to project root by Flow's SPA. Only `tile.click()` — which goes through the SPA's own `pushState` + router state setup — settles on the correct `/edit/{routing_slug}`.

---

## 4. Iteration log (Run 17 = 6 sub-runs)

| # | Change | Result | Learning |
|---|---|---|---|
| 17a | first live of async upscale port | login stuck on Google overlay `<div class="dKGsO" jsname="OQ2Y6">` intercepting pointer events; `permanent error` after 15× timeout | login.py needs overlay escape — user: *"load lại url là bypass"* |
| 17b | login.py stuck-detect + `page.reload()` after 3× same-step fail | login recovered; upscale hit `Download button not found` — wrong view | post-L1 page is project root, not /edit/ |
| 17c | added logging of `page.url` + button candidate counts | confirmed page.url=`.../project/{pid}` → text-match candidates: 0 | need to navigate to /edit/ first |
| 17d | added `_ensure_edit_view` via `page.goto(/edit/{api_media_id})` | URL bounced back to `/project/{pid}` → 720p fallback | API media_id ≠ routing slug (probe §5.5 UUID dualism confirmed) |
| 17e | read `data-tile-id="fe_id_{X}"` → goto `/edit/{X}` | URL STILL bounced → 720p fallback. Post-click URL showed `edit/4ed94c32…` but tile's `data-tile-id` was `fe_id_54ce98c9…` → different UUIDs | `fe_id_` prefix ≠ routing slug (probe §5.5 partially stale); also `page.goto` doesn't set up SPA state → always bounces |
| **17f** | **`tile.click()`** → let Flow's SPA router own the slug | ✅ SPA landed on `/edit/{correct_slug}`; icon button found; 1080pUpscaled clicked; upscale 63s; **`t2v_1080p_1776605652.mp4` 38.4 MB** | UI-driven nav is the only path that works; data-tile-id is a React DOM ID, unrelated to routing |

---

## 5. Final success trace (Run 17f, job `581287d2-8eeb-4ae0-9d65-fedfe6ad096c`)

```
20:31:25  claim → ngoctuandt20
20:31:33  Clicked new project via: button:has(i.google-symbols):has-text('add_2')
...       (L1 t2v generation — ~90s)
20:33:03  [UPSCALE] Clicking tile for SPA nav to /edit/ view
20:33:04  [UPSCALE] SPA landed on /edit/: .../edit/4ed94c32-a4b1-4c19-898a-133d8e7d0573
20:33:05  [UPSCALE] Attempt 1/2
20:33:05  [UPSCALE] Current URL: .../edit/4ed94c32-a4b1-4c                        ← /edit/ stable
20:33:05  [UPSCALE] i-tag candidate buttons: 1                                    ← button found!
20:33:05  [UPSCALE] Clicked /edit/ download button (i-tag match)
20:33:05  [UPSCALE] Clicked 1080pUpscaled menu item
20:33:08  [UPSCALE] Upscaling in progress...                                      ← busy toast
20:33:38  [UPSCALE] Waiting... (30s)
20:34:09  [UPSCALE] Waiting... (60s)
20:34:12  [UPSCALE] Complete (63s)                                                ← done toast
20:34:13  [UPSCALE] Saved: downloads\t2v_1080p_1776605652.mp4 (38434315 bytes)   ← 38.4 MB!
20:34:14  text-to-video DONE | files=1 media_id=7ca5e9c9-70ad-4832-9c2a-6d9f1dfcff6b
```

Total elapsed: 2m49s (claim → complete); upscale proper: 63s.

---

## 6. Tests

| Suite | Result |
|---|---|
| `pytest tests/` (main dir) | ✅ **119 pass** |
| `pytest tests/` (worktree) | ✅ **119 pass** |
| Import smoke `from flow.upscale import upscale_and_download_1080p, _ensure_edit_view` | ✅ ok |
| Live Run 17f | ✅ `t2v_1080p_1776605652.mp4` 38.4 MB |

Zero new unit tests added (live E2E is the definitive verification for this fix — DOM-dependent UI flow can't be mocked meaningfully).

---

## 7. Invariants & rules verified

- [x] INV-1 Account Binding — single profile `ngoctuandt20` throughout
- [x] INV-3 Store Everything — `project_url` + `media_id` + `output_files` all persisted
- [x] R-CODE-3 Locale-Independent — `^1080pUpscaled$` + `^(close|dismiss|đóng)$` + VI/EN regex for toast states
- [x] R-CODE-7 Download Fallback Chain — tier 1 (UI 1080p) → tier 2 (API 720p) → tier 3 (UI right-click) → tier 4 (blob) preserved
- [x] R-CODE-10 No `datetime.utcnow()` — no new datetime usage
- [x] Model Panel Dismiss — upscale.py `_close_toast` clicks Close button, NEVER Escape (comment at L114)
- [x] No architectural restructure — 3 files changed, contained blast radius

---

## 8. Issues / Decisions

### Decisions

- **Cherry-pick over rewrite**: the old engine's `upscale_unified.py` had 2 years of live field-hardening (toast regexes, close-button dismissal, retry race logic). Porting async + adapting to `/edit/` view's icon button was ~1h; rewrite would have been 3-5h with more bugs.
- **`tile.click()` over DOM-slug extraction**: after Runs 17d & 17e proved `page.goto` always bounces regardless of slug source, the SPA-click path is the only reliable navigation. Code is also simpler (no slug parsing).
- **Login stuck-detect as `feedback` memory + implementation**: user flagged overlay issue mid-session. Saved as `feedback_login_stuck_reload.md` (persistent memory) + implemented reload-on-3×-stuck in `login.py`.

### Vấn đề phát sinh

- Probe §5.5's claim "`fe_id_{slug}` matches `/edit/` slug (strip `fe_id_` prefix)" is **not correct** or no longer correct as of today — Run 17e evidence: `data-tile-id=fe_id_54ce98c9…` vs post-click URL `edit/4ed94c32…`. The `fe_id_` is a React DOM identifier, unrelated to Flow's router slug. Flagged inline in `flow/upscale.py::_ensure_edit_view` docstring.

### Bug candidates NOT fixed (out of scope)

- `docs/session-reports/2026-04-19_download-probe.md` §5.5 — stale/incorrect claim about `fe_id_` = routing slug. Propose P3 doc-fix to strike through the incorrect row + replace with "tile DOM identifier — NOT the /edit/ routing slug; slug only resolvable post-click via SPA router."
- Extend / insert / remove / camera ops — B38 only tested for L1 t2v. Chain L2+ ops may or may not need the tile-click nav (they probably land on /edit/ already). Not validated live this session. Future Tier 2 Run 18 should exercise a 5-op chain with 1080p download at each level.

---

## 9. Handoff notes

- Workdir state: worktree `blissful-almeida-59b7fc` — 3 flow/*.py modified/new + this report + SPEC + E2E log.
- Main dir (`D:/AI/FlowEngine`) has identical flow/*.py (they're the canonical live copies the worker used).
- Server + worker killed at session end — no lingering processes.
- Downloads folder: `t2v_1080p_1776605652.mp4` (38.4 MB — Run 17f golden) + earlier 720p fallback files from 17d/17e.

---

## 10. Done criteria checklist

- [x] Live 1080p download produced — `downloads\t2v_1080p_1776605652.mp4` 38.4 MB
- [x] Pytest 119 pass (main dir + worktree)
- [x] SPEC.md §D.4 B38 entry prepended
- [x] E2E_RESULTS_PHASE_A.md Run 17 block prepended
- [x] Session report written (this file)
- [x] Commit on `claude/blissful-almeida-59b7fc` — `d326e33`
- [x] Run 18 stability validation — 3/3 live (see §11 below)
- [ ] Supervisor review

---

## 11. Run 18 — stability validation (3× t2v, mixed AR)

Added 2026-04-19 22:07–22:16 local, after Run 17f landed green. Goal: rule out the one-off-fluke hypothesis by exercising three jobs on the same commit (`d326e33`) with mixed orientations + mixed prompt styles.

### Jobs

| # | Job id (short) | prompt | AR | claim → complete | upscale | output | size | ffprobe |
|---|---|---|---|---|---|---|---|---|
| J1 | `22c84f49` | a dramatic lighthouse at dusk… | 16:9 | 22:09:?? → 22:11:18 | 66s | `t2v_1080p_1776611477.mp4` | 10 954 416 B / 10.9 MB | 1920×1080 h264 24fps 8.0s |
| J2 | `09fd6ef7` | a close-up of a violin on a wooden table… | 9:16 | 22:11:?? → 22:13:37 | 51s | `t2v_1080p_1776611618.mp4` | 11 885 371 B / 11.9 MB | 1920×1080 h264 24fps 8.0s |
| J3 | `87b26c12` | a quiet river flowing through a mossy forest… | 16:9 | 22:14:?? → 22:16:29 | 96s | `t2v_1080p_1776611789.mp4` | 32 573 938 B / 32.6 MB | 1920×1080 h264 24fps 8.0s |

**Verdict: ✅ 3/3 PASS.** Combined with Run 17f: **4/4 live** — B38 upgraded from "first successful run" to **stable** for L1 t2v.

### Observations

- **Orientation-agnostic:** 2× LANDSCAPE + 1× PORTRAIT all hit `tile.click → /edit/ → 1080pUpscaled → save` cleanly, no 720p fallback.
- **Upscale time 51–96s.** Well inside our retry window (B34b 360s). No retries triggered.
- **Frame is always 1920×1080 h264** regardless of composer AR. Flow's 1080p upscale pipeline outputs landscape frame; PORTRAIT content is letterboxed inside the same frame. Expected — not a bug, but worth noting if downstream expects 1080×1920 for vertical.
- **DB invariants:** all 3 jobs have `status=completed` + `output_files=['downloads\\t2v_1080p_*.mp4']` + `media_id` set. INV-3 (store-everything) held across 3/3.
- **No login-stuck recurrence** — worker hot-reused profile `ngoctuandt20` across all three without reload (the B38 login stuck-detect code is present but wasn't exercised this run; it was the cold-start hazard in 17a).

### Log trace (J2 — PORTRAIT, 51s — fastest of the three)

```
22:12:41 flow.upscale: [UPSCALE] Clicking tile for SPA nav to /edit/ view
22:12:41 flow.upscale: [UPSCALE] SPA landed on /edit/: .../project/2e957cd2-…/edit/c5d36ed6-42f2-4f38-87db-477874b2ea08
22:12:43 flow.upscale: [UPSCALE] i-tag candidate buttons: 1
22:12:43 flow.upscale: [UPSCALE] Clicked /edit/ download button (i-tag match)
22:12:43 flow.upscale: [UPSCALE] Clicked 1080pUpscaled menu item
22:12:46 flow.upscale: [UPSCALE] Upscaling in progress...
22:13:37 flow.upscale: [UPSCALE] Complete (51s)
```

### Still open

- **L2+ chain with 1080p download (Run 19)** — extend / insert / remove / camera at L2+ not yet live-verified. The worker should already start on `/edit/` for L2+ (tile-activated by `navigation.py` B32), so `_ensure_edit_view` is expected to short-circuit on the first `if "/edit/" in page.url: return` branch — but not verified.
- **Concurrent multi-profile (Run 20)** — needs a second English-locale account before scheduling.
- **Negative paths (Run P3)** — out-of-credit upscale, hard toast failure paths.

---

_Sign-off: ✅ Run 17 + Run 18 — B38 stable for L1 t2v. Ready for supervisor review._
