from fastapi import APIRouter, Request

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("")
async def get_settings(request: Request):
    return request.app.state.store.public()


@router.patch("")
async def patch_settings(request: Request):
    body = await request.json()
    changed = await request.app.state.store.patch(body)
    if changed:
        await request.app.state.hub.broadcast("settings.updated", changed)
    return request.app.state.store.public()

