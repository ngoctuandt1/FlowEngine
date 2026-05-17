"""WebSocket auth gate — production mode (DASHBOARD_PASSWORD set).

The HTTP middleware in `server/dashboard_auth.py` only inspects http scopes,
so /ws/jobs handshakes have to enforce the same signed cookie inline. Without
this gate any anonymous client could observe `job_update` broadcasts that
carry prompts, project URLs, and error text.
"""
from __future__ import annotations

from contextlib import contextmanager
import importlib
import time

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect


@contextmanager
def _reloaded_app(tmp_path, *, password: str = "test", secret: str = "test-secret"):
    import server.app  # noqa: WPS433
    import server.dashboard_auth  # noqa: WPS433

    patch = pytest.MonkeyPatch()
    patch.setenv("DASHBOARD_PASSWORD", password)
    patch.setenv("DASHBOARD_AUTH_SECRET", secret)
    patch.setenv("FLOW_DOWNLOAD_DIR", str(tmp_path / "downloads"))
    patch.setenv("FLOW_UPLOAD_DIR", str(tmp_path / "uploads"))

    importlib.reload(server.dashboard_auth)
    importlib.reload(server.app)
    try:
        yield server.app.app, server.dashboard_auth
    finally:
        patch.undo()
        importlib.reload(server.dashboard_auth)
        importlib.reload(server.app)


def test_ws_handshake_rejected_when_password_set_and_no_cookie(temp_db_path, tmp_path):
    with _reloaded_app(tmp_path) as (app, _):
        with TestClient(app) as client:
            with pytest.raises(WebSocketDisconnect) as exc:
                with client.websocket_connect("/ws/jobs"):
                    pass

    # 4401 = our custom "WS unauthorized" close code.
    assert exc.value.code == 4401


def test_ws_handshake_accepts_valid_signed_cookie(temp_db_path, tmp_path):
    with _reloaded_app(tmp_path) as (app, dashboard_auth):
        token = dashboard_auth._sign_token(int(time.time()))
        with TestClient(app, cookies={dashboard_auth.AUTH_COOKIE: token}) as client:
            with client.websocket_connect("/ws/jobs") as ws:
                # Connection is accepted; close cleanly without waiting for a ping.
                ws.close()


def test_ws_handshake_rejects_tampered_cookie(temp_db_path, tmp_path):
    with _reloaded_app(tmp_path) as (app, dashboard_auth):
        bad_token = f"{int(time.time())}.deadbeef" + "0" * 24
        with TestClient(
            app, cookies={dashboard_auth.AUTH_COOKIE: bad_token},
        ) as client:
            with pytest.raises(WebSocketDisconnect) as exc:
                with client.websocket_connect("/ws/jobs"):
                    pass

    assert exc.value.code == 4401


def test_ws_handshake_open_when_dashboard_password_unset(temp_db_path):
    # Dev mode (no password set) — must not break existing flows.
    from server.app import app

    with TestClient(app) as client:
        with client.websocket_connect("/ws/jobs") as ws:
            ws.close()
