from __future__ import annotations

from contextlib import contextmanager
import importlib

import pytest
from fastapi.testclient import TestClient


@contextmanager
def _reloaded_app(tmp_path, *, trust_proxy_headers: str = "0"):
    import server.dashboard_auth  # noqa: WPS433
    import server.app  # noqa: WPS433

    patch = pytest.MonkeyPatch()
    patch.setenv("DASHBOARD_PASSWORD", "test")
    patch.setenv("DASHBOARD_AUTH_SECRET", "test-secret")
    patch.setenv("ALLOWED_ORIGINS", "https://ai.hassio.io.vn")
    patch.setenv("TRUST_PROXY_HEADERS", trust_proxy_headers)
    patch.setenv("FLOW_DOWNLOAD_DIR", str(tmp_path / "downloads"))
    patch.setenv("FLOW_UPLOAD_DIR", str(tmp_path / "uploads"))

    importlib.reload(server.dashboard_auth)
    importlib.reload(server.app)
    try:
        yield server.app.app
    finally:
        patch.undo()
        patch.setenv("DASHBOARD_AUTH_SECRET", "test-secret")
        importlib.reload(server.dashboard_auth)
        importlib.reload(server.app)
        patch.undo()


def test_downloads_mount_bypasses_dashboard_auth(temp_db_path, tmp_path):
    with _reloaded_app(tmp_path) as app:
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/downloads/foo.mp4")

    assert response.status_code == 404


def test_login_cookie_ignores_forwarded_proto_without_trusted_proxy_headers(
    temp_db_path,
    tmp_path,
):
    with _reloaded_app(tmp_path, trust_proxy_headers="0") as app:
        with TestClient(app, base_url="https://testserver") as client:
            response = client.post(
                "/api/auth/login",
                json={"password": "test"},
                headers={"X-Forwarded-Proto": "http"},
            )

    assert response.status_code == 200
    assert "secure" in response.headers["set-cookie"].lower()
