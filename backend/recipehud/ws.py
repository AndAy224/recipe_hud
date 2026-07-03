import asyncio
import logging

from fastapi import WebSocket

log = logging.getLogger(__name__)


class Hub:
    """WebSocket fan-out. Events flow server -> clients; commands use REST."""

    def __init__(self):
        self._clients: dict[WebSocket, str] = {}

    async def connect(self, ws: WebSocket, role: str) -> None:
        await ws.accept()
        self._clients[ws] = role

    def disconnect(self, ws: WebSocket) -> None:
        self._clients.pop(ws, None)

    def client_count(self) -> int:
        return len(self._clients)

    async def broadcast(self, type_: str, data) -> None:
        message = {"type": type_, "data": data}
        dead = []
        for ws in list(self._clients):
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)

    def broadcast_soon(self, type_: str, data) -> None:
        """Fire-and-forget broadcast from sync contexts."""
        asyncio.get_event_loop().create_task(self.broadcast(type_, data))
