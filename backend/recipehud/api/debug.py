"""Dev-only endpoints (RECIPEHUD_DEBUG=1) to exercise the idle state machine
without hardware: force states and simulate a touch."""

from fastapi import APIRouter, HTTPException, Request

router = APIRouter(prefix="/api/debug", tags=["debug"])


@router.post("/idle/{state}")
async def force_idle_state(request: Request, state: str):
    idle = request.app.state.idle
    if state == "active":
        await idle.wake()
    elif state == "clock":
        await idle.show_clock()
    elif state == "off":
        await idle.force_off()
    else:
        raise HTTPException(404, "Unknown state")
    return idle.status()


@router.post("/touch")
async def simulate_touch(request: Request):
    request.app.state.idle.activity("debug")
    return request.app.state.idle.status()


@router.post("/screensaver/warm")
async def warm_screensaver(request: Request):
    """Run a screensaver cache warm pass now instead of waiting for the boot one."""
    from ..screensaver import warm

    return await warm(request.app.state.db, request.app.state.store)
