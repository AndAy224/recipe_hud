import datetime
from collections import Counter

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from ..extractor import (
    ExtractionError, delete_image_snapshot, extract, local_image_url, snapshot_image,
)
from ..models import RecipeUrlBody, RenameBody, TagsBody
from .auth import require_admin

router = APIRouter(prefix="/api/recipe", tags=["recipe"])

SAVED_SUMMARY = (
    "SELECT url, title, image_url, image_local, yields, total_time_s, source_host, "
    "saved_at, tags FROM recipe_cache WHERE saved = 1"
)


def _summary(row: dict) -> dict:
    row["image_url"] = local_image_url(row) or row["image_url"]
    row.pop("image_local", None)
    row["tags"] = [t for t in (row.get("tags") or "").split(",") if t]
    return row


def normalize_tags(tags: list[str]) -> list[str]:
    seen = []
    for tag in tags:
        tag = tag.strip().lower()[:30]
        if tag and tag not in seen:
            seen.append(tag)
    return seen[:10]


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
async def list_saved(request: Request, q: str | None = None, tag: str | None = None):
    sql = SAVED_SUMMARY
    params: list = []
    if q:
        escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        sql += r" AND title LIKE ? ESCAPE '\'"
        params.append(f"%{escaped}%")
    if tag:
        sql += " AND ',' || tags || ',' LIKE '%,' || ? || ',%'"
        params.append(tag.strip().lower())
    rows = await request.app.state.db.fetchall(sql + " ORDER BY saved_at DESC", tuple(params))
    return [_summary(row) for row in rows]


@router.get("/tags")
async def list_tags(request: Request):
    rows = await request.app.state.db.fetchall(
        "SELECT tags FROM recipe_cache WHERE saved = 1 AND tags != ''")
    counts = Counter(t for row in rows for t in row["tags"].split(",") if t)
    return [{"tag": tag, "count": count}
            for tag, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]


@router.post("/tags", dependencies=[Depends(require_admin)])
async def set_tags(request: Request, body: TagsBody):
    db = request.app.state.db
    url = str(body.url)
    tags = ",".join(normalize_tags(body.tags))
    row = await db.fetchone(
        "SELECT url FROM recipe_cache WHERE url = ? AND saved = 1", (url,))
    if not row:
        raise HTTPException(404, "Not a saved recipe")
    await db.execute("UPDATE recipe_cache SET tags = ? WHERE url = ?", (tags, url))
    await request.app.state.hub.broadcast("recipes.updated", {})
    return _summary(await db.fetchone(SAVED_SUMMARY + " AND url = ?", (url,)))


@router.post("/rename", dependencies=[Depends(require_admin)])
async def rename_recipe(request: Request, body: RenameBody):
    db = request.app.state.db
    url = str(body.url)
    title = body.title.strip()
    if not title:
        raise HTTPException(400, "Title must not be empty")
    row = await db.fetchone(
        "SELECT url FROM recipe_cache WHERE url = ? AND saved = 1", (url,))
    if not row:
        raise HTTPException(404, "Not a saved recipe")
    await db.execute("UPDATE recipe_cache SET title = ? WHERE url = ?", (title, url))
    await request.app.state.hub.broadcast("recipes.updated", {})
    return _summary(await db.fetchone(SAVED_SUMMARY + " AND url = ?", (url,)))


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
    await snapshot_image(db, url)  # best-effort; remote URL stays as fallback
    await request.app.state.hub.broadcast("recipes.updated", {})
    return _summary(await db.fetchone(SAVED_SUMMARY + " AND url = ?", (url,)))


@router.post("/unsave")
async def unsave_recipe(request: Request, body: RecipeUrlBody):
    db = request.app.state.db
    await delete_image_snapshot(db, str(body.url))
    await db.execute(
        "UPDATE recipe_cache SET saved = 0, saved_at = NULL WHERE url = ?", (str(body.url),))
    await request.app.state.hub.broadcast("recipes.updated", {})
    return {"ok": True}
