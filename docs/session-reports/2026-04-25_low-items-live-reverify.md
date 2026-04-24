# Session — 2026-04-25: LOW-items live re-verify

**Profile:** `ngoctuandt20` (Ultra)
**Worker master head at run time:** pre-#52 (existing long-running worker PID 307377)
**Parent L1 jobs:** `73a9eb13` (mid `ca131268`, project `bed96`), `fc625824` (mid `3e8094ec`, project `afb006`)

## TL;DR

Re-verified 3 of the 5 LOW-priority parked items from `2026-04-20_session-handoff.md §8`.
2 verified green live, 1 still needs a worker env change, 2 skipped as non-issues.

| Item | Status | Evidence |
|---|---|---|
| `.jpeg` → `.jpg` rewrite | SKIP — non-issue | `image/jpg` isn't a standard MIME; `.jpg` is correct; `flow/upscale.py:313-314` already preserves `.jpeg` when suggested filename says so |
| `_BUSY_RE` broadening | SKIP — speculative | No live evidence Flow changed busy/done copy |
| Extend-video multi-generation | ✅ VERIFIED | Job `ed0a607e`: 2 distinct 720p files saved (`ext_720p_1777054017.mp4` 9.3 MB + `ext_720p_1777054024.mp4` 9.2 MB). PR #28's iterate-all pattern covers extend too. |
| Camera-move L2 direct off L1 | ✅ VERIFIED | Job `6a7c81af`: parent mid `3e8094ec` → new mid `bfa79f70` (mints NEW on early-chain, matches SPEC INV-5). File `cam_1080p_1777054154.mp4` 32.8 MB. |
| Async busy/done toast fallback | ✅ VERIFIED (part 2) | See "Part 2" below — 3 back-to-back 4K t2i jobs all hit `Upscaling in progress... → Complete` state trail (15-19s each). |

## Observations

- **Extend UI 1080p path fell through to 720p API fallback** for both segments. This is working-as-designed (`flow/download.py:116` "Video quality downgraded from 1080p to 720p") — not a new regression, matches historical pattern (`t2v_720p_*` files from prior sessions in `pr3-agent/downloads/`). Camera-move succeeded at 1080p on same profile in the same run, so the fallback is extend-UI-specific, not account/session-wide.
- **Worker PID 307377** was started earlier today (Apr 24); does not contain PR #52 browser-pool or PR #49 window-geometry changes. L2 probes still pass on it because the 2026-04-23 media_id fix (`0bb9d29`) landed before that worker launched.

---

## Part 2 — #49 window geometry + 4K toast (added 01:22–01:30)

Worker restarted with `FLOW_WINDOW_SIZE=810x700 FLOW_WINDOW_POSITION=1750,0 FLOW_IMAGE_QUALITY=4k FLOW_USE_BASE_PROFILE=1 WORKER_PROFILES=ngoctuandt20`.

### #49 Chrome window geometry ✅ VERIFIED (cmdline)

Chrome process cmdline (via `wmic`):

```
chrome.exe --user-data-dir=D:\AI\FlowEngine\chrome-profiles\ngoctuandt20
  --remote-debugging-port=19300 --no-first-run --no-default-browser-check
  --new-window --window-size=810,700 --window-position=1750,0
```

Env vars propagated to `--window-size` + `--window-position` flags correctly. L1 t2v job `75f492c5` completed cleanly (`t2v_1080p_1777055132.mp4`, 20.9 MB, mid `6160c960`). No "Ignoring invalid" warnings in log.

**Visual dock-position confirmation still requires the user's eye** — flags-applied is code-verified; actual on-screen position is OS-level + user-screen-geometry-dependent.

### Image 4K busy/done toast state machine ✅ VERIFIED (3/3)

Three back-to-back t2i jobs with distinct prompts, all hit the `busy → _wait_upscale → Complete` branch (NOT the cached/immediate-done branch that dominated the 2026-04-20 session). Log pattern per job:

```
[UPSCALE][IMAGE] Attempt 1/2 for 4k
[UPSCALE][IMAGE][4k] menu items: ['1K\nOriginal size', '2K\nUpscaled', '4K\nUpscaled']
[UPSCALE][IMAGE] Clicked 4k item with regex ^4K\s*Upscaled$
[UPSCALE] Upscaling in progress...
[UPSCALE] Complete (Ns)
[UPSCALE] Saved image: downloads\t2i_4k_<ts>.jpeg (<bytes>)
```

| Job | Wait | File | Size |
|---|---|---|---|
| `e398d686` | 18s | `t2i_4k_1777055237.jpeg` | 9.6 MB |
| `46378473` | 19s | `t2i_4k_1777055338.jpeg` | 11.7 MB |
| `d269458e` | 15s | `t2i_4k_1777055439.jpeg` | 9.8 MB |

Confirms:

- PR #24 async state machine works end-to-end on Flow's real busy-state response (no longer the 4K-always-immediate case from 2026-04-20 §4).
- PR #26 composer-chip selector path still matches Flow's current menu layout.
- `.jpeg` extension **IS preserved** (not rewritten to `.jpg`) when Flow's `download.suggested_filename` is `.jpeg` — the upscale-path preservation check at `flow/upscale.py:313-314` works correctly. The 2026-04-20 note "`.jpeg` is rewritten to `.jpg` (not preserved)" applies only to the non-upscale API-fallback path in `flow/download.py:_extension_for`, not to upscaled image downloads.

**Branches still NOT live-exercised** (defensive, unit-tested only):
- `done`-state-immediately (cached) — all 3 hit busy first.
- `failed`-state retry.
- Exhausted-attempts fallback to API original.

## Credit tally (self-reported, verify vs Flow account)

This session's live runs — 2 worker restarts, 2 batches:

| Phase | Job | Type | Output | Notes |
|---|---|---|---|---|
| Part 1 | `ed0a607e` | L2 extend | 2× 720p video (9.3 + 9.2 MB) | Multi-gen iterate |
| Part 1 | `6a7c81af` | L2 camera-move | 1× 1080p video (32.8 MB) | Early-chain off L1 |
| Part 1 | `dac476fd` | t2i | 1× original .jpg (1.1 MB) | No upscale |
| Part 2 | `75f492c5` | L1 t2v | 1× 1080p video (20.9 MB) | 1080p-upscale (no 4K video cost) |
| Part 2 | `e398d686` | t2i + 4K | 1× .jpeg (9.6 MB) | Image 4K upscale |
| Part 2 | `46378473` | t2i + 4K | 1× .jpeg (11.7 MB) | Image 4K upscale |
| Part 2 | `d269458e` | t2i + 4K | 1× .jpeg (9.8 MB) | Image 4K upscale |

**Generations total: 8** (2 extend segments + 1 camera + 4 images + 1 t2v). Flow per-op credit rates aren't hard-coded in this repo; consult Flow account ledger for exact draw. Per CLAUDE.md §7, 4K **video** upscale = 50 credits (avoided this session); image 4K rate unspecified in repo — treat as chargeable.

## Confirmed live-clean (this run)

- L2 `media_id` extraction (PR #30 fix + #37 refactor): 2/2 distinct mids minted, matches 2026-04-23 stress-test pattern.
- Camera-move handler (`run_camera`, bug #6): still produces file + valid new media_id on L2-direct-off-L1.
- Extend-video multi-output handling (PR #28 iterate-all): both segments saved with distinct timestamps.
