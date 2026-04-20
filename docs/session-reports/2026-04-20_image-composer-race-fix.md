# 2026-04-20 Image Composer Race Fix

> **SUPERSESSION (2026-04-20):** Follow-up recommendation completed later the same day. See `2026-04-20_composer-chip-fix-and-4k-live.md` for 3/3 serial t2i PASS results.

## Summary

Fixed the `text-to-image` composer race in `flow/operations/image.py` where second and later t2i jobs on the same account failed with `Composer tab not found: Image`.

Root cause: Flow persists composer mode per account. After a prior video job, the new-project composer can default to Video mode and only render the Image tab after the composer chip dropdown is opened. The old `_switch_to_image_output()` retried direct tab lookup and never forced the chip-open path early enough.

## Changes

- Added a fast path that uses the inline Image tab when it is already rendered.
- Added `_ensure_image_mode(page)` to mirror the persisted-mode dropdown pattern from `_ensure_video_mode()`:
  - open the composer chip
  - wait for `[role="menu"][data-state="open"]`
  - inspect `button[role="tab"]` entries
  - match Image by text or exact `i:text-is('image')` icon
  - click only when inactive
- Added info-level logging for which path was taken:
  - inline Image tab path
  - chip-open fallback path
- Kept the video flow untouched.

## Evidence

- Working t2i run in `logs/worker.log` at `2026-04-20 20:52`
- Failing t2i runs in `logs/worker.log` at `2026-04-20 20:53` and `2026-04-20 21:10`
- Memory reference: `C:\Users\Tuan\.claude\projects\D--AI-FlowEngine\memory\feedback_flow_video_mode_chip.md`

## Verification

- `python -c "import flow.operations.image; print('ok')"` -> `ok`
- `python -m pytest tests/ -x` -> `153 passed`
- Manual follow-up still recommended: run 3 serial t2i jobs on the same account, including after a prior video job, and confirm all 3 submit successfully.

## Git / PR

- Branch: `claude/bug-image-composer-race`
- Commit: `c969da0da124edbd2651115566e0e664c45b0e58`
- PR: `https://github.com/ngoctuandt1/FlowEngine/pull/25`
- Merge status: `MERGED`
