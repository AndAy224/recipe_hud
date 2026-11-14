from fastapi import APIRouter, HTTPException, Request

from ..settings_store import SettingError

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("")
async def get_settings(request: Request):
    return request.app.state.store.public()


@router.patch("")
async def patch_settings(request: Request):
    try:
        body = await request.json()
    except ValueError:
        raise HTTPException(422, "Body must be a JSON object of setting names to values")
    try:
        changed = await request.app.state.store.patch(body)
    except SettingError as exc:
        raise HTTPException(422, str(exc))
    if changed:
        await request.app.state.hub.broadcast("settings.updated", changed)
    return request.app.state.store.public()

