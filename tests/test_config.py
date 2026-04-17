"""B7 regression test — default SERVER_PORT must match worker default.

Root cause: server/config.py defaulted to 8000 while worker/main.py defaulted
to 8080. Running scripts/start_all.cmd locally (no env var) => server on 8000,
worker polling 8080 => connection refused loop.

Invariant guarded here: the two defaults MUST match. Docker compose always
sets SERVER_PORT/SERVER_URL explicitly, so this only bites local dev.
"""
import os

import pytest


def test_server_port_default_is_8080(monkeypatch):
    """B7: server/config.py default must be 8080 (match worker/main.py default)."""
    # Clear any env override so we read the code default, not the shell.
    monkeypatch.delenv("SERVER_PORT", raising=False)

    # Re-import fresh so the module re-evaluates os.getenv at import time.
    import importlib
    import server.config as config_mod
    importlib.reload(config_mod)

    assert config_mod.SERVER_PORT == 8080, (
        f"server default port is {config_mod.SERVER_PORT} but worker/main.py "
        f"defaults SERVER_URL to http://localhost:8080. The two MUST match "
        f"or `scripts/start_all.cmd` fails with connection refused."
    )


def test_server_port_respects_env_override(monkeypatch):
    """Env var SERVER_PORT should still be honored (e.g. Docker sets 8080 explicitly)."""
    monkeypatch.setenv("SERVER_PORT", "9999")

    import importlib
    import server.config as config_mod
    importlib.reload(config_mod)

    assert config_mod.SERVER_PORT == 9999
