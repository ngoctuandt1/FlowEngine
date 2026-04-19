# Phase A — E2E Results

> Live engine E2E validation log per `docs/WORKPLAN.md` §5 / §7 Meta.
> Format: one section per attempt, most recent first. `Tier 1 = DOM probe via Chrome MCP`; `Tier 2 = full engine-driven chain via REST API`; `Tier 1.5 = DB-layer live validation against real-run snapshot (no Chrome)`.

---

## Tier 2 — 2026-04-19 — Run 17 — ✅ **PASS (1/1 1080p)** — B38 UI-driven 1080p upscale verified live

| Field | Value |
|---|---|
| Date | 2026-04-19 19:15–20:36 local (UTC+7); ~1h20m across 6 sub-iterations (17a–17f) |
| Tier | 2 — full engine-driven single-job via REST API |
| Profile | `ngoctuandt20` |
| Scope | Validate B38 cherry-pick (`flow/upscale.py` NEW + `flow/download.py` + `flow/login.py`) — replaces broken `_upsampled` API poll with UI-driven `/edit/` → icon Download → `1080pUpscaled` menu → `uploadImage` POST flow. Pre-B38 historical count of `t2v_1080p_*.mp4` across all runs: **0** |
| Branch | `claude/blissful-almeida-59b7fc` (worktree off `master` @ `26ca413`) |
| Final job ID | `581287d2-8eeb-4ae0-9d65-fedfe6ad096c` (Run 17f) |

### Result

| # | Job id (short) | prompt | AR | status | media_id | output |
|---|---|---|---|---|---|---|
| 17f | `581287d2` | a bamboo forest in autumn with soft wind through leaves | 16:9 | ✅ 20:34:14 | `7ca5e9c9-70ad-4832-9c2a-6d9f1dfcff6b` | `downloads\t2v_1080p_1776605652.mp4` (**38 434 315 B / 38.4 MB** — first live 1080p in project history) |

**Verdict: ✅ PASS.** Live reproducible UI-driven upscale path. Upscale proper took 63s (20:33:08 busy toast → 20:34:12 done toast); total claim → complete 2m49s.

### Iterations (6 sub-runs to reach pass)

| # | Change | Result | Lesson |
|---|---|---|---|
| 17a | first live of async upscale port | login stuck on Google `<div class="dKGsO" jsname="OQ2Y6">` overlay (pointer intercept) | login.py needs stuck-escape — user feedback: *"load lại url là bypass"* |
| 17b | login.py stuck-detect + `page.reload()` after 3× same-step fail | login recovered; upscale hit `Download button not found` | post-L1 page is project root, not /edit/ |
| 17c | added `page.url` + button candidate-count logging | confirmed page.url=`/project/{pid}` → candidates: 0 | need /edit/ navigation |
| 17d | `_ensure_edit_view` via `page.goto(/edit/{api_media_id})` | SPA bounced to `/project/{pid}` → 720p fallback | API media_id ≠ routing slug (UUID dualism confirmed) |
| 17e | read `data-tile-id="fe_id_{X}"` → goto `/edit/{X}` | bounced again (post-click URL `edit/4ed94c32…` vs tile `fe_id_54ce98c9…` — different UUIDs) | `fe_id_` ≠ routing slug; `page.goto` never sets up SPA state |
| **17f** | **`tile.click()`** → SPA router owns slug resolution | ✅ SPA landed on `/edit/{correct_slug}`; icon button found; 1080pUpscaled clicked; 63s upscale; 38.4 MB file saved | **UI-click is the only reliable /edit/ navigation** |

### Success trace (Run 17f, `logs/worker.log.run17f`)

```
20:33:03 flow.upscale: [UPSCALE] Clicking tile for SPA nav to /edit/ view
20:33:04 flow.upscale: [UPSCALE] SPA landed on /edit/: .../edit/4ed94c32-a4b1-4c19-898a-133d8e7d0573
20:33:05 flow.upscale: [UPSCALE] Attempt 1/2
20:33:05 flow.upscale: [UPSCALE] Current URL: .../edit/4ed94c32-a4b1-4c                 ← /edit/ stable
20:33:05 flow.upscale: [UPSCALE] i-tag candidate buttons: 1                              ← button found
20:33:05 flow.upscale: [UPSCALE] Clicked /edit/ download button (i-tag match)
20:33:05 flow.upscale: [UPSCALE] Clicked 1080pUpscaled menu item
20:33:08 flow.upscale: [UPSCALE] Upscaling in progress...
20:34:12 flow.upscale: [UPSCALE] Complete (63s)
20:34:13 flow.upscale: [UPSCALE] Saved: downloads\t2v_1080p_1776605652.mp4 (38434315 bytes)
20:34:14 worker.dispatcher: text-to-video DONE | files=1 media_id=7ca5e9c9-70ad-4832-9c2a-6d9f1dfcff6b
```

### Invariants observed

| Invariant | Status | Evidence |
|---|---|---|
| INV-1 Account Binding | ✅ | single profile `ngoctuandt20`; no switch |
| INV-3 Store Everything | ✅ | `project_url` + `media_id` + `output_files` + `completed_at` all persisted |
| R-CODE-3 Locale-Independent | ✅ | `^1080pUpscaled$` + `^(close\|dismiss\|đóng)$` + VI/EN toast regexes |
| R-CODE-7 Download Fallback Chain | ✅ | tier 1 (UI 1080p, B38) → tier 2 (API 720p) → tier 3 (UI right-click) → tier 4 (blob) |
| Model Panel Dismiss (CLAUDE.md §7) | ✅ | `_close_toast` clicks Close button, never Escape |
| B35 force x1 | ✅ | `Output count set to x1 (chip verified)` logged, `files=1` |
| **B38 UI 1080p path** | ✅ | `Saved: t2v_1080p_…` + 38 MB file (vs pre-B38: 0 files ever) |

### Residual / out of scope

- B38 validated for L1 t2v only. Chain L2+ ops (extend / insert / remove / camera) not tested with 1080p. They likely land on `/edit/` already (no tile-click needed) but should be confirmed in Run 18.
- Probe §5.5 claim "`fe_id_{slug}` == routing slug" is stale/wrong per Run 17e evidence. Flagged as P3 doc fix.
- login.py stuck-detect only fires on (email / password / totp / challenge_select). Other stuck surfaces (recaptcha, device-verify) not covered — out of scope for B38.

---

## Tier 2 — 2026-04-19 — Run 15 — ✅ **PASS (3/3)** — B37 harvest + B35 x1 verified across 3 diverse t2v jobs

| Field | Value |
|---|---|
| Date | 2026-04-19 18:33:38–18:47:04 local (UTC+7); ~13m26s serial |
| Tier | 2 — full engine-driven parallel-queue / serial-execute via REST API |
| Profile | `ngoctuandt20` (1 profile → worker runs 3 jobs serially) |
| Scope | Regression check for B37 (`7914020`) + B35 (`dc486a7`) across 3 diverse prompts and mixed aspect ratios (16:9 / 9:16 / 16:9) |
| Branch | `claude/condescending-mcnulty-82b9c3` (worktree off `master` @ `7914020`) |
| Job IDs | `a0333bca`, `4f60e9f7`, `36c845bb` |

### Three-job result

| # | Job id (short) | prompt | AR | status | media_id | output |
|---|---|---|---|---|---|---|
| J1 | `a0333bca` | cinematic aerial shot of a coastal lighthouse at golden hour | 16:9 | ✅ 18:38:16 | `3e9f60e2-caec-4031-a9de-a9c7bf271ec0` | `downloads\t2v_720p_1776598695.mp4` (5 137 569 B) |
| J2 | `4f60e9f7` | a hummingbird hovering near bright red hibiscus flowers | 9:16 | ✅ 18:42:45 | `988440e4-41a5-4431-bfa6-c481e047db7f` | `downloads\t2v_720p_1776598965.mp4` (2 439 801 B) |
| J3 | `36c845bb` | sunrise over a snowy mountain ridge reflected in a lake | 16:9 | ✅ 18:47:04 | `0c70a870-d799-4c22-984e-c7a0db5c84f8` | `downloads\t2v_720p_1776599223.mp4` (3 973 522 B) |

**Verdict: ✅ PASS.** All pass criteria met across 3/3 jobs with zero regressions vs Run 14's post-fix state.

### Pass-criteria scorecard

