import datetime

from fastapi import APIRouter, Depends, HTTPException, Request

from ..models import ReorderBody, SiteIn, SiteUpdate
from .auth import require_admin

router = APIRouter(prefix="/api/sites", tags=["sites"])


@router.get("")
async def list_sites(request: Request):
    return await request.app.state.db.fetchall(
        "SELECT * FROM sites ORDER BY position, id")


@router.post("", dependencies=[Depends(require_admin)])
async def create_site(request: Request, body: SiteIn):
    db = request.app.state.db
    row = await db.fetchone("SELECT COALESCE(MAX(position), -1) + 1 AS pos FROM sites")
    site_id = await db.execute(
        "INSERT INTO sites (name, url, color, icon, position, open_mode) VALUES (?, ?, ?, ?, ?, ?)",
        (body.name, str(body.url), body.color, body.icon, row["pos"], body.open_mode),
    )
    return await db.fetchone("SELECT * FROM sites WHERE id = ?", (site_id,))


@router.patch("/{site_id}", dependencies=[Depends(require_admin)])
async def update_site(request: Request, site_id: int, body: SiteUpdate):
    db = request.app.state.db
    updates = body.model_dump(exclude_unset=True)
    if "url" in updates:
        updates["url"] = str(updates["url"])
    if updates:
        assignments = ", ".join(f"{key} = ?" for key in updates)
        await db.execute(
            f"UPDATE sites SET {assignments} WHERE id = ?",
            (*updates.values(), site_id),
        )
    site = await db.fetchone("SELECT * FROM sites WHERE id = ?", (site_id,))
    if not site:
        raise HTTPException(404, "Site not found")
    return site


@router.delete("/{site_id}", dependencies=[Depends(require_admin)])
async def delete_site(request: Request, site_id: int):
    await request.app.state.db.execute("DELETE FROM sites WHERE id = ?", (site_id,))
    return {"ok": True}


@router.post("/reorder", dependencies=[Depends(require_admin)])
async def reorder_sites(request: Request, body: ReorderBody):
    await request.app.state.db.executemany(
        "UPDATE sites SET position = ? WHERE id = ?",
        [(pos, site_id) for pos, site_id in enumerate(body.ids)],
    )
    return {"ok": True}


@router.post("/{site_id}/visit")
async def record_visit(request: Request, site_id: int):
    now = datetime.datetime.now().isoformat(timespec="seconds")
    await request.app.state.db.execute(
        "UPDATE sites SET visit_count = visit_count + 1, last_visited_at = ? WHERE id = ?",
        (now, site_id),
    )
    return {"ok": True}
