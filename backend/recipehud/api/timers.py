from fastapi import APIRouter, Depends, HTTPException, Request

from ..models import ExtendBody, PresetIn, TimerCreate
from .auth import require_admin

router = APIRouter(prefix="/api", tags=["timers"])


def _engine(request: Request):
    return request.app.state.engine


@router.get("/timers")
async def list_timers(request: Request):
    return _engine(request).list()


@router.post("/timers")
async def create_timer(request: Request, body: TimerCreate):
    return await _engine(request).create(body.label, body.seconds)


@router.post("/timers/{timer_id}/{action}")
async def timer_action(request: Request, timer_id: str, action: str, body: ExtendBody | None = None):
    engine = _engine(request)
    try:
        if action == "pause":
            return await engine.pause(timer_id)
        if action == "resume":
            return await engine.resume(timer_id)
        if action == "extend":
            seconds = body.seconds if body else 60
            return await engine.extend(timer_id, seconds)
        if action == "cancel":
            await engine.cancel(timer_id)
            return {"ok": True}
        if action == "dismiss":
            await engine.dismiss(timer_id)
            return {"ok": True}
    except KeyError:
        raise HTTPException(404, "Timer not found")
    raise HTTPException(404, "Unknown action")


# -- presets ------------------------------------------------------------

@router.get("/presets")
async def list_presets(request: Request):
    return await request.app.state.db.fetchall(
        "SELECT * FROM timer_presets ORDER BY position, id")


@router.post("/presets", dependencies=[Depends(require_admin)])
async def create_preset(request: Request, body: PresetIn):
    db = request.app.state.db
    row = await db.fetchone(
        "SELECT COALESCE(MAX(position), -1) + 1 AS pos FROM timer_presets")
    preset_id = await db.execute(
        "INSERT INTO timer_presets (label, seconds, position) VALUES (?, ?, ?)",
        (body.label, body.seconds, row["pos"]),
    )
    return await db.fetchone("SELECT * FROM timer_presets WHERE id = ?", (preset_id,))


@router.patch("/presets/{preset_id}", dependencies=[Depends(require_admin)])
async def update_preset(request: Request, preset_id: int, body: PresetIn):
    db = request.app.state.db
    await db.execute(
        "UPDATE timer_presets SET label = ?, seconds = ? WHERE id = ?",
        (body.label, body.seconds, preset_id),
    )
    preset = await db.fetchone("SELECT * FROM timer_presets WHERE id = ?", (preset_id,))
    if not preset:
        raise HTTPException(404, "Preset not found")
    return preset


@router.delete("/presets/{preset_id}", dependencies=[Depends(require_admin)])
async def delete_preset(request: Request, preset_id: int):
    await request.app.state.db.execute(
        "DELETE FROM timer_presets WHERE id = ?", (preset_id,))
    return {"ok": True}
