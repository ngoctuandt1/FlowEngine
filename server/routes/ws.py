"""WebSocket endpoint for real-time job updates."""

import json
import logging
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from server.models.job import Job

logger = logging.getLogger(__name__)

router = APIRouter(tags=["websocket"])

# -- Connected clients ---------------------------------------------------------
_clients: set[WebSocket] = set()


@router.websocket("/ws/jobs")
async def ws_jobs(ws: WebSocket):
    """Accept a WS connection and keep it alive for broadcasting."""
    await ws.accept()
    _clients.add(ws)
    logger.info("WS client connected (%d total)", len(_clients))
    try:
        # Keep connection open; read loop just absorbs pings / client msgs.
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
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
