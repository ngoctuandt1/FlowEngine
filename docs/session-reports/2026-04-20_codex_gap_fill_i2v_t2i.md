# 2026-04-20 — Codex gap fill (i2v + t2i)

## Summary

Three requested commits landed on `claude/codex-gap-fill`:

1. `d2e590f` `fix(web): align Veo model list`
2. `bd480f0` `feat(engine): add frames-to-video flow`
3. `eba254e` `feat(engine): add text-to-image flow`

## Commit 1

### Landed

- Aligned `frontend/js/constants.js::MODELS` with `flow/model_selector.py::MODEL_MAP`.
- Removed the stale bare `veo-3.1` key.
- Added the missing `veo-3.1-lite` and corrected the paid Quality key to `veo-3.1-quality`.

### Validation

- Pre-check: `git grep -n "veo-3\.1'" -- frontend` returned no residual references after the rewrite.
- Tests before: `144 passed`
- Tests after: `144 passed`

## Commit 2

### Landed

- Added `frames-to-video` to the backend job contract and frontend job picker.
- Added `start_image_path` / `end_image_path` to the job schema, SQLite schema, and job-store plumbing.
- Added `POST /api/uploads` plus `/uploads` static serving from `FLOW_UPLOAD_DIR`.
- Added worker-side resolution of server-relative upload paths to local disk.
- Implemented `flow/operations/frames_to_video.py` for Video → Frames mode with:
  - composer chip open
  - Video/Frames tab selection
  - Start / optional End image upload
  - prompt typing
  - model selection
  - aspect selection
  - forced `x1`
  - submit / wait / download / media extraction
- Added offline coverage for dispatcher routing and the upload endpoint.
- Kept chain-builder from surfacing the new L1 type as an L2 step.

### Validation

- Tests before: `144 passed`
- Tests after: `148 passed`

### Live dry-run evidence

- Job id: `3bd23ee2-5357-4e80-9289-0af74b148116`
- Status: `COMPLETED`
- `completed_at`: `2026-04-20T07:27:51.669255Z`
- Output file: `downloads\f2v_1080p_1776670071.mp4`
- Size: `10,638,714 bytes`
- Resolution: `1920x1080`
- Project URL: `https://labs.google/fx/tools/flow/project/3c6a1144-3a84-4a72-bd22-a4177b87b1e0`
- Media id: `0bb49ef9-7783-4c92-86c0-c53055790305`

## Commit 3

### Landed

- Added `text-to-image` to the backend job contract and frontend job picker.
- Added `ref_image_path` to the job schema, SQLite schema, and job-store plumbing.
- Added backend default-model handling so `text-to-image` defaults to `nano-banana-pro`.
- Added `flow/operations/image.py` for Image output mode with:
  - Image output tab switch
  - image-model selection (`nano-banana-pro`, `nano-banana-2`, `imagen-4`)
  - 5 image aspect ratios (`16:9`, `4:3`, `1:1`, `3:4`, `9:16`)
  - forced `x1`
  - submit / wait / download / media extraction
- Extended the existing download pipeline to save image outputs using the returned content type instead of forking a second helper.
- Added frontend support for image-only model/aspect lists plus optional reference-image upload.
- Added API and dispatcher tests for `text-to-image`.

### Validation

- Tests before: `148 passed`
- Tests after: `151 passed`

### Live dry-run evidence

- Job id: `c73167f7-c1d3-4751-bb88-28e4ea2dbd3c`
- Status: `COMPLETED`
- `completed_at`: `2026-04-20T07:43:02.488989Z`
- Output file: `downloads\t2i_720p_1776670982.jpg`
- Size: `793,535 bytes`
- Resolution: `1024x1024`
- Project URL: `https://labs.google/fx/tools/flow/project/edb1f90f-714b-4863-9ed8-e5c1d79fed34`
- Media id: `c0513f37-9d66-4cc8-98e8-e659e6a2a71b`

## Selector notes

Two live selectors differed from the handoff’s text-only expectation and were recorded in `flow/operations/image.py`:

- Image output tab: the reliable selector was `[role='tab']:has(i:text-is('image'))`, not a plain `:text-is('Image')` probe.
- Image model dropdown chip: the reliable anchor was `button[aria-haspopup='menu']:has(i:text-is('arrow_drop_down'))` filtered by text containing `Nano Banana` / `Imagen`; the live text can begin with the banana emoji.

## Deferred work

- Video Ingredients mode remains deferred.
- Voice refs remain deferred.
- `text-to-image` reference-image plumbing is implemented but only prompt-only T2I was live-validated in this session.
- Browser preview-specific checks (`preview_snapshot`, `preview_console_logs`) were not run here because those tools were not available in this Codex desktop session; validation used the live server/worker path instead.
