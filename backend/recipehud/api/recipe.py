import datetime

from fastapi import APIRouter, HTTPException, Query, Request

from ..extractor import ExtractionError, extract
from ..models import RecipeUrlBody

router = APIRouter(prefix="/api/recipe", tags=["recipe"])

SAVED_SUMMARY = (
    "SELECT url, title, image_url, yields, total_time_s, source_host, saved_at "
    "FROM recipe_cache WHERE saved = 1"
)


@router.get("/extract")
async def extract_recipe(
    request: Request,
    url: str = Query(min_length=10),
    refresh: bool = False,
):
    if not url.startswith(("http://", "https://")):
        raise HTTPException(400, "URL must be http(s)")
    try:
        return await extract(request.app.state.db, request.app.state.store, url, refresh)
    except ExtractionError as exc:
        raise HTTPException(422, str(exc))


@router.get("/saved")
async def list_saved(request: Request):
    return await request.app.state.db.fetchall(SAVED_SUMMARY + " ORDER BY saved_at DESC")


@router.post("/save")
async def save_recipe(request: Request, body: RecipeUrlBody):
    url = str(body.url)
    db = request.app.state.db
    try:
        await extract(db, request.app.state.store, url)
    except ExtractionError as exc:
        raise HTTPException(422, str(exc))
    saved_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    await db.execute(
        "UPDATE recipe_cache SET saved = 1, saved_at = ? WHERE url = ?", (saved_at, url))
    await request.app.state.hub.broadcast("recipes.updated", {})
    return await db.fetchone(SAVED_SUMMARY + " AND url = ?", (url,))


@router.post("/unsave")
async def unsave_recipe(request: Request, body: RecipeUrlBody):
    await request.app.state.db.execute(
        "UPDATE recipe_cache SET saved = 0, saved_at = NULL WHERE url = ?", (str(body.url),))
    await request.app.state.hub.broadcast("recipes.updated", {})
    return {"ok": True}
