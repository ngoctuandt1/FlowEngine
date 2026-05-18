"""Dashboard password gate.

Wraps the FastAPI app with a pure ASGI middleware that requires either
a valid signed-cookie session or a worker API key on every request, and
exposes ``POST /api/auth/login`` + ``POST /api/auth/logout`` plus a
``GET /login`` HTML page.

Activated only when ``DASHBOARD_PASSWORD`` is set in the environment.
Worker traffic (``/api/worker/*``) is left to ``server/auth.py`` which
gates by ``Authorization: Bearer $API_KEY`` — that path bypasses the
cookie gate via ``PUBLIC_PATH_PREFIXES``.

Ported from the legacy video-ai-studio engine (modules/auth.py) and
trimmed for FlowEngine's route surface.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
import secrets
import time
from pathlib import Path
from urllib.parse import quote

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger(__name__)


DASHBOARD_PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "").strip()
DASHBOARD_AUTH_ENABLED = bool(DASHBOARD_PASSWORD)
TRUST_PROXY_HEADERS = os.environ.get("TRUST_PROXY_HEADERS", "0").strip() == "1"

AUTH_COOKIE = "flowengine_session"
AUTH_MAX_AGE = 30 * 24 * 3600  # 30 days


def _load_auth_secret() -> str:
    """Load a stable HMAC secret so cookies survive restarts."""
    env_secret = os.environ.get("DASHBOARD_AUTH_SECRET", "").strip()
    if env_secret:
        return env_secret

    candidates: list[Path] = []
    secret_file_env = os.environ.get("DASHBOARD_AUTH_SECRET_FILE", "").strip()
    if secret_file_env:
        candidates.append(Path(secret_file_env))

    data_root = Path(os.environ.get("FLOW_DATA_DIR", "")) if os.environ.get("FLOW_DATA_DIR") else None
    if data_root:
        candidates.append(data_root / "auth_secret.txt")

    candidates.append(Path(__file__).resolve().parent.parent / "data" / "auth_secret.txt")

    seen: set[str] = set()
    uniq: list[Path] = []
    for c in candidates:
        key = str(c)
        if key in seen:
            continue
        seen.add(key)
        uniq.append(c)

    for path in uniq:
        try:
            if path.exists():
                value = path.read_text(encoding="utf-8", errors="ignore").strip()
                if value:
                    return value
        except OSError as exc:
            logger.warning("dashboard_auth: cannot read %s: %s", path, exc)

    generated = secrets.token_hex(32)
    for path in uniq:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(generated, encoding="utf-8")
            return generated
        except OSError as exc:
            logger.warning("dashboard_auth: cannot write %s: %s", path, exc)
    return generated


_AUTH_SECRET = _load_auth_secret()


# Routes the gate must always allow through.
PUBLIC_PATHS = {
    "/login",
    "/api/auth/login",
    "/api/auth/logout",
    "/health",
    "/favicon.ico",
}

# Path prefixes the gate ignores entirely.
# Static assets are public; login.html ships its own form. Worker endpoints
# are gated by Bearer-token auth in server/auth.py — they must NOT require
# a dashboard cookie or workers would 401 on every claim. Generated and
# uploaded media mounts are intentionally public; gate them at nginx or
# another proxy layer if that ever needs to change.
PUBLIC_PATH_PREFIXES = (
    "/css/",
    "/js/",
    "/assets/",
    "/downloads/",
    "/uploads/",
    "/api/worker/",
)


def _request_is_https(request: Request) -> bool:
    # Production behind Cloudflare Tunnel should set TRUST_PROXY_HEADERS=1
    # and run uvicorn with --proxy-headers --forwarded-allow-ips="*".
    if TRUST_PROXY_HEADERS:
        forwarded = (request.headers.get("x-forwarded-proto") or "").strip().lower()
        if forwarded:
            return forwarded.split(",", 1)[0].strip() == "https"
    return (request.url.scheme or "").lower() == "https"


def _is_public_path(path: str) -> bool:
    if path in PUBLIC_PATHS:
        return True
    return any(path.startswith(prefix) for prefix in PUBLIC_PATH_PREFIXES)


def _is_cors_preflight(request: Request) -> bool:
    return request.method == "OPTIONS" and "access-control-request-method" in request.headers


def _sign_token(ts: int) -> str:
    msg = f"{ts}:{DASHBOARD_PASSWORD}".encode()
    sig = hmac.new(_AUTH_SECRET.encode(), msg, hashlib.sha256).hexdigest()[:32]
    return f"{ts}.{sig}"


def _verify_token(token: str) -> bool:
    if not token or not DASHBOARD_PASSWORD:
        return False
    try:
        ts_str, _ = token.split(".", 1)
        ts = int(ts_str)
    except ValueError:
        return False
    if time.time() - ts > AUTH_MAX_AGE:
        return False
    return hmac.compare_digest(token, _sign_token(ts))


def _set_session_cookie(response, request: Request) -> None:
    token = _sign_token(int(time.time()))
    response.set_cookie(
        AUTH_COOKIE,
        token,
        max_age=AUTH_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=_request_is_https(request),
        path="/",
    )


_LOGIN_HTML = """<!DOCTYPE html>
<html lang="vi">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>FlowEngine — Login</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { background: #0a0a0a; color: #e5e5e5; font-family: 'Inter', system-ui, sans-serif;
  display: flex; align-items: center; justify-content: center; min-height: 100vh; }
