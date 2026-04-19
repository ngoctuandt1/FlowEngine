"""B34 — source-level contracts for upscale polling env-override.

Pre-B34: `_api_download_with_retry(max_retries=3)` + POLL_INTERVAL=10s →
every live Tier 2 run fell through to 720p because `_upsampled` returned
202/404 at 30s cumulative wait (Flow upscale typically 1-3 min for 9:16
clips). Evidence: `downloads/` folder had zero `_1080p_` files across
Run 10 + Run 12 + earlier tests.

Post-B34: defaults are 15s × 12 retries = 180s total, env-configurable
via `FLOW_UPSCALE_POLL_INTERVAL_SEC` + `FLOW_UPSCALE_MAX_RETRIES`. Tests
here assert the module-level constants read from env, not the underlying
Playwright timing (that's a live-E2E concern, not a unit-test concern).

B37 — tests for media-id harvesting from `client._media_id_events` /
`_video_urls`. Pre-B37 `download_video` read `evt["media_id"]` but the
client stores events under key `"mid"` (see `client.py::_record_media_id`)
→ primary harvest path always empty, download fell back to blob. Fix
was a 1-char key rename + unwrap `_video_urls` list-of-dicts before
passing each URL to `media_id_from_url`.
"""

import importlib
import inspect
import os


def test_upscale_poll_defaults_meet_minimum():
    """B34/B34b contract: default poll window ≥ 300s.

    Prevents a silent regression below the B34b threshold (24 × 15 =
    360s). Run 15 (2026-04-19) proved the pre-B34b 180s was still too
    short for Flow upscale on 9:16 / 16:9 fast-LP clips — all 3 jobs
    fell through to 720p within 180s. Keeping ≥ 300s as the guard
    floor leaves a 60s safety margin above the last observed miss.
    """
    # Fresh import without env overrides
    env_keys = (
        "FLOW_UPSCALE_POLL_INTERVAL_SEC",
        "FLOW_UPSCALE_MAX_RETRIES",
        "FLOW_UPSCALE_MAX_WAIT_SEC",
    )
    saved = {k: os.environ.pop(k, None) for k in env_keys}
    try:
        import flow.download as dl  # noqa: E402
        importlib.reload(dl)
        total = dl.UPSCALE_POLL_INTERVAL * dl.UPSCALE_MAX_RETRIES
        assert total >= 300, (
            f"B34b: default poll window must be ≥ 300s to cover Flow upscale "
            f"latency. Got {dl.UPSCALE_POLL_INTERVAL}s × {dl.UPSCALE_MAX_RETRIES} "
            f"retries = {total}s."
        )
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v


def test_download_reads_mid_key_from_media_id_events():
    """B37 source trip-wire: `download_video` must harvest media IDs from
    `client._media_id_events` using key `"mid"` (the key `client.py::
    _record_media_id` writes). Pre-B37 used `"media_id"` → always empty
    → blob fallback. This check pins the key contract so a rename on
    either side is caught immediately."""
    from flow.download import download_video

    src = inspect.getsource(download_video)
    # The harvest block must reference "mid" — the key written by
    # `_record_media_id`. If someone renames back to "media_id", the
    # download pipeline silently reverts to fallback.
    assert 'evt["mid"]' in src or "evt['mid']" in src, (
        "B37: download_video must read media IDs via key 'mid' to match "
        "client._record_media_id storage shape. See flow/client.py:506."
    )


def test_download_unwraps_video_urls_list_of_dicts():
    """B37 source trip-wire: `_video_urls` is a list of
    `{"url": str, "ts": float}` (see `client.py::_on_response`). The
    fallback harvester must unwrap the dict before calling
    `media_id_from_url`. Pre-B37 passed the dict directly — regex
    matched via `str(dict)` repr by accident, worked only when the
    captured URL still carried `?name=...`."""
    from flow.download import download_video

    src = inspect.getsource(download_video)
    # Accept either `entry["url"]`/`entry['url']` unwrap or an explicit
    # isinstance check.
    has_unwrap = (
        'entry["url"]' in src
        or "entry['url']" in src
        or "isinstance(entry, dict)" in src
    )
    assert has_unwrap, (
        "B37: download_video must unwrap `_video_urls` dicts before "
        "passing each URL to media_id_from_url. See flow/client.py:466."
    )


def test_upscale_env_overrides_read_at_import():
    """B34 contract: `FLOW_UPSCALE_POLL_INTERVAL_SEC` + `FLOW_UPSCALE_MAX_RETRIES`
    env vars override the defaults at module-import time. Operators tuning for
    a slower Flow region can extend the window without code change.
    """
    os.environ["FLOW_UPSCALE_POLL_INTERVAL_SEC"] = "25"
    os.environ["FLOW_UPSCALE_MAX_RETRIES"] = "20"
    try:
        import flow.download as dl
        importlib.reload(dl)
        assert dl.UPSCALE_POLL_INTERVAL == 25
        assert dl.UPSCALE_MAX_RETRIES == 20
    finally:
        del os.environ["FLOW_UPSCALE_POLL_INTERVAL_SEC"]
        del os.environ["FLOW_UPSCALE_MAX_RETRIES"]
        import flow.download as dl
        importlib.reload(dl)  # restore defaults for subsequent tests
