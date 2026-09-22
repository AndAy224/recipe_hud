import asyncio
import contextlib
import logging

from fastapi import WebSocket

log = logging.getLogger(__name__)

# A client that can't absorb a small JSON frame in this long is wedged (frozen
# renderer, half-open TCP). Without a bound, one such client blocks the idle
# loop, the 1 Hz timer tick and every broadcasting route — the whole appliance
# goes unresponsive while the socket looks perfectly healthy.
SEND_TIMEOUT_S = 5.0


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
        clients = list(self._clients)
        if not clients:
            return
        # Concurrently, so one slow client doesn't delay the rest either.
        results = await asyncio.gather(
            *(self._send(ws, message) for ws in clients), return_exceptions=True)
        for ws, ok in zip(clients, results):
            if ok is not True:
                self.disconnect(ws)

    async def _send(self, ws: WebSocket, message: dict) -> bool:
        try:
            await asyncio.wait_for(ws.send_json(message), SEND_TIMEOUT_S)
            return True
        except asyncio.TimeoutError:
            log.warning("ws client %s stalled; dropping", self._clients.get(ws, "?"))
        except Exception:
            log.debug("ws send failed", exc_info=True)
        # The frame was cut mid-write, so the stream is unusable either way.
        # Bound the close too — it writes a frame to the same wedged peer.
        with contextlib.suppress(Exception):
            await asyncio.wait_for(ws.close(code=1011), 2)
        return False

    def broadcast_soon(self, type_: str, data) -> None:
        """Fire-and-forget broadcast from sync contexts."""
        asyncio.get_running_loop().create_task(self.broadcast(type_, data))
