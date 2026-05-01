# Session - 2026-05-01: ai.hassio.io.vn cutover to FlowEngine

**Public URL:** `https://ai.hassio.io.vn`  
**Base commit before merge train:** `bc75a31`  
**Final verified master commit:** `6a6b6a3`  
**Final verification tag:** `live-verified-all-cats-20260501-2134`

## TL;DR

On 2026-05-01 the public `ai.hassio.io.vn` route was moved off the legacy
`video-ai-studio` deployment and onto FlowEngine while preserving the existing
Cloudflare Tunnel target on port `8899`. The merge train shipped the new public
dashboard/pages, added a signed-cookie password gate, hardened deploy/runtime
behavior on Debian, and then passed live end-to-end verification across 26
tracked categories. Legacy assets were archived instead of deleted so rollback
stayed one tag away.

## Goal

Replace the legacy public web entrypoint at `https://ai.hassio.io.vn` with
FlowEngine without changing the externally visible URL or tunnel route, keep the
old engine archived for rollback, and prove that web -> server -> worker ->
Chrome -> Flow still works across the full user-facing surface after the cutover.

## PRs merged

Note: the session handoff text called this a "14 PR" merge train, but the
enumerated range after `bc75a31` is 18 merged PRs (`#90-#108`, excluding `#93`).

| PR | Commit | Area | Outcome |
|---|---|---|---|
| #90 | `0e017d6` | FE page | TTS page |
| #91 | `d489c47` | FE page | Characters + workflows |
| #92 | `1a108cc` | FE page | Media tools |
| #94 | `6ad7dcb` | FE page | Jobs history + gallery |
| #95 | `6d0ca66` | Auth | Dashboard password gate (cookie-signed) |
| #96 | `3ad794e` | FE page | Engine status |
| #97 | `fe363f9` | FE page | Batch queue |
| #98 | `878c7c6` | FE page | Job detail |
| #99 | `d59beff` | FE page | Chain tree visualizer |
| #100 | `f0be9e4` | FE fix | Job-detail follow-up fixes |
| #101 | `d2b4ee0` | FE fix | Chain-tree follow-up fixes |
| #102 | `667e52c` | FE fix | Batch-queue follow-up fixes |
| #103 | `08cf51b` | FE fix | Engine-status follow-up fixes |
| #104 | `026ea0c` | Backend | 30s WebSocket keepalive ping |
| #105 | `59f44a5` | Backend | Upload streaming + magic-byte validation |
| #106 | `9f247ba` | Backend | POSIX Chrome process-group reap |
| #107 | `fcd5cb8` | Backend | CORS preflight bypass + ASGI middleware + proxy guard |
| #108 | `6a6b6a3` | Backend | Camera preset FE/BE sync + JobCreate validators + Lite-LP default |

## Deploy steps

1. Kept the public route stable by binding FlowEngine on `0.0.0.0:8899` instead
   of moving the Cloudflare Tunnel to a new port.
2. Patched the Debian/systemd `run_server.py` launch path so uvicorn honors
   proxy headers when `TRUST_PROXY_HEADERS=1`.
3. Installed static `ffmpeg` `7.0.2` after the Debian apt mirror hung; media
   cut/merge/fetch-url/retarget were validated against that binary.
4. Installed `Pillow` so upload validation could sniff magic bytes and reject
   bad inputs early.
5. Stopped the legacy `video-ai-studio` container once FlowEngine was reachable
   on the same public route.
6. Archived the old engine directories at
   `/opt/_archive/video-ai-studio.20260501` and `/opt/_archive/video-ai.20260501`
   instead of deleting them.

## Live verification

| Group | Category | Result | Notes |
|---|---|---|---|
| L1 | `text-to-video` | pass | End-to-end on public deploy |
| L1 | `text-to-image` | pass | End-to-end on public deploy |
| L1 | `frames-to-video` | pass | Split replacement for legacy image-to-video bundle |
| L1 | `ingredients-to-video` | pass | Split replacement for legacy image-to-video bundle |
| L2 | `extend-video` | pass | End-to-end on public deploy |
| L2 | `insert-object` | pass | End-to-end on public deploy |
| L2 | `remove-object` | pass | End-to-end on public deploy |
| L2 | `camera-move` | pass | End-to-end on public deploy |
| Support | TTS | pass | Public UI + backend route verified |
| Support | LLM helpers | pass | `expand-prompt` + `auto-prompt` + `prompt-builder` |
| Support | Uploads | pass | `Pillow` magic-byte validation exercised live |
| Support | Characters CRUD | pass | Create/update/delete/list path verified |
| Support | Templates | pass | Create + instantiate both verified |
| Support | Media cut | pass | `ffmpeg` static binary path verified |
| Support | Media merge | pass | `ffmpeg` static binary path verified |
| Support | Media fetch-url | pass | Live fetch/download path verified |
| Support | Media retarget | pass | Live retarget path verified |
| Support | `GET /api/profiles` | pass | Returned live deploy state |
| Support | `GET /api/jobs` | pass | Returned live deploy state |
| Support | `GET /api/templates` | pass | Returned live deploy state |
| Support | `GET /api/characters` | pass | Returned live deploy state |
| Support | Dashboard auth | pass | Login + signed cookie verified |
| Support | WebSocket keepalive | pass | 30s ping observed |
| Support | CORS preflight | pass | `200` with allow-origin headers |
| Support | `/downloads` static bypass | pass | Static file route bypassed auth gate correctly |
| Support | JobCreate + output file integrity | pass | Fail-fast validators hit; output files present on disk `10/10` |

## Credit tally

- Approximate spend for the session: `~46` Veo Lite credits.
- Total covers 3 live verification rounds on the public deployment.

## Rollback tags

| Tag | Purpose |
|---|---|
| `pre-fe-merge-20260501-1403` | Before the public frontend page train |
| `pre-r2-merge-20260501-1605` | Before engine-status / batch-queue / job-detail / chain-tree round |
| `pre-r3-fix-merge-20260501-2023` | Before round-2 frontend fixes |
| `pre-r4-deploy-fixes-20260501-2039` | Before final deploy hardening |
| `live-verified-all-cats-20260501-2134` | Final all-categories verification point |

## Open items

| Type | Item | Status | Notes |
|---|---|---|---|
| Archived legacy op | `audio-to-video` | archived | Deliberately removed from FlowEngine in `cfead65`; not re-added during cutover |
| Archived legacy op | `remix-video` | archived | Legacy engine only had a stub / `ImportError` fallback; no real implementation to port |
| Archived legacy op | `shorten-video` | archived | Legacy engine only had a stub / `ImportError` fallback; no real implementation to port |
| Deferred | `jefmon_vhnu100` profile recovery | open | Login is stuck on password challenge; likely missing 2FA/TOTP data in `profiles_ultra.txt` |
| Deferred | `s1324h1450` replacement | open | Flow access is org-disabled (`ServiceNotAllowed`); treat the account as permanently dead |
| Deferred | Multi-profile pool expansion | open | After cutover only `ngoctuandt20` is healthy; add more Flow-eligible accounts before scaling beyond single-profile live runs |
