# Session Report — C6b upscale fix + image 2K/4K path

> **LIVE-STATUS UPDATE (2026-04-20):** Image 4K UI path was live-verified 3/3 on `ngoctuandt20`. See `2026-04-20_composer-chip-fix-and-4k-live.md`. The original `LIVE-UNVERIFIED` note below is kept for history.

## Metadata

- Branch: `claude/revert-video-upscale-add-image-2k-4k`
- Commit: `161d34711f44145df123e4857c0bcc4db029ace1`
- Commit subject: `fix(upscale): revert video 1080p regression + add image 2K/4K path`
- Scope: revert the video 1080p→2K regression from `7990707`; add an image-only UI upscale path gated by env; keep unrelated fail-if-no-files guards untouched

## Files changed

- `flow/upscale.py`
- `flow/download.py`

## Diff summary by file

### `flow/upscale.py`

- Reverted the VIDEO `/edit/` menu selector back to `^1080pUpscaled$` with fallback `1080p`.
- Restored VIDEO save token to `"{prefix}_1080p_{ts}.mp4"`.
- Kept `upscale_and_download_1080p()` public name for backward compatibility.
- Refactored the shared `/edit/` download trigger into reusable helpers:
  - `_open_edit_download_menu()`
  - `_log_menuitem_texts()`
- Added IMAGE-only `upscale_and_download_image(...)` with `target_quality: Literal["2k", "4k"]`.
- Added image menu matching with ordered regex lists and `logger.info` logging of `all_inner_texts()` before click.
- Added image save path that preserves the required filename token:
  - `"{prefix}_2k_{ts}.<ext>"`
  - `"{prefix}_4k_{ts}.<ext>"`
- Added minimal byte-signature content-type inference so image UI downloads can still use Flow’s shared `_extension_for(...)` mapping.

### `flow/download.py`

- Added `FLOW_IMAGE_QUALITY` env gating for image downloads.
- Default remains `original`; no image upscale is attempted unless env is `2k` or `4k`.
- When `media_kind == "image"` and env requests `2k` or `4k`, download dispatch now tries `upscale_and_download_image(...)` first.
- On image upscale failure, flow falls back to the existing API original download path and returns `None` only from the upscale helper, not the whole download pipeline.
- Adjusted API fallback naming so image originals keep the `original` quality token instead of inheriting video-oriented `720p`.

## Exact menu regexes

### VIDEO

- Primary: `^1080pUpscaled$`
- Fallback: `1080p`

### IMAGE `2k`

- `^2K\s*Upscaled$`
- `^2KUpscaled$`
- `\b2K\b`

### IMAGE `4k`

- `^4K\s*Upscaled$`
- `^4KUpscaled$`
- `\b4K\b`

## Env var reference

- `FLOW_IMAGE_QUALITY=original`
  - default behavior; no image UI upscale attempt
- `FLOW_IMAGE_QUALITY=2k`
  - try image `/edit/` UI upscale first, then fall back to API original on failure
- `FLOW_IMAGE_QUALITY=4k`
  - try image `/edit/` UI upscale first, then fall back to API original on failure

## Verification

- Import check:
  - `python -c "import flow.upscale, flow.download, flow.operations.image; print('import ok')"` ✅
- Requested pytest command:
  - `python -m pytest tests/ -x --timeout=30` ❌ unsupported here because `pytest` in this env does not have the `pytest-timeout` plugin installed
- Fallback pytest run:
  - `python -m pytest tests/ -x` ✅ `153 passed`

## LIVE-UNVERIFIED

- IMAGE `/edit/` menu labels were not live-probed in this task.
- The implementation assumes the user-confirmed 2K/4K image options exist and defends with:
  - ordered anchored→legacy→loose regexes
  - `logger.info` dump of visible menuitem texts before click
- Final image download extension is inferred from downloaded bytes, then normalized through `flow.download._extension_for(...)`; this is a defensive fallback because live image-menu response headers were not probed here.

## Not changed

- No browser live-testing
- No push
- No changes to `flow/model_selector.py`, `worker/*`, or `server/*`
- No removal of unrelated fail-if-no-files guards from the `7990707` era