| Criterion | Expected | Observed | Verdict |
|---|---|---|---|
| B35 `Output count set to x1 (chip verified)` | 3× | 3× (18:34:04 / 18:38:35 / 18:43:01) | ✅ |
| B37 dispatcher DONE `media_id=<uuid>` not `None` | 3× | 3× (`3e9f60e2` / `988440e4` / `0c70a870`) | ✅ |
| B37 output file `t2v_720p_*.mp4` (API path) | 3× | 3× | ✅ |
| B37 output file `t2v_blob_*.mp4` (fallback path) | 0× | 0× | ✅ |
| `files=1` (B35 x1 downstream) | 3× | 3× | ✅ |
| `Traceback` / `ERROR` / `FAILED` / `No media IDs` | 0 each | 0 / 0 / 0 / 0 | ✅ |

### B35 signals captured (`logs/worker.log`, deduplicated)

```
18:34:04 flow.operations.generate: Step 4.5: Force output count = x1                         (J1)
18:34:04 flow.operations.generate: Output count set to x1 (chip verified)                    (J1)
18:38:35 flow.operations.generate: Aspect ratio set to 9:16 (chip verified: crop_9_16)       (J2)
18:38:35 flow.operations.generate: Output count set to x1 (chip verified)                    (J2)
18:43:01 flow.operations.generate: Output count set to x1 (chip verified)                    (J3)
```

### B37 signals captured (`logs/worker.log`, deduplicated)

```
18:38:15 flow.download: Downloaded 720p: downloads\t2v_720p_1776598695.mp4 (5137569 bytes)   (J1)
18:38:16 worker.dispatcher: text-to-video DONE | files=1 media_id=3e9f60e2-caec-4031-...
18:42:45 flow.download: Downloaded 720p: downloads\t2v_720p_1776598965.mp4 (2439801 bytes)   (J2)
18:42:45 worker.dispatcher: text-to-video DONE | files=1 media_id=988440e4-41a5-4431-...
18:47:03 flow.download: Downloaded 720p: downloads\t2v_720p_1776599223.mp4 (3973522 bytes)   (J3)
18:47:04 worker.dispatcher: text-to-video DONE | files=1 media_id=0c70a870-d799-4c22-...
```

### Invariants observed

| Invariant | Status | Evidence |
|---|---|---|
| INV-1 Account Binding | ✅ | All 3 jobs stored `profile=ngoctuandt20`; worker only claimed profile-matching rows |
| INV-3 Store Everything | ✅ | 3/3 persisted `project_url` + `media_id` (real UUIDv4) + `output_files` + `completed_at` — Run 14's `media_id=None` regression fully suppressed |
| R-CODE-3 Locale-Independent | ✅ | J2 9:16 chip via `crop_9_16` selector (B19); all 3 x1 chips via Radix `[id$="-trigger-1"]` (B35) |
| **B35 force x1** | ✅ | 3× `Output count set to x1 (chip verified)` + 3× `files=1` |
| **B37 harvest key** | ✅ | 3× `media_id=<uuid>` in DONE logs + 3× API path + 0× blob fallback |

### What this session did NOT change

- Zero `.py` diff (verification-only).
- No SPEC.md §D.4 change (B35/B37 markers already present from Run 13 / Run 14).
- Session report committed: `docs/session-reports/2026-04-19_Tier2_Run15_B37_verify.md`.

### Residual observation (out of scope, P3)

All 3 jobs fell through to 720p after exhausting `FLOW_UPSCALE_MAX_RETRIES=12 × 15s = 180s` polling on the `_upsampled` endpoint. `downloads/` historical count: 0 `t2v_1080p_*` files across all runs. B34's window-extension (30s → 180s) is still insufficient for Flow's actual upscale latency on `veo-3.1-fast-lp`. Not investigated — 1080p is not required for this task. Future option: bump `FLOW_UPSCALE_MAX_RETRIES` via env var (zero code change; already env-configurable at [`flow/download.py:21-22`](flow/download.py:21)) or open B38-candidate to probe Flow's actual upscale behavior.

---

## Tier 2 — 2026-04-19 — Run 14 — ⚠️ **PARTIAL** (B35 re-verified · surfaced B37 download-harvest bug · fix landed same session)

| Field | Value |
|---|---|
| Date | 2026-04-19 18:00:24–18:01:51 local (UTC+7); ~1m27s |
| Tier | 2 — full engine-driven single-job via REST API |
| Profile | `ngoctuandt20` |
| Scope | Re-run B35 verify (user: "chạy cái mới") — chain surfaced media-id harvest bug |
| Branch | `claude/stupefied-feistel-707bac` |
| Job id | `da2eb837-22d0-45de-9e7a-8a3b352975cc` |

### Single-job result

| # | Job id (short) | type | status | media_id | output |
|---|---|---|---|---|---|
| J1 | `da2eb837` | text-to-video (16:9, veo-3.1-fast-lp) | ✅ completed 18:01:51 | **`None`** (harvest failed — see B37) | `downloads\t2v_blob_1776596510.mp4` (1 934 639 B · blob fallback) |

**Verdict: ⚠️ PARTIAL.** B35 x1 force ✅ confirmed again (`Step 4.5: Force output count = x1` + `Output count set to x1 (chip verified)` + `files=1`). But `media_id=None` stored to DB + fell through to `_download_blob` instead of 720p API download → surfaced B37 (download-harvest key mismatch). Same worktree fixed B37 in-session; fix landed + 2 trip-wire tests added (119 pass).

### B35 signals captured (`logs/worker.log`)

```
18:00:46 flow.operations.generate: Step 4.5: Force output count = x1
18:00:46 flow.operations.generate: Output count set to x1 (chip verified)
18:00:48 flow.submit: Submit confirmed: cards 0 -> 2
18:01:46 flow.wait: Completion via DOM (new video at 57%) after 58s
18:01:48 flow.operations.generate: Generation complete!
18:01:48 flow.operations.generate: Step 8: Download video
18:01:48 [WARNING] flow.download: No media IDs found for download
18:01:50 flow.download: Blob download: downloads\t2v_blob_1776596510.mp4 (1934639 bytes)
18:01:51 worker.dispatcher: text-to-video DONE | files=1 media_id=None
```