.card { background: rgba(28,28,28,0.95); border: 1px solid rgba(255,255,255,0.08);
  border-radius: 16px; padding: 48px 40px; width: 380px; text-align: center;
  box-shadow: 0 24px 80px rgba(0,0,0,0.6); backdrop-filter: blur(20px); }
.card h1 { font-size: 1.5rem; font-weight: 600; margin-bottom: 8px; }
.card p { font-size: 0.85rem; color: #888; margin-bottom: 28px; }
input { width: 100%; padding: 14px 16px; background: rgba(255,255,255,0.06);
  border: 1px solid rgba(255,255,255,0.1); border-radius: 10px; color: #fff;
  font-size: 1rem; outline: none; transition: border 0.2s; }
input:focus { border-color: #818CF8; }
button { width: 100%; padding: 14px; margin-top: 16px; background: linear-gradient(135deg, #6366F1, #818CF8);
  border: none; border-radius: 10px; color: #fff; font-size: 1rem; font-weight: 600;
  cursor: pointer; transition: opacity 0.2s; }
button:hover { opacity: 0.9; }
.err { color: #f87171; font-size: 0.8rem; margin-top: 12px; display: none; }
</style>
</head>
<body>
<div class="card">
  <h1>FlowEngine</h1>
  <p>Nhập mật khẩu để truy cập</p>
  <form id="f" onsubmit="return doLogin()">
    <input type="password" id="pw" placeholder="Mật khẩu" autofocus autocomplete="current-password">
    <button type="submit">Đăng nhập</button>
    <div class="err" id="err">Sai mật khẩu</div>
  </form>
</div>
<script>
async function doLogin() {
  const pw = document.getElementById('pw').value;
  const res = await fetch('/api/auth/login', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({password: pw})
  });
  if (res.ok) {
    const params = new URLSearchParams(window.location.search);
    const next = params.get('next') || '/';
    const safeNext = next.startsWith('/') && !next.startsWith('//') && !next.includes('://') ? next : '/';
    window.location.href = safeNext;
  } else {
    document.getElementById('err').style.display = 'block';
  }
  return false;
}
</script>
</body>
</html>
"""


class DashboardAuthMiddleware:
    """Gate every non-public request behind a signed-cookie session."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        if not DASHBOARD_AUTH_ENABLED:
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        path = request.url.path

        # Let browser preflight requests reach CORSMiddleware even if
        # middleware order regresses later.
        if _is_cors_preflight(request):
            await self.app(scope, receive, send)
            return

        if _is_public_path(path):
            await self.app(scope, receive, send)
            return

        token = request.cookies.get(AUTH_COOKIE)
        if _verify_token(token or ""):
            await self.app(scope, receive, send)
            return

        # API requests get a JSON 401; browser navigations get a redirect
        # to the login page so the SPA shell does not load unauthenticated.
        if path.startswith("/api/") or path.startswith("/ws/"):
            response = JSONResponse(
                status_code=401,
                content={"error": "Unauthorized"},
            )
            await response(scope, receive, send)
            return

        next_path = path
        if request.url.query:
            next_path = f"{next_path}?{request.url.query}"
        response = RedirectResponse(
            url=f"/login?next={quote(next_path, safe='')}",
            status_code=307,
        )
        await response(scope, receive, send)


async def serve_login_page() -> HTMLResponse:
    return HTMLResponse(_LOGIN_HTML)


async def api_login(request: Request) -> JSONResponse:
    if not DASHBOARD_AUTH_ENABLED:
        return JSONResponse({"ok": True, "auth_disabled": True})
    try:
        body = await request.json()
    except Exception:
        body = {}
    pw = (body or {}).get("password", "")
    if pw == DASHBOARD_PASSWORD:
        resp = JSONResponse({"ok": True})
        _set_session_cookie(resp, request)
        return resp
    return JSONResponse(status_code=401, content={"error": "Wrong password"})


async def api_logout() -> JSONResponse:
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(AUTH_COOKIE, path="/")
    return resp
