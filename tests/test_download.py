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
"""

import importlib
import os


def test_upscale_poll_defaults_meet_minimum():
    """B34 contract: default poll window ≥ 120s.

    Prevents a silent regression to the pre-B34 30s total that caused
    every live run to return 720p instead of 1080p.
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
        assert total >= 120, (
            f"B34: default poll window must be ≥ 120s to cover Flow upscale "
            f"latency. Got {dl.UPSCALE_POLL_INTERVAL}s × {dl.UPSCALE_MAX_RETRIES} "
            f"retries = {total}s."
        )
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v


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