(`cards 0 -> 2` is Flow SPA's card-layout quirk — the actual generated clip count is 1 per `files=1`. Pre-B35 submits showed `cards 0 -> 4` with `files=2`.)

### B37 discovery

Comparing Run 13 (17:49, `media_id=7c4fa302` ✅) vs Run 14 (18:00, `media_id=None` ⚠️) on the same account/model/aspect — non-deterministic. Code inspection found two issues:

1. **Key mismatch (primary):** `flow/download.py:50` reads `evt["media_id"]` but `client.py:506` writes `{"mid": ...}`. Primary harvest path always empty.
2. **List-of-dicts passed as string (secondary fallback):** `_video_urls` iterator passed each dict to `media_id_from_url`, which stringified it. Accidentally worked when the URL carried `?name=…` (Run 13), failed when captures were direct `.mp4` (Run 14).

**In-session fix** — `flow/download.py:49-67`:
- `evt["mid"]` instead of `evt["media_id"]`
- unwrap `entry["url"]` before `media_id_from_url(url)`

**Tests** — `tests/test_download.py` +2 source trip-wires locking both contracts. Full suite 119 pass.

### Invariants observed

| Invariant | Status | Evidence |
|---|---|---|
| INV-1 Account Binding | ✅ | `profile=ngoctuandt20` on claim + completion |
| INV-3 Store Everything | ⚠️ partial | `project_url` + `output_files` persisted, `media_id=None` (B37 pre-fix — any L2 child of this job would fail) |
| **B35 force x1** | ✅ | `Step 4.5: Force output count = x1` + `Output count set to x1 (chip verified)` + `files=1` |
| **B37 download-harvest** | ❌ surfaced → ✅ fixed same session | See root cause + fix above |

### What this session changed

- `flow/download.py:49-67` — B37 fix (harvest key rename + list-of-dicts unwrap).
- `tests/test_download.py` +2 trip-wires.
- `docs/SPEC.md` — appended new B37 entry above B35; B35 header updated with Run 14 re-verify marker.
- `docs/E2E_RESULTS_PHASE_A.md` — this entry.

---

## Tier 2 — 2026-04-19 — Run 13 — ✅ **PASS (1/1)** — B35 output-count x1 force verified live

| Field | Value |
|---|---|
| Date | 2026-04-19 17:49:00–17:53:31 local (UTC+7); ~4m31s single-job run |
| Tier | 2 — full engine-driven single-job via REST API |
| Profile | `ngoctuandt20` (default quantity = x2 — the exact account B35 was written to protect) |
| Scope | B35 live verification — force count=x1 before submit on L1 `text-to-video` |
| Branch | `claude/stupefied-feistel-707bac` off `master` |
| Job id | `45f7ccf6-7ea5-4943-a4ea-4ce8ee9dc4ef` |

### Single-job result

| # | Job id (short) | type | status | media_id | output |
|---|---|---|---|---|---|
| J1 | `45f7ccf6` | text-to-video (16:9, veo-3.1-fast-lp) | ✅ completed 17:53:30 | `7c4fa302-357a-48c3-952b-08e1e7deb71b` | `downloads\t2v_720p_1776596010.mp4` (17 903 542 B) |

**Verdict: ✅ PASS — B35 confirmed.** `files=1` (single clip, not two) + single `media_id` recorded + worker log proves the Quantity tablist was clicked to x1.

### B35 signals captured (`logs/worker.log`)

```
17:49:00 worker: Claimed job 45f7ccf6 [text-to-video] profile=ngoctuandt20
17:49:00 worker.dispatcher: text-to-video START | prompt='B35 live verify x1 - a zen garden at sunset' model=veo-3.1-fast-lp profile=ngoctuandt20
17:49:16 flow.operations.generate: Step 4.5: Force output count = x1
17:49:16 flow.operations.generate: Output count set to x1 (chip verified)
17:53:30 worker.dispatcher: text-to-video DONE | files=1 media_id=7c4fa302-357a-48c3-952b-08e1e7deb71b
17:53:31 worker: Job 45f7ccf6 result sent -> completed
```

The `(chip verified)` suffix fires only when `_set_output_count` successfully matches `x1` in the chip's post-close `innerText` — i.e. Flow accepted the x1 selection and the panel actually rendered it. `files=1` from the downstream download path independently confirms the x2→x1 reduction.

### Invariants observed

| Invariant | Status | Evidence |
|---|---|---|
| INV-1 Account Binding | ✅ | `profile=ngoctuandt20` on claim + completion |
| INV-3 Store Everything | ✅ | `project_url` + `media_id` + `output_files` persisted |
| R-CODE-3 Locale-Independent | ✅ | `_set_output_count` uses the Radix `[id$="-trigger-1"]` selector — no locale strings |
| **B35 force x1** | ✅ | `Step 4.5: Force output count = x1` + `Output count set to x1 (chip verified)` logs |

### What this session did NOT change

- Zero `.py` diff (verification-only).
- SPEC.md §D.4 B35: appended `· Tier2 Run 13 verified live 2026-04-19 (job 45f7ccf6, files=1 media_id=7c4fa302, chip verified x1)` marker.
- No new B-numbered ticket.

---

## Tier 2 — 2026-04-19 — Run 12 — ✅ **PASS (5/5)** — B32 chain-with-extend-middle verified live

| Field | Value |
|---|---|
| Date | 2026-04-19 14:15:25–14:25:04 local (UTC+7); ~9m39s rerun (full session ~27m incl. aborted attempt + queue cleanup) |
| Tier | 2 — full engine-driven chain via REST API |
| Profile | `ngoctuandt20` (EN-locale) |
| Scope | B32 architectural-fix live verification on 5-op chain (t2v 9:16 → extend → insert → remove → camera Orbit left) |
| Branch | `claude/admiring-maxwell-1064c0` off `master` @ `b4e99f6` |
| Chain id | `2b0f2667-b854-4af2-901d-429aac266c6d` |
| Project url | `https://labs.google/fx/tools/flow/project/0d6ced8a-5207-4359-959e-2fc6408ca2fe` |
| Session report | [`docs/session-reports/2026-04-19_Tier2_Run12_B32_verify.md`](session-reports/2026-04-19_Tier2_Run12_B32_verify.md) |

### 5-op chain result

| # | Job id (short) | type | status | media_id | output |
|---|---|---|---|---|---|
| J1 | `01ab28d0` | text-to-video (9:16) | ✅ completed 14:17:05 | `a33b2e9d-…` | `downloads\t2v_720p_1776583024.mp4` |
| J2 | `a0ed1ab6` | extend-video | ✅ completed 14:19:38 | `7d53d6fc-…` (**NEW** — INV-5 mint) | `downloads\ext_720p_1776583177.mp4` |
| J3 | `3f31cc22` | insert-object (bbox=0.6,0.6,0.2,0.2) | ✅ completed 14:21:12 | `7d53d6fc-…` (preserved) | `downloads\ins_720p_1776583271.mp4` |
| J4 | `2922a34c` | remove-object (bbox=0.3,0.5,0.4,0.4) | ✅ completed 14:23:22 | `7d53d6fc-…` (preserved) | `downloads\rm_720p_1776583401.mp4` |
| J5 | `f66f5718` | camera-move (Orbit left) | ✅ completed 14:25:04 | `7d53d6fc-…` (**NOT minted** — see Finding 1) | `downloads\cam_720p_1776583503.mp4` |

**Verdict: ✅ PASS — B32 architectural fix confirmed on chain-with-extend-middle pattern.** 5/5 jobs completed end-to-end serial (J1→J2→J3→J4→J5). Zero `Mode button disabled` errors. Chain-with-extend-middle pattern (which was the Run 11 blocker before B32 landed) now fully unblocked.

### B32 signals captured (`logs/worker.log`)

**Signal 1 — J2 dispatch (tile-click fallback landed on wrong clip):**

```
14:17:10 flow.operations._base: On project view — clicking video tile to enter edit mode  (B27 SPA-bounce fallback)
14:17:12 flow.operations._base: Clicked first [data-tile-id] tile
14:17:15 flow.operations._base: Edit mode entered: .../edit/7d53d6fc-…  (wrong clip)
14:17:15 flow.operations._base: URL media differs from target: url=7d53d6fc-c9bd-4211-9 target=a33b2e9d-98ec-4288-b — activating target tile
14:17:16 flow.operations._base: Activated clip tile for media=a33b2e9d-98ec-4288-b
14:17:16 flow.operations._base: Video element loaded
```

**Signal 2 — J3 dispatch (direct child of extend — the key test):**

```
14:19:39 flow.operations._base: Navigating to edit URL: .../edit/7d53d6fc-…  (J2.edit_url via B22 direct parent)
14:19:43 flow.operations._base: URL media differs from target: url=7d53d6fc-c9bd-4211-9 target=a33b2e9d-98ec-4288-b — activating target tile  (B30 walk-up past extend)
14:19:45 flow.operations._base: Activated clip tile for media=a33b2e9d-98ec-4288-b
14:19:45 flow.operations._base: Video element loaded
14:19:45 flow.operations._base: Clicked mode button via title='Insert'  ← enabled (B32 architectural fix confirmed)
14:19:46 flow.operations._base: Drew bbox on canvas: x=0.60 y=0.60 w=0.20 h=0.20 canvas=390x694
```

**J4 + J5 did NOT fire B32** (expected): B30 walk-up for J4 (parent=J3 insert, non-extend) stops at J3 → target `7d53d6fc` == URL media → no activation needed. Same for J5 (parent=J4 remove). Both mode buttons (`title='Remove'`, `title='Camera'`) clicked enabled on first try.

### Invariants observed

| Invariant | Status | Evidence |
|---|---|---|
| INV-1 Account Binding | ✅ | All 5 jobs stored `profile=ngoctuandt20` |
| INV-2 Navigate by `edit_url` | ✅ | Every L2+ dispatch log shows `Navigating to edit URL: .../edit/{media_id}` |
| INV-3 Store Everything | ✅ | Each completed job persisted `project_url` + `media_id` + `edit_url` + `output_files` |
| INV-4 Serial per Project | ✅ | `Project lock ACQUIRED/RELEASED` pairs bracket every dispatch; J1→J2→J3→J4→J5 strict order |
| INV-5 `media_id` re-extracted per op | ⚠️ partial | J1→J2 extend minted new (✅), J3+J4 in-place preserve (✅), **J5 camera-move did NOT mint new (contradicts post-Run-10 SPEC revision — see Finding 1)** |
| R-CODE-3 Locale-Independent | ✅ | B19/B26/title-attr/B12-computed-color selectors held on EN profile |
| **B32 architectural fix** | ✅ | 2× firings (J2 + J3); 0× `Mode button disabled` errors; downstream mode-button clicks all enabled |

### Findings (NOT fixed — supervisor review)

1. **[B33-candidate · P2] J5 camera-move did NOT mint new `media_id`.** Run 10 (J2 camera-move directly on J1 t2v) minted new uuid — evidence basis for current SPEC INV-5 revision. Run 12 (J5 camera-move after extend→insert→remove) kept `7d53d6fc` (same as parents J3/J4). Hypothesis: camera-submit doesn't bump `page.url` when the active clip is already deep in project history, so engine's post-op URL re-extraction reads the stale uuid. Not a B32 regression — chain completed and the mp4 download succeeded (`cam_720p_1776583503.mp4`, 1712500 B, file distinct). SPEC INV-5 revision may need a context-sensitivity note, or engine-side camera-move may need a URL-bump-wait.

2. **Queue hygiene (ops-only note, not engineering).** During startup, I found 23 pending + 3 claimed stray jobs on `ngoctuandt20` from prior test sessions (chains `b5582b10`, `71013075`, `2141a50e`, `15f5602c`, `93e940fb`, `4046ea7d`, `a1cc6a67`, `cd8ec66b`). My first worker died mid-J1-download (no traceback — possibly `run_in_background` lifecycle at ~131s; `nohup` + `disown` used for the rerun worked fine). Respawned worker consumed ≥2 LP of strays before I cleaned up via `DELETE /api/jobs/{id}` × 20 + `DELETE` old chain × 5. Worker restart via `nohup ... & disown` survived the full 10-min rerun.

### What this session did NOT change

- Zero `.py` / profile-creds / config diff (verification + docs only).
- No new B-numbered fix; only **B32 verification marker** appended in `SPEC.md` §D.4 (`· Tier2 Run 12 verified live 2026-04-19`).
- Finding 1 is surfaced as **B33-candidate** but not opened/fixed — supervisor call.

---

## Tier 2 — 2026-04-19 — Tests 2/3/4 — ⚠️ **PARTIAL / BLOCKED** (Test 3 ✅ · Test 2 PARTIAL · Test 4 ❌ · 2 bug-candidates + 1 SPEC INV-5 contradiction surfaced)

| Field | Value |
|---|---|
| Date | 2026-04-19 11:52–12:05 local (UTC+7); ~13 min run |
| Tier | 2 — full engine-driven chain via REST API |
| Profile | `ngoctuandt20` (EN-locale) |
| Scope | WORKPLAN §5.2 Tests **2** (5-op chain) · **3** (bbox out-of-range) · **4** (3 camera presets) |
| Branch | `claude/sweet-hawking-9c8ebb` off `master` @ `2dbe544` |
| Chain id | `4a0d03b5-e31b-449b-9fcc-99ac9d1dc583` |
| Session report | [`docs/session-reports/2026-04-19_tests_2-3-4_ui.md`](session-reports/2026-04-19_tests_2-3-4_ui.md) |

### Test 2 — 5-op chain (t2v → extend → insert → remove → camera Orbit left)

| # | Job id (short) | type | status | media_id | output |
|---|---|---|---|---|---|
| J1 | `cea64458` | text-to-video | ✅ completed 11:56:02 | `6842325d-…` | `downloads\t2v_720p_1776574562.mp4` |
| J2 | `a125c084` | extend-video | ✅ completed 11:58:25 | `1a6e3b77-…` (**NEW uuid** — SPEC INV-5 contradiction, see below) | `downloads\ext_720p_1776574672.mp4` + `1776574705.mp4` |
| J3 | `de5487da` | insert-object | ❌ failed 11:58:45 | — | `RuntimeError: Failed to find Insert button` on `/edit/1a6e3b77-…` after `Video element loaded` |
| J4 | `8ed20a7d` | remove-object | ⏸ never claimed | — | blocked by J3 failure |
| J5 | `346d19e9` | camera-move (Orbit left) | ⏸ never claimed | — | blocked by J4 pending |

**Verdict: PARTIAL — 2/5 completed.** J1 + J2 validate B1 (aspect-ratio real impl + chip `crop_9_16`) + B22 (L2 claim-time inherit). J3 failed via new-bug candidate (see below). J4/J5 never claimed because `claim_next_job` L2+ predicate requires `parent.status='completed'`.

### Test 3 — bbox out-of-range {x:1.5, y:0, w:0.5, h:0.5}

**Verdict: ✅ PASS.** `POST /api/jobs` with `parent_job_id=J1` returned **HTTP 422** — Pydantic `server/models/job.py::BBox` `Field(ge=0, le=1)` caught the out-of-range coord at the API boundary. Body: `{"detail":[{"type":"less_than_equal","loc":["body","bbox","x"],"msg":"Input should be less than or equal to 1","input":1.5}]}`. Request never reached the engine; the B2/B11 in-engine overflow-clamp path (`flow/operations/_base.py::draw_bbox_on_video`) remains a defense-in-depth layer, covered by `tests/test_bbox.py`. Key negative guard ("NOT `RuntimeError`") holds — the boundary rejection is deterministic.

### Test 4 — 3 camera presets as J1 children + 1 diagnostic

| Job id | direction | parent | status | failure |
|---|---|---|---|---|
| `4a0a2bfb` | Orbit left | J1 | ❌ failed 12:01:23 | `Failed to find camera preset: Orbit left` — preceded by `Video element not found after 15s — proceeding anyway` |
| `df7fa268` | Low | J1 | ❌ failed 12:01:46 | same pattern |
| `3a99988e` | Dolly out | J1 | ❌ failed 12:02:11 | same pattern |
| `76d81c00` | Orbit left (diagnostic) | J2 (extend output) | ❌ failed 12:04:28 | `Failed to find **Camera button**` — different failure: `Video element loaded` succeeded on `/edit/1a6e3b77-…` but action sidebar had no Camera button |

**Verdict: ❌ FAIL.** All 3 Test-4 presets failed on J1 parent's `/edit/{old_media_id}` URL (video element not found — likely stale after sibling extend created new media). Diagnostic on J2 parent failed one step earlier (Camera button absent from sidebar). The "Low"-must-not-match-"Lower" trip wire could not be exercised because the upstream `_click_preset` found 0 candidates.

### Invariants observed

| Invariant | Status | Evidence |
|---|---|---|
| INV-1 Account Binding | ✅ | All 9 jobs (5 chain + 3 Test-4 + 1 diagnostic) stored `profile=ngoctuandt20`; worker claimed only profile-matching rows |
| INV-2 Navigate by `edit_url` | ✅ | `navigate_to_edit` log `Navigating to edit URL: .../edit/{media_id}` on every L2+ dispatch |
| INV-3 Store Everything | ✅ | J1 & J2 persisted `project_url` + `media_id` + `edit_url` + `output_files` + `completed_at`; B22 claim-time inherit visible on J2/J3 rows |
| INV-4 Serial per Project | ✅ | `Project lock ACQUIRED` / `RELEASED` pairs around every dispatch; 4 Test-4 jobs ran serially on shared `project_url` |
| INV-5 `media_id` per op | ❌ **SPEC contradiction** | J2 `extend-video` minted NEW `media_id` (`6842325d` → `1a6e3b77`) — SPEC §A.1 INV-5 matrix claims `extend-video` is "Preserved (Flow updates in-place)". Re-extracted by `finalize_operation` from post-submit `page.url`. See §7 of session report. |
| R-CODE-3 Locale-Independent | ✅ | B18 `add_2` homepage · B19 `crop_9_16` chip · B26 `arrow_forward` submit · B12 computed-color preset verify — all icon/exact-text, no locale-string deps |

### New findings (NOT fixed — supervisor action required)

1. **[B28-candidate · P0] Action sidebar missing on extend-output `/edit/` URL.** Chain with `extend-video` in the middle cannot proceed to L3 (insert | remove | camera) because the extend-output's `/edit/{new_media_id}` renders without the Insert/Camera/Remove action buttons in the right sidebar. Reproduced by J3 (`Failed to find Insert button`) + diagnostic (`Failed to find Camera button`). Belongs in Tier-1 DOM-probe follow-up to capture the difference vs. t2v or camera-output edit mode.
2. **[B29-candidate · P0] L1 `/edit/{media_id}` URL breaks after a sibling `extend-video` runs.** Navigation succeeds (URL matches) but `Video element not found after 15s`; action sidebar not rendered. All 3 Test-4 camera attempts on J1 parent showed this. Likely Flow-SPA treats extend output as the project's new "current" media; the prior `/edit/` URL becomes stale. Blocks any L2 op parallelism on an L1 after a completed extend.
3. **[INV-5 contradiction]** `extend-video` empirically mints a NEW `media_id` (not preserved in-place). SPEC INV-5 matrix needs revision — the "Preserved" row should either split or reclassify `extend-video`. Tier-2 Run 10 (cited as evidence for INV-5) did not exercise `extend-video` — so the "preserved" claim for extend rests solely on `FLOW_MULTILEVEL_JOBS.md §10` (2026-04-16, different account), which appears to have aged out. Worth re-verifying before SPEC edit.

### What this session did NOT change

- Zero `.py` diff (test-execution + docs only).
- No SPEC.md edit — task rule "append 'verified' nếu pass"; did not pass, so no append.
- No B-numbered bug closed. New bug-candidate tickets **B28** and **B29** should be opened by supervisor if `extend-video` is a supported chain primitive.

---

## Tier 1.5 — 2026-04-19 — Tests 5/6/7 infra — ✅ **§5.2 INFRA INVARIANTS COVERED**

| Field | Value |
|---|---|
| Date | 2026-04-19 |
| Tier | 1.5 — DB + claim-algorithm integration (no Chrome, no Flow submit) |
| Scope | WORKPLAN §5.2 Tests **5** (INV-1 profile pinning), **6** (INV-4 project lock), **7** (stale recovery) |
| Branch | `claude/epic-euclid-39ebf9` off `master` @ `159a6a0` |
| Test file | `tests/test_e2e_invariants.py` (NEW — 5 cases, 270 lines) |
| Session report | [`docs/session-reports/2026-04-19_tests_5-6-7_infra.md`](session-reports/2026-04-19_tests_5-6-7_infra.md) |

### Why Tier 1.5 and not Tier 2

These three WORKPLAN §5.2 tests assert **scheduler / persistence** behaviour, not Flow-UI interaction. The contracts live entirely inside `server/db/job_store.py::claim_next_job` and `::recover_stale_jobs`. A real Flow submit would only add latency + LP consumption without proving anything extra — the predicates are SQL, and `tests/conftest.py` already provides a temp-SQLite `db` fixture (B9). So: integration tests against the real DB layer, with `FlowClient` and `worker/dispatcher.py` deliberately not invoked.

### Per-test verdict

| # | Test | Invariant | Verdict | Evidence |
|---|---|---|---|---|
| 5 | `test_5_profile_pinning_l2_claim_respects_profile_list` | INV-1 (account binding) | ✅ PASS | Worker B with `['p2']` returns `None` on `claim_next_job`; Worker A with `['p1']` claims the L2 child whose parent ran on `p1` — `parent.profile IN (...)` predicate filters correctly. |
| 5b | `test_5_profile_pinning_l1_with_null_profile_claimable_by_any` | INV-1 counter-case | ✅ PASS | Blast-radius guard: fresh L1 with `profile IS NULL` is claimable by any worker — otherwise first-run t2v would deadlock forever. |
| 6 | `test_6_project_lock_serialises_two_l2_on_same_project_url` | INV-4 (serial per project_url) | ✅ PASS | Two L2 sharing `project_url`: first claim flips row to `'claimed'`, second claim returns `None` via NOT EXISTS subquery; after `update_job(..., status=COMPLETED)` on first, second claim succeeds on the next call. |
| 7 | `test_7_stale_recovery_resets_claimed_and_reopens_for_claim` | Stale recovery | ✅ PASS | Backdated `updated_at` to `now - 40m` via direct SQL (prod path never writes past timestamps; test-only plumbing). `recover_stale_jobs(stale_minutes=30)` returned exactly 1 row, reset to `pending`, `worker_id=NULL`, `claimed_at=NULL`, error breadcrumb set; re-claim by a fresh worker succeeded. |
| 7b | `test_7_stale_recovery_skips_fresh_claims` | Recovery safety | ✅ PASS | Blast-radius guard: a fresh claim survives the recovery call — `recover_stale_jobs` is a filtered reset, not a nuke. |

### Suite totals

| Metric | Before | After |
|---|---|---|
| Tests collected | 95 | **100** |
| Pass | 95 | **100** |
| Fail / skip / error | 0 | 0 |
| DeprecationWarning (under `-W error::DeprecationWarning`) | 0 | 0 |
| Runtime | ~7.8s | ~8.2s |

Full suite command: `python -W error::DeprecationWarning -m pytest tests/` → `100 passed in 8.16s`.

### Blast radius (what did NOT change)

- Zero `.py` diff in `flow/`, `server/`, `worker/` — `git status --short` shows only the new test file + two `.md` updates (this log + the session report).
- No SPEC §D.4 strike-through — this session adds coverage, does not close a B-numbered bug.
- No change to `pytest.ini`, `tests/conftest.py`, or any fixture.

### Remaining §5.2 coverage

| §5.2 Test | Status | Covered by |
|---|---|---|
| Test 1 — single t2v (B1 aspect) | ✅ | Tier-2 Run 10 (J1) + `tests/test_aspect_ratio.py` |
| Test 2 — 4-step chain | partial | Tier-2 Run 10 is 3-step (t2v → camera → insert); 4-step `+ extend` not yet exercised in live chain but individual extend covered by `tests/test_extend.py` |
| Test 3 — bbox edge cases (B2) | ✅ | Tier-2 Run 10 (J3) + `tests/test_bbox.py` |
| Test 4 — 3 camera presets (B3) | partial | Tier-2 Run 10 covered `"Dolly in"` only; `"Orbit left"` / `"Low"` need live exercise |
| **Test 5 — profile pinning** | ✅ **this session** | `tests/test_e2e_invariants.py::test_5_*` |
| **Test 6 — project lock** | ✅ **this session** | `tests/test_e2e_invariants.py::test_6_*` |
| **Test 7 — stale recovery** | ✅ **this session** | `tests/test_e2e_invariants.py::test_7_*` |

---

## Tier 2 — 2026-04-19 — Run 10 — ✅ **FULL 3-JOB CHAIN PASS** (B1 + B11 + B12 cross-locale verified; incidentally landed B27 engine simplification)

| Field | Value |
|---|---|
| Date | 2026-04-19 ~03:30-03:42 local (Run 10.b PASS; Run 10.a blocked pre-language-switch) |
| Tier | 2 — full engine-driven chain via REST API |
| Profile | `ngoctuandt20` (Google account was VI-locale at session start → switched to English at `myaccount.google.com/language` mid-session per `feedback_english_locale.md` memory) |
| Chain type | 3-job: t2v (9:16) → camera-move (Dolly in) → insert-object (bbox 0.10/0.10/0.20/0.20, "a small bird") |
| Chain id | `72160591-d2bb-4731-8096-1a48a45c6ef2` |
| Commits under test | B18 `8dc357c` + B19 `e1597b2` + B22 `0637c92` + B23 `caef3e9` + B24 `004d8fb` + B26 `d4fca1a` + B20-final `0aa01b8` |
| Session report | [`docs/session-reports/2026-04-19_Tier2_Run10_VI_final.md`](session-reports/2026-04-19_Tier2_Run10_VI_final.md) |

### Per-job verdict

| # | Job | Target bug | Status | Completion evidence |
|---|---|---|---|---|
| 1 | `text-to-video` aspect=9:16, "a fluffy cat chasing a butterfly in sunlit meadow" | B1 / B18 / B19 | ✅ `completed` @ 2026-04-18T20:34:15Z | `media_id=5920c395-465d-4970-b22e-5c5359a3c147`, `project_url=https://labs.google/fx/tools/flow/project/dbb990c0-7d75-41f4-b7c9-21870bf3b190`, output `downloads\t2v_720p_1776544454.mp4` |
| 2 | `camera-move` direction="Dolly in" | B12 preset + B22 L2 inherit | ✅ `completed` @ 2026-04-18T20:36:08Z | new `media_id=e219fc6c-ee61-4a42-a1b7-731e9f95ae53` (Flow mints new media on camera-move — see INV-5 discovery below), output `downloads\cam_720p_1776544567.mp4`. B22 inheritance: J2 claimed with J1's `project_url` + `media_id` + `edit_url` populated. |
| 3 | `insert-object` bbox={0.10, 0.10, 0.20, 0.20}, "a small bird" | B11 canvas bbox + B22 L3 inherit | ✅ `completed` @ 2026-04-18T20:37:55Z | `media_id=e219fc6c-…` (preserved from J2 — insert-object does NOT mint new media), output `downloads\ins_720p_1776544675.mp4`. B11 worker log: `Drew bbox on canvas: x=0.10 y=0.10 w=0.20 h=0.20 canvas=390x694`. |

### Outcome — PASS (full 3-job browser chain)

Run 10 is the **first full 3-job chain to reach terminal state on all three L1 + L2 + L2 operations** in Tier 2 — Phase A baseline. Verifies B1 / B11 / B12 in live chain-context (not just the isolated Tier 1 DOM probes from 2026-04-17), and verifies B22 inheritance + B26 exact-text selectors hold under back-to-back L2 navigations.

### Run 10.a blocker → Run 10.b path

Run 10.a (first attempt on VI-locale Google account) blocked at J2 with `RuntimeError("Failed to enter edit mode")`. Isolated via `scripts/probe_nav_direct.py`: Flow's SPA redirects `/fx/tools/flow/project/{id}` → `/fx/vi/tools/flow/project/{id}` on VI-locale accounts AND strips `/edit/{media_id}` segment on direct goto AND renders Next.js catch-all placeholder on EN-URL direct goto. All three are SPA-level, not engine-selector, so code-level fix is impractical (locale-conditional URL handling everywhere).

Supervisor flipped `ngoctuandt20@gmail.com`'s Preferred Language to English at `myaccount.google.com/language` (Google Account-level setting, not per-Chrome-profile). Run 10.b then produced the PASS above. Saved as `feedback_english_locale.md` memory — future Flow-onboarding operators must switch account language to EN before first engine run.

### Invariants observed

| Invariant | Status | Evidence |
|---|---|---|
| INV-1 Account Binding | ✅ | All 3 jobs `profile=ngoctuandt20` |
| INV-2 Navigate by `edit_url` | ✅ | `navigate_to_edit(job)` used `project_url` + `media_id` → built `edit_url`; no `video_index` |
| INV-3 Store Everything | ✅ | Each job stored `project_url` + `media_id` + `edit_url` + output file post-completion; B22 claim-time propagation populated L2/L3 on claim |
| INV-4 Serial per Project | ✅ | J1 → J2 → J3 ran sequentially (ProjectLock path) |
| INV-5 `media_id` stable | ⚠️ | J1 `5920c395` → J2 `e219fc6c` (Flow minted new media on camera-move) → J3 preserved `e219fc6c`. Pre-existing Flow-SPA behavior; engine handles via `finalize_operation` re-extract. Flagged for SPEC wording revision — see session report §7 |
| R-CODE-3 Locale-Independent | ✅ | All selectors (B18 `add_2`, B19 `crop_9_16`, B26 `arrow_forward`, B12 computed-color) locale-agnostic; VI blocker was Flow-SPA URL rewrite, not engine code |

### Probe-driven engine simplification (B27 landed mid-session)

After Run 10.b PASS, supervisor requested probing direct `page.goto(edit_url)` on the now-EN profile. `scripts/probe_direct_edit_url.py` v2 confirms direct goto lands on the rendered editor (submit chip `arrow_forward` + Veo model chip + textarea all present, no homepage bounce, `/edit/` URL preserved). v1 of the probe reported FAIL — that verdict was false-positive on a naïve `"[...catchAll]"` string match in raw HTML; v2 checks real editor DOM signals.

Based on probe v2 evidence, `flow/operations/_base.py::navigate_to_edit` updated: `target_url = edit_url_val` (was `project_url_val or edit_url_val`) — direct `goto(edit_url)` is the fast path; existing `_click_video_tile` fallback block remains defensive. Saves one pageload + 3s sleep per L2+ operation. Tests `tests/test_base.py` +2 cases (primary-goto trip-wire + fallback path). Full suite 95 pass.

See SPEC.md §D.4 B27 for the complete code / test / rationale entry.

---

## Tier 1.5 — 2026-04-18 — Run 9 — ✅ **B22 FIX VERIFIED AGAINST LIVE RUN-8 DB** (DB-layer)

| Field | Value |
|---|---|
| Date | 2026-04-18 ~08:45 UTC (~15:45 local) |
| Tier | 1.5 — DB-layer live-validation (not full-browser Tier 2; see Outcome) |
| Profile | `ngoctuandt20` (same account as Run 8) |
| Source DB | `.claude/worktrees/gallant-jang-cbe036/data/flowengine.db` (read-only snapshot of Run-8 state) |
| Fix under test | B22 commit on branch `claude/elated-edison-a7ac87` (this worktree) |
| Target row | L2 `8ffc308a-…` (camera-move Dolly in — the exact job that failed in Run 8) |
| Parent row | L1 `6bdcadd7-…` (text-to-video completed in Run 8 with real `project_url` + `media_id`) |
| Session report | [`docs/session-reports/2026-04-18_B22_l2-inheritance.md`](session-reports/2026-04-18_B22_l2-inheritance.md) |

### Method

1. Snapshot the Run-8 DB to a temp path (read-only on source).
2. Load MY worktree's `server.db.job_store.claim_next_job` (B22-fixed) with `DATABASE_PATH` re-pointed at the snapshot.
3. Reset L2 `8ffc308a-…` to `pending` with `project_url` / `media_id` / `edit_url` all NULL — reproduces the exact Run-8 failure state.
4. Invoke `claim_next_job("run9-db-probe", ["ngoctuandt20"])`.
5. Read back the child row and compare against the parent's values.

### Result

```
Parent L1 6bdcadd7 (completed):
  project_url = https://labs.google/fx/tools/flow/project/bf4c75fa-e039-43bb-b994-bf7d6373138e
  media_id    = 03fe613e-988d-4f29-b0b1-3d0603c916a1
  edit_url    = https://labs.google/fx/tools/flow/project/bf4c75fa-…/edit/03fe613e-…
  profile     = ngoctuandt20

Before B22 claim (L2 reset to pending):
  status=pending  project_url=None  media_id=None  edit_url=None

After B22 claim (L2 8ffc308a):
  status=claimed  profile=ngoctuandt20
  project_url=https://labs.google/fx/tools/flow/project/bf4c75fa-e039-43bb-b994-bf7d6373138e
  media_id=03fe613e-988d-4f29-b0b1-3d0603c916a1
  edit_url=https://labs.google/fx/tools/flow/project/bf4c75fa-…/edit/03fe613e-…
```

All 3 target fields now populated on the child row — exactly what `worker/dispatcher.py` + `flow/operations/_base.py::navigate_to_edit(job)` need. Pre-B22 Run 8 had all 3 NULL, which was the sole blocker.

### Invariants observed

| Invariant | Status | Evidence |
|---|---|---|
| INV-1 Account Binding | ✓ | `profile=ngoctuandt20` inherited from parent L1 |
| INV-2 Navigate by `edit_url` | ✓ **UNBLOCKED** | `edit_url` now populated on the child row — worker has a target |
| INV-3 Store Everything | ✓ **UNBLOCKED** | `project_url` + `media_id` + `edit_url` + `profile` all persisted on claim (claim-time propagation per SPEC.md §A.1 INV-3 B22 note) |
| INV-4 Serial per Project | ✓ unchanged | Existing project-lock logic preserved — B22 only adds fields to the same UPDATE |
| INV-5 `media_id` stable | ✓ | Child `media_id` == parent `media_id` (`03fe613e-…`) |

### Outcome — PASS (DB-layer) / DEFERRED (full browser J1→J2→J3)

**PASS**: B22 fix verified against real Run-8 DB state using the exact parent/child rows that failed in Run 8. The ONE thing B22 changes (claim-time field propagation) works correctly against authentic live data — not just synthetic fixtures.

**DEFERRED (full browser chain retry)**: Not executed because:
1. The sibling worktree `gallant-jang-cbe036` has its engine process running (PID 49360 server + PID 47656 worker on port 8080 + `CHROME_USER_DATA_DIR=D:/AI/chrome-profiles`) — stopping it would interrupt that session's open context.
2. A full J1→J2→J3 chain requires a real Chrome run against real Flow that consumes live LP credits on `ngoctuandt20` (10–15 min, 3 video generations).
3. B22 is strictly a DB-layer change. The worker / `navigate_to_edit` / `camera.run_camera` / `insert.run_insert` code is **unchanged**. Live DB validation proves the fix populates the fields those callers already know how to use.

Proposed supervisor action: run a standalone Tier 2 Run 9 (full Chrome chain) after this branch merges, when the sibling worktree's engine can be cleanly stopped. Success criteria: J2 (camera-move) reaches `completed` validating B12, J3 (insert-object) reaches `completed` validating B11.

### Unit-test coverage (complementary)

`tests/test_claim_algorithm.py` adds 4 cases (B22 regression guards):
- `test_l2_claim_inherits_project_url_media_id_edit_url` — core contract (RED→GREEN against pre-B22 code).
- `test_l2_claim_overwrites_child_fields_from_parent` — parent-wins-on-overwrite (single source of truth).
- `test_l1_claim_does_not_inherit_anything` — L1 fresh-claim branch untouched (blast-radius guard).
- `test_l2_claim_inherits_when_parent_edit_url_null` — pure NULL-preserving propagation, no synthesis.

Full-suite: 93 pass (was 89 + 4 new) under `python -W error::DeprecationWarning -m pytest tests/`.

---

## Tier 2 — 2026-04-18 — Run 8 — ⚠️ **PARTIAL** (B19 fix holds end-to-end on J1; J2/J3 expose independent L2 inheritance gap — out of B19 scope)

| Field | Value |
|---|---|
| Date | 2026-04-18 ~07:40 UTC (~14:40 local) |
| Profile | `ngoctuandt20` |
| Chain type | 3-job (t2v 9:16 → camera Dolly in → insert bbox) |
| B19 commit under test | `e1597b2` (this branch — `claude/gallant-jang-cbe036`) |
| Session report | [`docs/session-reports/2026-04-18_B19_aspect-chip-multiline.md`](session-reports/2026-04-18_B19_aspect-chip-multiline.md) |

### Per-job verdict

| # | Job | Target bug | Status | Verdict |
|---|---|---|---|---|
| 1 | text-to-video `9:16` | B1 aspect (via B19 fix) | `completed` | ✅ B19 two-part fix holds in chain context: icon-ligature selector matched `crop_9_16` + pre-open guard correctly skipped chip click when `data-state="open"`. Persisted `project_url=https://labs.google/fx/tools/flow/project/bf4c75fa-e039-43bb-b994-bf7d6373138e` + `media_id=03fe613e-988d-4f29-b0b1-3d0603c916a1`. |
| 2 | camera-move `Dolly in` | B12 preset verify | `failed` | **Independent L2 inheritance gap (NOT B19).** Worker raised `Cannot navigate: no edit_url, project_url=, media_id=` — server's `claim_next_job` (`server/db/job_store.py`) currently inherits only `profile` from parent, NOT `project_url` / `media_id`. |
| 3 | insert-object bbox | B11 canvas drag | `pending` | Not reached — parent J2 failed. |

### Outcome

B19 fix (two-part) landed cleanly. B1 end-to-end **unblocked** in chain context. The downstream L2 inheritance bug is pre-existing (predates B19) and surfaces only once a chain gets past J1 — it was masked in Phase A Tier 1 because Tier 1 jobs were exercised individually, and masked in Tier 2 Runs 1-6 because no chain ever reached J2. Proposed **B22 (P0)**: extend `claim_next_job` to also inherit `project_url` + `media_id` from parent when L2+ job is claimed.

---

## Tier 2 — 2026-04-18 — Run 7 — ✅ **B19 FIX VERIFIED LIVE (single job)**

| Field | Value |
|---|---|
| Date | 2026-04-18 ~07:30 UTC (~14:30 local) |
| Profile | `ngoctuandt20` |
| Job type | single `text-to-video` (aspect 9:16) |
| B19 commit under test | `e1597b2` |
| Session report | [`docs/session-reports/2026-04-18_B19_aspect-chip-multiline.md`](session-reports/2026-04-18_B19_aspect-chip-multiline.md) |

### Verdict: ✅ PASS

First full green run of the aspect-ratio code path after B19 fix v3 landed. Engine output:
- Chip located via icon selector: `button[aria-haspopup="menu"]:has-text("crop_9_16")` matched directly (bypassing model-name text that was `"🍌 Nano Banana Pro\ncrop_9_16\nx1"`).
- Pre-click `get_attribute("data-state")` returned `"open"` — engine SKIPPED `chip.click()` per B19 guard and fell through to `wait_for("[role=\"menu\"][data-state=\"open\"]")` which resolved immediately.
- Portrait trigger clicked, chip verified `crop_9_16`, submit succeeded.
- Persisted: `project_url=https://labs.google/fx/tools/flow/project/f656f223-7e65-4309-bc34-cd39e9b3da24`, `media_id=f2f736d2-5094-4bdb-abc6-d4f8ed254ccb`.

### Invariants observed

| Invariant | Status | Evidence |
|---|---|---|
| INV-1 Account Binding | ✓ | `profile=ngoctuandt20` on J1 claim + completion |
| INV-3 Store Everything | ✓ | `project_url` + `media_id` persisted |
| R-CODE-3 Locale-Independent | ✓ | Icon ligature `crop_9_16` matches across models/locales |
| R-CC-1 No architecture restructure | ✓ | Single-function patch in `_set_aspect_ratio` |

---

## Tier 2 — 2026-04-18 — Run 6 — ❌ BLOCKED (live DOM diag — real root cause surfaced)

| Field | Value |
|---|---|
| Date | 2026-04-18 ~07:10 UTC |
| Profile | `ngoctuandt20` |
| Fix version | v2 (CSS `:has-text`, no state guard yet) — + temporary `DIAG aspect chip:` log line |
| Verdict | Same `Locator.wait_for: Timeout 3000ms` symptom, but diag log exposed true cause |

### Diagnostic output (critical finding)

```
DIAG aspect chip: { exists: true, dataState: 'open', innerText: '🍌 Nano Banana Pro\ncrop_9_16\nx1' }
```

Two facts that flipped B19's hypothesis from v1/v2 to v3:
1. **Chip text is NOT `"Video"`** — default model on this account is `"🍌 Nano Banana Pro"`. Pre-B19 regex `r"video.*x\d"` matched nothing.
2. **`data-state="open"` BEFORE `_set_aspect_ratio` called** — a prior interaction (likely `flow/model_selector.py::_open_model_dropdown` which uses `button:has-text('Video')` — same substring match as the chip's old-DOM label) left the aspect chip's Radix trigger pre-open. Unconditional `chip.click()` then TOGGLED CLOSED → subsequent `wait_for` timed out.

This run is the pivot: from "regex multi-line" (wrong hypothesis) to "text probe wrong + pre-open state" (real hypothesis). Triggered fix v3 (icon-ligature selector + state guard) → Run 7 ✅.

---

## Tier 2 — 2026-04-18 — Runs 4 + 5 — ❌ BLOCKED (fix v1/v2 still fail)

| Run | Fix version | Selector form | Verdict |
|---|---|---|---|
| 4 | v1 | `button:has(i.google-symbols:has-text(/crop_(9_16|16_9)/))` (nested `has=` with regex) | ❌ same timeout — selector resolved correctly in Playwright's eyes but click-toggle effect still closed the menu |
| 5 | v2 | `button[aria-haspopup="menu"]:has-text("crop_9_16"), …:has-text("crop_16_9")` (CSS `:has-text`, simpler form) | ❌ same timeout — simpler selector, same behavior |

**Lesson:** whichever selector resolved the chip, the `.click()` call happened on a trigger that was already open → toggle-closed the menu. Selector-only fixes could not succeed without a pre-open state check.

---

## Tier 2 — 2026-04-18 — Run 3 — ❌ BLOCKED (wrong hypothesis: `re.DOTALL`)

| Field | Value |
|---|---|
| Date | 2026-04-18 ~06:30 UTC |
| Fix v0 | `re.compile(r"video.*x\d", re.IGNORECASE \| re.DOTALL)` — added `re.DOTALL` flag so `.` crosses `\n` |
| Verdict | ❌ Same `Locator.wait_for: Timeout 3000ms` — DOTALL didn't help |

### Why fix v0 failed

Initial hypothesis was that chip `innerText` is `"Video\ncrop_9_16\nx1"` (multi-line) and regex `video.*x\d` needed `re.DOTALL` to cross the newlines. Unit-test-green (pattern matches multi-line string), but live run showed the **actual chip text did not start with `"Video"` at all** — default model had been switched to `"🍌 Nano Banana Pro"` since Phase A Tier 1 tag `db4c746`. Even with `DOTALL`, the `video` token was absent. Ran 1-line fix live → identical failure symptom → triggered Chrome MCP live DOM probe that surfaced the real root cause (Run 6).

---

## Tier 2 — 2026-04-18 — Run 2 — ⚠️ **PARTIAL** (B18 PASS, new B19 candidate blocker)

| Field | Value |
|---|---|
| Date | 2026-04-18 05:21 UTC (12:21 local) |
| Profile | `ngoctuandt20` (ULTRA tier — unchanged from Run 1) |
| Chain IDs | 2 sequential retries (both halted at same downstream point — first attempt + post-login re-click) |
| Jobs per chain | 3 (t2v 9:16 → camera Dolly in → insert bbox seagull) |
| LP consumed | 0 |
| Supervisor commit | `e618731` (master — pre-B18) |
| B18 commit under test | `8dc357c` (worktree `claude/brave-villani-73e607`) |
| Session report | [`docs/session-reports/2026-04-18_B18_homepage-locale-fix.md`](session-reports/2026-04-18_B18_homepage-locale-fix.md) |

### Per-job verdict

| # | Job | Target bug | Status | Verdict |
|---|---|---|---|---|
| 1 | text-to-video `9:16` | B1 aspect ratio | `failed` | **B18 PASS live** (homepage button clicked twice via icon selector, 2 projects created). **B19 candidate FAIL** (aspect-ratio chip panel never opens `[role="menu"][data-state="open"]`). |
| 2 | camera-move `Dolly in` | B12 preset verify | `pending` | Not reached — parent J1 failed at B19 candidate |
| 3 | insert-object bbox | B11 canvas drag | `pending` | Not reached — parent J2 never ran |

### B18 verification evidence (LIVE — ✅ PASS)

```
flow.operations.generate: Clicked new project via: button:has(i.google-symbols):has-text('add_2')
```

Same log line emitted on BOTH the initial attempt (before login re-check) AND the post-login re-click loop — proves the module-level `NEW_PROJECT_SELECTORS` constant is shared across both paths as contract-tested. Engine successfully transitioned from `https://labs.google/fx/tools/flow` → `/project/cf20a347-…/edit/...` (attempt 1) and again `/project/82fa5465-…/edit/...` (attempt 2). Pre-B18 this transition never happened — `RuntimeError("Failed to find '+ New project' button on Flow homepage")` fired at `generate.py:125` every time.

### Downstream blocker (NEW — B19 candidate, OUT OF B18 SCOPE)

```
error: Locator.wait_for: Timeout 3000ms exceeded.
       waiting for locator("[role=\"menu\"][data-state=\"open\"]")
```

Triggered at the aspect-ratio chip panel step. Chrome MCP DOM probe on the failing editor page (`/edit/82fa5465-…`) found:
- 6 `button[aria-haspopup="menu"]` buttons on the editor toolbar.
- The target chip (aspect) at y=599 carries multi-line text: `"Video\ncrop_9_16\nx1"` (newlines between tokens).
- Suspected root cause: B1's regex `re.compile(r"video.*x\d", re.IGNORECASE)` in `flow/operations/generate.py` lacks `re.DOTALL` — `.` does not match `\n`, so the chip is never found and a wrong (or no) click occurs, leaving the Radix menu closed.

### Invariants observed

| Invariant | Status | Evidence |
|---|---|---|
| INV-1 Account Binding | ✓ honored | `profile=ngoctuandt20` claimed both retries under same worker |
| INV-2 Navigate by `edit_url` | n/a | No L2+ nav |
| INV-3 Store Everything | partial | J1 failed pre-submit; `project_url` created client-side twice, not persisted (failed before L2+) |
| INV-4 / INV-5 | n/a | Chain halted pre-submit |
| **R-CODE-3 Locale-Independent** | ✓ **RESTORED** | B18 selector matches VI + EN via `add_2` icon ligature |
| R-CODE-10 No `datetime.utcnow()` | ✓ | Unchanged from Run 1 |
| B5 auto `completed_at` | ✓ incidental | Both J1 failures auto-stamped `completed_at` |
| B6 profile release | ✓ incidental | `ngoctuandt20` marked AVAILABLE after each terminal status |
| B4 chain aggregate | ✓ incidental | `status=failed` (rule #1) on both retries |

### Next action

B18 (homepage locale) is closed. Blocker moves to **B19 candidate — aspect-ratio chip regex/selector**. Propose:

1. **B19** — multi-line chip text breaks `re.compile(r"video.*x\d", re.IGNORECASE)`. Add `re.DOTALL` or switch to `[\s\S]*`; alternatively select by `aria-haspopup="menu"` + label sibling. P0 for any T2V. DOM probe session needed.
2. **B-stdout-encoding** (carried from Run 1, P2) — still open.

Until B19 lands, Tier 2 still cannot exercise B1 (aspect verify), B11 (bbox canvas), or B12 (camera preset) code paths on any profile. B18 alone was necessary but not sufficient to complete Tier 2.

---

## Tier 2 — 2026-04-18 — Run 1 — ⚠️ **BLOCKED**

| Field | Value |
|---|---|
| Date | 2026-04-18 04:51 UTC |
| Profile | `ngoctuandt20` (ULTRA tier — confirmed via page text) |
| Chain ID | `cd8ec66b-348f-4f49-a964-d1d11f5ca767` |
| Jobs | 3 (t2v 9:16 → camera Dolly in → insert bbox seagull) |
| LP consumed | 0 |
| Supervisor commit | `b80cc05` (master) |
| Session report | [`docs/session-reports/2026-04-18_Tier2_e2e-live.md`](session-reports/2026-04-18_Tier2_e2e-live.md) |

### Per-job verdict

| # | Job | Target bug | Job ID | Status | Verdict |
|---|---|---|---|---|---|
| 1 | text-to-video `9:16` | B1 aspect ratio | `9314caf4-…` | `failed` (21s) | Not reached — halted pre-aspect-ratio at Flow homepage button |
| 2 | camera-move `Dolly in` | B12 preset verify | `787cd278-…` | `pending` (never claimed) | Not reached — parent J1 failed |
| 3 | insert-object bbox | B11 canvas drag | `17e525e8-…` | `pending` (never claimed) | Not reached — parent J2 never ran |

### Root cause

`flow/operations/generate.py:125` raised `RuntimeError: Failed to find '+ New project' button on Flow homepage`.

Account **is** logged in (page text shows `ULTRA` tier + existing projects with dated edit/delete buttons) and LP **is** available (pre-run user confirmation: >3 slots). Flow homepage rendered Vietnamese despite engine appending `?locale=en`:

> `ULTRA / Apr 16, 08:49 PM / edit / Chỉnh sửa dự án / delete / Xoá dự án / …`

The English "+ New project" button locator misses the Vietnamese "Dự án mới" entry point — direct violation of **R-CODE-3 Locale-Independent** in `SPEC.md`.

### Invariants observed

| Invariant | Status | Evidence |
|---|---|---|
| INV-1 Account Binding | ✓ honored | Chain payload `profile=ngoctuandt20` → all 3 job rows stored that profile; J1 claim log shows `profile=ngoctuandt20` on `worker-1` |
| INV-2 Navigate by `edit_url` | n/a | No L2+ navigation occurred |
| INV-3 Store Everything | ✓ (vacuous) | J1 failed pre-submit → `project_url`/`media_id` stayed `null` (correct) |
| INV-4 Serial per Project | n/a | No project was created |
| INV-5 `media_id` stable | n/a | Never allocated |
| R-CODE-3 Locale-Independent | ❌ **VIOLATION** | Root cause of this BLOCKED run |
| R-CODE-10 No `datetime.utcnow()` | ✓ | All API timestamps ISO-8601 UTC with `Z` suffix |
| B5 auto `completed_at` | ✓ incidental | J1 `completed_at=2026-04-18T04:52:08.455557Z` after failure |
| B6 profile release | ✓ incidental | `Profile ngoctuandt20 marked AVAILABLE` log after J1 failure |
| B4 chain aggregate | ✓ incidental | `GET /api/chains/{id}` → `status=failed` (rule #1: any failed → failed), `progress.completed=0/3` |

### Next action

Blocked on a new P0 for non-English Google accounts. Proposed follow-up:

1. **B18 (propose)** — locale-independent Flow homepage new-project locator. Requires DOM probe session on `ngoctuandt20`. See session report §7 for fix-direction candidates.
2. **B-stdout-encoding (P2)** — Windows `cp1252` stdout encoder crashes on Vietnamese diagnostics. Inline `PYTHONIOENCODING=utf-8` or `sys.stdout.reconfigure(...)` in worker bootstrap.

Until B18 lands, Tier 2 cannot exercise any B1/B11/B12 code path on a non-English Google account. A rerun on an English-locale account (if one is available in the profile pool) might unblock B1/B11/B12 validation independently.

---

## Tier 1 — 2026-04-17 — Round 2 — ✅ PASS

`docs/session-reports/2026-04-17_Tier1r2_revalidation.md` — B11 canvas selector and B12 `getComputedStyle` verify both re-probed live on project `785d2255-…/edit/f1994aba-…`. Threshold margins: bbox canvas 479×269 (pass ≥300×200); camera color sum 144 vs 765 (pass <400). Evidence recorded in SPEC.md §D.4 B11/B12.

## Tier 1 — 2026-04-17 — Round 1 — ⚠️ B2/B3 flipped

`docs/session-reports/2026-04-17_Tier1_dom-validation.md` — revealed B2 and B3 initial fixes targeted non-existent DOM elements. Spawned B11 and B12 as supersessions.

---

_Maintained per WORKPLAN §5.3 — append new attempts at the top._
