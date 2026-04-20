# C6c Review Response

> **SUPERSESSION (2026-04-20):** Superseded later the same day by PR #28 (`ef09a13`). Multi-image iteration now lands in `flow/download.py`. See `docs/session-reports/2026-04-20_session-handoff.md` §1.

## Commit

- Branch: `claude/revert-video-upscale-add-image-2k-4k`
- Commit: `2edf098`
- Commit message: `fix(upscale): handle async busy/done state in image upscale path`

## Fixed

- MEDIUM: `flow/upscale.py` image upscale now mirrors the video async state machine instead of assuming `expect_download()` always fires immediately.
- After clicking the image `2K`/`4K` menu item, the code now races download events against popup state for ~15s.
- If a download arrives immediately, it is saved and returned.
- If popup state is `busy`, the code reuses `_wait_upscale()` with the existing `FLOW_UPSCALE_TIMEOUT_SEC` / `UPSCALE_TIMEOUT_SEC` budget, closes the toast, then re-opens the menu and re-clicks the requested image target inside `expect_download()` to fetch the real file.
- If popup state is `done`, the code closes the toast and performs the same re-download path.
- If popup state is `failed`, the code logs it, closes the toast, and retries the outer attempt.
- If no download or recognized toast appears in the initial race window, the image helper returns `None`, preserving the existing fallback to the original API download path.
- `_DONE_RE` and `_BUSY_RE` were broadened so the shared popup scanner can recognize both video phrases and image phrases such as `2K ready`, `4K ready`, and generic `upscale complete`.

## Nits Fixed

- `_open_edit_download_menu()` is now a real abstraction: it clicks the trigger and verifies the menu opened by waiting for `[role="menuitem"]`.
- Image menu text diagnostics now wait for menu items before logging, so slow renders no longer produce misleading empty-list logs.

## Parked

- LOW: no test-file changes were made in this diff.
- LOW: multi-image iteration remains parked; image download still follows the current single-image invariant.
- LOW: uppercase `FLOW_IMAGE_QUALITY=2K` handling was already correct and was not changed.

## Verification

- `python -c "import flow.upscale, flow.download; print('import ok')"` -> `import ok`
- `pytest tests/ -x` -> `153 passed`

## Diff Summary

- Refactored shared menu/download helpers so both video and image paths can reuse the same download-vs-popup race and re-download flow.
- Kept video target selection on `1080pUpscaled`; no video menu behavior was changed.
- Limited code changes to `flow/upscale.py`.
