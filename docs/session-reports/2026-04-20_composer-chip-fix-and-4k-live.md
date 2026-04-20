# Composer-chip fix + 4K t2i live-test (3/3 PASS)

## Context

After PR #25 (`c969da0`) added `_switch_to_image_output` with a composer-chip fallback,
a 2nd t2i job on the same worker (Flow in Image mode from the 1st run) failed with:

```
RuntimeError: Could not open composer menu for frames-to-video
  at flow/operations/frames_to_video.py:188
```

Root cause: `COMPOSER_MENU_SELECTORS` only matched the video-aspect chip icons
`crop_9_16` / `crop_16_9`. When Flow's composer was in Image mode, the chip icon
was `crop_square` / `crop_landscape` / `crop_portrait` instead.

## Fix — PR #26 (`429dad6`)

- Unified `COMPOSER_MENU_SELECTORS` in `flow/operations/frames_to_video.py` to
  cover all 5 known chip icons (video + image).
- Dropped the duplicate `IMAGE_CHIP_SELECTORS` list in `flow/operations/image.py`;
  `_locate_image_chip` now iterates the shared list only.
- Added a `logger.warning` diagnostic dumping visible `button[aria-haspopup='menu']`
  inner texts when all selectors miss, so the next DOM drift leaves evidence.
- Error message updated from "Could not open composer menu for frames-to-video"
  to "Could not open composer chip (tried N icon variants). Flow may have
  introduced a new chip icon - run the probe to update."

## Live-test — 3 serial t2i 4K on `ngoctuandt20`

Worker env: `FLOW_USE_BASE_PROFILE=1 CHROME_USER_DATA_DIR=D:\AI\chrome-profiles FLOW_IMAGE_QUALITY=4k WORKER_PROFILES=ngoctuandt20`

| Job | ID | Prompt | File | Size | Duration |
|-----|-----|--------|------|------|----------|
| J1 | `5966f88f` | zen garden | `t2i_4k_1776697296.jpg` | 12.0 MB | ~1:07 |
| J2 | `db13913f` | typewriter | `t2i_4k_1776697408.jpg` |  9.4 MB | ~1:42 |
| J3 | `db171e0b` | phoenix mosaic | `t2i_4k_1776697519.jpg` | 12.0 MB | ~1:40 |

### Path coverage

- **J1**: hit the inline-tab fast path (`_switch_to_image_output: inline Image tab path (state=active)`) — worker fresh, composer defaulted to Image mode because the persisted session was already Image-mode from previous runs.
- **J2, J3**: hit the chip-menu fallback (`opening composer chip to reveal Image tab`) and resolved cleanly. This is the exact code path PR #26 unblocks for Flow's persisted-mode state machine.

### Upscale path

All 3 runs logged the expected menu dump:

```
[UPSCALE][IMAGE][4k] menu items: ['1K\nOriginal size', '2K\nUpscaled', '4K\nUpscaled']
[UPSCALE][IMAGE] Clicked 4k item with regex ^4K\s*Upscaled$
[UPSCALE] Saved image: ...\t2i_4k_<ts>.jpg (~10-12 MB)
```

Download path used the direct-download branch in PR #24's C6c async state machine —
no `busy`/`done` toast fallback was exercised (Flow served the 4K file immediately).

## Status

Image 4K UI upscale path is **done v1**:

- Menu selector (`^4K\s*Upscaled$`) and 2K counterpart (`^2K\s*Upscaled$`) live-verified.
- Async busy/done fallback implemented in `flow/upscale.py` (C6c) but not yet exercised.
- Composer-mode persistence (video -> image and image -> image) handled by PR #26.
- `FLOW_IMAGE_QUALITY=2k|4k` env gating live-verified; default `original` path untouched.

## Parked

- LOW: unit tests for `_requested_image_quality()`, `_save_image_download()`,
  and the `upscale_and_download_image()` fallback.
- LOW: multi-image iteration in `download_video()` image branch.
- LOW: async busy/done toast fallback for image path is implemented but untested live.
