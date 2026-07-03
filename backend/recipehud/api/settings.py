from fastapi import APIRouter, Depends, Request

from ..models import PasswordBody
from .auth import require_admin

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("")
async def get_settings(request: Request):
    return request.app.state.store.public()


@router.patch("", dependencies=[Depends(require_admin)])
async def patch_settings(request: Request):
    body = await request.json()
    changed = await request.app.state.store.patch(body)
    if changed:
        await request.app.state.hub.broadcast("settings.updated", changed)
    return request.app.state.store.public()


@router.post("/password", dependencies=[Depends(require_admin)])
async def set_password(request: Request, body: PasswordBody):
    await request.app.state.store.set_admin_password(body.password)
    return {"ok": True}
