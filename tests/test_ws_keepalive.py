import asyncio
from datetime import datetime

from fastapi import WebSocketDisconnect
from fastapi.testclient import TestClient

from server.routes import ws as ws_route


def test_ws_jobs_sends_keepalive_ping(monkeypatch, temp_db_path):
    monkeypatch.setattr(ws_route, "KEEPALIVE_INTERVAL_SECONDS", 0.01)

    from server.app import app

    with TestClient(app) as client:
        with client.websocket_connect("/ws/jobs") as websocket:
            frame = websocket.receive_json()

    assert frame["event"] == "ping"
    assert isinstance(frame["ts"], str)
    assert datetime.fromisoformat(frame["ts"])


async def test_ws_jobs_cancels_keepalive_task_on_disconnect(monkeypatch):
    ws_route._clients.clear()
    keepalive_cancelled = False

    async def fake_keepalive(websocket):
        nonlocal keepalive_cancelled
        try:
            await websocket.blocker.wait()
        except asyncio.CancelledError:
            keepalive_cancelled = True
            raise

    class FakeWebSocket:
        def __init__(self):
            self.accepted = False
            self.blocker = asyncio.Event()

        async def accept(self):
            self.accepted = True

        async def receive_text(self):
            await asyncio.sleep(0)
            raise WebSocketDisconnect(code=1000)

    monkeypatch.setattr(ws_route, "_keepalive", fake_keepalive)
    websocket = FakeWebSocket()

    await ws_route.ws_jobs(websocket)

    assert websocket.accepted is True
    assert keepalive_cancelled is True
    assert websocket not in ws_route._clients
