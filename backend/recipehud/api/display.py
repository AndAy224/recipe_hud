from fastapi import APIRouter, Depends, Request

from .auth import require_admin

router = APIRouter(prefix="/api", tags=["display"])


@router.get("/display")
async def display_state(request: Request):
    return request.app.state.idle.status()


@router.post("/display/on", dependencies=[Depends(require_admin)])
async def display_on(request: Request):
    await request.app.state.idle.wake()
    return request.app.state.idle.status()


@router.post("/display/off", dependencies=[Depends(require_admin)])
async def display_off(request: Request):
    await request.app.state.idle.force_off()
    return request.app.state.idle.status()


@router.post("/display/show-clock")
async def show_clock(request: Request):
    await request.app.state.idle.show_clock()
    return request.app.state.idle.status()


@router.post("/activity")
async def activity(request: Request):
    request.app.state.idle.activity("http")
    return {"ok": True}
