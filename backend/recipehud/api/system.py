import asyncio
import sys

from fastapi import APIRouter, Depends, HTTPException, Request

from ..models import NavigateBody
from .auth import require_admin

router = APIRouter(prefix="/api/system", tags=["system"])


@router.post("/kiosk/restart", dependencies=[Depends(require_admin)])
async def restart_kiosk():
    """Kill the kiosk Chromium; start-kiosk.sh's loop relaunches it."""
    if sys.platform != "linux":
        raise HTTPException(400, "Kiosk restart only works on the Pi")
    proc = await asyncio.create_subprocess_exec(
        "pkill", "-f", "recipehud-chromium",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await proc.communicate()
    return {"ok": True}


@router.post("/navigate", dependencies=[Depends(require_admin)])
async def navigate(request: Request, body: NavigateBody):
    """Push a URL to the kiosk (overlay/launcher navigates on the event)."""
    await request.app.state.hub.broadcast("navigate", {"url": str(body.url)})
    return {"ok": True}
