# 2026-04-20 - Codex ingredients image refs

## Base branch

- Landed on `claude/codex-ingredients-image`
- Base: `claude/codex-gap-fill` at `ece1822`
- Note: `claude/codex-gap-fill` is local-only/unpushed, so rebasing onto `master` (`1591c06`) may be required later

## What landed

- Added `ingredients-to-video` to the backend job contract, frontend job picker, and worker dispatcher.
- Added `ingredient_image_paths: list[str]` to the job models plus SQLite persistence via `ingredient_image_paths_json`.
- Added `flow/operations/ingredients.py` as a Level-1 Flow operation that reuses the existing L1 helpers for project creation, prompt typing, model selection, output-count forcing, wait, media-id extraction, and download.
- Added frontend multi-upload support for Ingredients jobs:
  - `Add reference image` button
  - thumbnail cards with remove buttons
  - max 10 refs
  - payload serialized as `ingredient_image_paths`
- Added offline coverage for dispatcher routing and API round-trip persistence.

## Validation

- Tests before: `151 passed`
- Tests after: `153 passed`
- Command: `python -m pytest tests/ -q`

## Live dry-run evidence

- Worker path: `FLOW_USE_BASE_PROFILE=1`, `WORKER_PROFILES=ngoctuandt20`, `python run_worker.py`
- Job id: `71e5462c-a811-47cd-a8b8-17877e5f13ab`
- Status: `COMPLETED`
- Completed at: `2026-04-20T08:44:10.304152Z`
- Project URL: `https://labs.google/fx/tools/flow/project/6567c0ae-3790-4685-bfe6-c932be9395be`
- Media id: `227f6f9a-bb86-43a2-8e92-c23678acda01`
- Output file: `downloads\ingredients_1080p_1776674649.mp4`
- Size: `7,230,449 bytes`
- Resolution: `1920x1080`
- Visual check: extracted frame `logs\ingredients_result_frame.png` clearly shows the green marbled cup on linen by a window, matching at least one uploaded ref

## Selector corrections from live probe

- Composer chip selector needed to handle the default new-project state:
  - live new projects opened in Image mode with `button[aria-haspopup='menu']:has(i:text-is('crop_square'))`
  - after switching to Video, the chip became `button[aria-haspopup='menu']:has(i:text-is('crop_16_9'))`
- Output/sub-mode tabs were confirmed via exact tab text:
  - `[role='tab']:text-is('Video')`
  - `[role='tab']:text-is('Ingredients')`
- The `+` button behavior differed from the handoff assumption:
  - live selector stayed consistent with the prompt: `button:not([title*='Add Media']):has(i:text-is('add'))`
  - but clicking it opened a compact menu with `[role='menuitem']:text-is('Upload image')`, not a tabbed asset-picker panel
- Upload handling needed the native file chooser:
  - `page.expect_file_chooser()` around `Upload image` was the reliable path
  - DOM-hunting for a hidden file input was less reliable than the chooser event
- Submit precondition also differed from the handoff assumption:
  - live Ingredients refs did not appear as prompt chips
  - successful uploads appeared as left-rail media tiles (`img[alt='Generated image']`)
  - the operation now waits for the uploaded-tile count to reach `len(ingredient_image_paths)` before submit

## Notes

- A manual probe confirmed the live UI shape before the production-path rerun:
  - `+` -> `Upload image`
  - uploaded refs populate the left media rail
  - submit succeeds once both uploaded media tiles are present
