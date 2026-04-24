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
| Async busy/done toast fallback | ⏭ DEFERRED | Worker runs with default `FLOW_IMAGE_QUALITY=original`; t2i job `dac476fd` took original path (file `t2i_original_1777054234.jpg` 1.1 MB). To exercise busy/done, restart worker with `FLOW_IMAGE_QUALITY=4k`. |

## Observations

- **Extend UI 1080p path fell through to 720p API fallback** for both segments. This is working-as-designed (`flow/download.py:116` "Video quality downgraded from 1080p to 720p") — not a new regression, matches historical pattern (`t2v_720p_*` files from prior sessions in `pr3-agent/downloads/`). Camera-move succeeded at 1080p on same profile in the same run, so the fallback is extend-UI-specific, not account/session-wide.
- **Worker PID 307377** was started earlier today (Apr 24); does not contain PR #52 browser-pool or PR #49 window-geometry changes. L2 probes still pass on it because the 2026-04-23 media_id fix (`0bb9d29`) landed before that worker launched.

## Still pending

### #49 Chrome window geometry live-smoke

Needs a worker restart with the env vars. Handoff:

```bash
# 1. Stop existing worker (selective by user-data-dir per memory feedback_chrome_kill_selective.md)
# 2. Restart with geometry env
FLOW_WINDOW_SIZE=810x700 FLOW_WINDOW_POSITION=1750,0 \
  WORKER_PROFILES=ngoctuandt20 \
  FLOW_USE_BASE_PROFILE=1 \
  python run_worker.py
# 3. Submit any L1 t2v and visually confirm Chrome window docks to top-right ~810x700
```

Success = Chrome opens at requested size + position. Failure modes to watch: (a) defaults if env parsing rejects (check logs for "Ignoring invalid"), (b) Chrome crash on sub-100px size.

### Image 2K/4K async busy/done toast fallback

Needs `FLOW_IMAGE_QUALITY=4k` on worker. In live runs during 2026-04-20 session, 4K always served immediately (cached/warm) — so the `busy → _wait_upscale → re-click` branch has never been exercised live, only unit-tested (7 cases in `tests/test_image_upscale.py`). To force busy-state exercise you'd need to submit multiple 4K jobs back-to-back on a cold account. LOW priority.

## Confirmed live-clean (this run)

- L2 `media_id` extraction (PR #30 fix + #37 refactor): 2/2 distinct mids minted, matches 2026-04-23 stress-test pattern.
- Camera-move handler (`run_camera`, bug #6): still produces file + valid new media_id on L2-direct-off-L1.
- Extend-video multi-output handling (PR #28 iterate-all): both segments saved with distinct timestamps.
