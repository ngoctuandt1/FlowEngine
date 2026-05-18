"""WebSocket endpoint for real-time job updates."""

import asyncio
import json
import logging
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any, Awaitable, Callable

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from server.models.job import Job

logger = logging.getLogger(__name__)

router = APIRouter(tags=["websocket"])

# -- Connected clients ---------------------------------------------------------
_clients: set[WebSocket] = set()
KEEPALIVE_INTERVAL_SECONDS = 30.0


async def _keepalive(
    websocket: WebSocket,
    *,
    interval_seconds: float | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    """Send periodic ping frames so upstream tunnels do not idle-drop the WS."""
    interval = KEEPALIVE_INTERVAL_SECONDS if interval_seconds is None else interval_seconds

    try:
        while True:
            # Cloudflare Tunnel appears to silently drop idle WebSockets after
            # roughly 100s, so a 30s ping keeps enough headroom to avoid it.
            await sleep(interval)
            await websocket.send_text(
                json.dumps(
                    {
                        "event": "ping",
                        "ts": datetime.now(UTC).isoformat(),
                    }
                )
            )
    except asyncio.CancelledError:
        raise
    except Exception:
        return


@router.websocket("/ws/jobs")
async def ws_jobs(ws: WebSocket):
    """Accept a WS connection and keep it alive for broadcasting.

    When DASHBOARD_PASSWORD is set (prod mode), the dashboard auth middleware
    skips websocket scopes, so this endpoint enforces the same signed-cookie
    gate inline. Without the cookie the handshake is closed with code 4401
    so anonymous clients cannot observe job_update broadcasts (which carry
    prompts, URLs, and error text).
    """
    # Import lazily so tests can reload `server.dashboard_auth` after env
    # changes without leaving this module bound to a stale token verifier.
    from server import dashboard_auth

    if dashboard_auth.DASHBOARD_AUTH_ENABLED:
        token = ws.cookies.get(dashboard_auth.AUTH_COOKIE) or ""
        if not dashboard_auth._verify_token(token):
            await ws.close(code=4401)
            return

    await ws.accept()
    _clients.add(ws)
    keepalive_task = asyncio.create_task(_keepalive(ws))
    logger.info("WS client connected (%d total)", len(_clients))
    try:
        # Keep connection open; read loop just absorbs pings / client msgs.
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        keepalive_task.cancel()
        with suppress(asyncio.CancelledError):
            await keepalive_task
        _clients.discard(ws)
        logger.info("WS client disconnected (%d remaining)", len(_clients))


# -- Broadcast helper (imported by other route modules) ------------------------

async def broadcast_job_update(job: Job | dict[str, Any]) -> None:
    """Push a job-status JSON message to every connected WS client."""
    if not _clients:
        return

    if isinstance(job, Job):
        payload = job.model_dump(mode="json")
    else:
        payload = job

    message = json.dumps({"event": "job_update", "data": payload})

    stale: list[WebSocket] = []
    for ws in _clients:
        try:
            await ws.send_text(message)
        except Exception:
            stale.append(ws)

    for ws in stale:
        _clients.discard(ws)
