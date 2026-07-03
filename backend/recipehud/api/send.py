"""Send-to-kiosk: a phone on the LAN pushes a URL to the kiosk display.
Open (no auth) by design — the whole point is zero-friction from a phone;
HttpUrl validation blocks javascript:/file: payloads. See ARCHITECTURE.md."""

import io
import socket
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request, Response

from ..config import CONFIG
from ..models import SendBody

router = APIRouter(prefix="/api/send", tags=["send"])


def _lan_ip() -> str | None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # UDP connect sends no packets; it just resolves the outbound interface.
        sock.connect(("192.0.2.1", 80))
        ip = sock.getsockname()[0]
    except OSError:
        ip = None
    finally:
        sock.close()
    if not ip or ip.startswith("127."):
        return None
    return ip


def _send_url() -> str | None:
    ip = _lan_ip()
    return f"http://{ip}:{CONFIG.port}/send" if ip else None


@router.post("")
async def send(request: Request, body: SendBody):
    target = str(body.url)
    if body.clean:
        # Absolute: the overlay on an external site must not resolve this
        # against that site's origin.
        target = CONFIG.launcher_url.rstrip("/") + "/recipe?url=" + quote(target, safe="")
    await request.app.state.idle.wake()  # panel may be dark
    await request.app.state.hub.broadcast("navigate", {"url": target})
    return {"ok": True, "clients": request.app.state.hub.client_count()}


@router.get("/info")
async def info():
    return {"send_url": _send_url(), "lan_ip": _lan_ip()}


@router.get("/qr.svg")
async def qr():
    import segno

    url = _send_url()
    if not url:
        raise HTTPException(404, "No LAN address detected")
    buf = io.BytesIO()
    segno.make(url, error="m").save(buf, kind="svg", scale=6, dark="#f2f0eb", light=None)
    return Response(content=buf.getvalue(), media_type="image/svg+xml")
