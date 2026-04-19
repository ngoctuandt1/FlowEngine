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
- [ ] Commit on `claude/blissful-almeida-59b7fc`
- [ ] Supervisor review

---

_Sign-off: ✅ Ready for supervisor review._
