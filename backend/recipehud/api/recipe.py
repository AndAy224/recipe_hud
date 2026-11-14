import datetime
import json
from collections import Counter

from fastapi import APIRouter, HTTPException, Query, Request

from ..extractor import (
    ExtractionError, canonical_url, delete_image_snapshot, extract, local_image_url,
    snapshot_image,
)
from ..models import RecipeUrlBody, RenameBody, TagsBody
from ..screensaver import deck_category
from ..wine import is_drink, suggest_wine

# A rule-table pairing cached while an API key is configured means the LLM
# call failed that once (offline, timeout). Offer it, but try again after this
# long instead of pinning the fallback to the recipe forever.
RULE_RETRY_S = 3600


def _query_url(url: str) -> str:
    """Canonicalize a ?url= parameter the same way request bodies are."""
    try:
        return canonical_url(url)
    except ValueError:
        raise HTTPException(400, "URL must be a valid http(s) address")


def _stale_fallback(pairing: dict, api_key: str) -> bool:
    if pairing.get("source") != "rule" or not api_key:
        return False
    try:
        made = datetime.datetime.fromisoformat(pairing["at"])
    except (KeyError, TypeError, ValueError):
        return True  # cached before timestamps existed
    age = (datetime.datetime.now(datetime.timezone.utc) - made).total_seconds()
    return age > RULE_RETRY_S

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
        # Tags are stored comma-joined, so a comma inside one would split it.
        tag = " ".join(tag.replace(",", " ").split()).lower()[:30]
        if tag and tag not in seen:
            seen.append(tag)
    return seen[:10]


@router.get("/extract")
async def extract_recipe(
    request: Request,
    url: str = Query(min_length=10),
    refresh: bool = False,
):
    url = _query_url(url)
    try:
        return await extract(request.app.state.db, request.app.state.store, url, refresh)
    except ExtractionError as exc:
        raise HTTPException(422, str(exc))


@router.get("/wine")
async def wine_pairing(request: Request, url: str = Query(min_length=10), refresh: bool = False):
    """Lazily produce a wine pairing for a (previously extracted) recipe.

    Kept off the /extract path so the recipe view paints immediately. The
    result is cached into the row's meta_json, so re-views are instant and it
    rides along with saved recipes offline. Returns {} when there's no pairing
    (non-recipe page, feature disabled, or the recipe isn't cached yet).
    refresh=1 forces regeneration, overwriting any cached pairing — used by the
    clean view's Re-fetch button."""
    db = request.app.state.db
    url = _query_url(url)
    row = await db.fetchone("SELECT * FROM recipe_cache WHERE url = ?", (url,))
    if not row:
        return {}
    meta = json.loads(row["meta_json"]) if row["meta_json"] else {}
    recipe = {
        "url": url,
        "kind": row["kind"],
        "title": row["title"],
        "ingredients": json.loads(row["ingredients_json"]),
        "meta": meta,
    }
    # Before the cache: a pairing generated for a cocktail before this check
    # existed must not keep being served (and is dropped from the row).
    if is_drink(recipe):
        if meta.pop("wine_pairing", None):
            await db.execute(
                "UPDATE recipe_cache SET meta_json = ? WHERE url = ?", (json.dumps(meta), url))
        return {}
    cached = meta.get("wine_pairing")
    api_key = request.app.state.cfg.anthropic_api_key
    if cached and not refresh and not _stale_fallback(cached, api_key):
        return cached
    pairing = await suggest_wine(recipe, request.app.state.cfg, request.app.state.store)
    if not pairing:
        return {}
    pairing["at"] = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    # Re-read before writing: the LLM call can take seconds, and a notes edit
    # or re-fetch landing meanwhile must not be overwritten with a stale copy.
    fresh = await db.fetchone("SELECT meta_json FROM recipe_cache WHERE url = ?", (url,))
    if fresh is not None:
        meta = json.loads(fresh["meta_json"]) if fresh["meta_json"] else {}
        meta["wine_pairing"] = pairing
        await db.execute(
            "UPDATE recipe_cache SET meta_json = ? WHERE url = ?", (json.dumps(meta), url))
    return pairing


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


@router.post("/tags")
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


@router.post("/rename")
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


async def save_url(state, url: str, add_tags: list[str] | None = None) -> dict:
    """Mark a recipe saved (extracting it first if needed), optionally merging
    tags, and tell clients. Shared by /save and the screensaver's add."""
    db = state.db
    try:
        await extract(db, state.store, url)
    except ExtractionError as exc:
        raise HTTPException(422, str(exc))
    saved_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    await db.execute(
        "UPDATE recipe_cache SET saved = 1, saved_at = ? WHERE url = ?", (saved_at, url))
    if add_tags:
        row = await db.fetchone("SELECT tags FROM recipe_cache WHERE url = ?", (url,))
        existing = [t for t in (row["tags"] or "").split(",") if t]
        tags = ",".join(normalize_tags(existing + add_tags))
        await db.execute("UPDATE recipe_cache SET tags = ? WHERE url = ?", (tags, url))
    await snapshot_image(db, url)  # best-effort; remote URL stays as fallback
    await state.hub.broadcast("recipes.updated", {})
    return _summary(await db.fetchone(SAVED_SUMMARY + " AND url = ?", (url,)))


@router.post("/save")
async def save_recipe(request: Request, body: RecipeUrlBody):
    return await save_url(request.app.state, str(body.url))


@router.post("/unsave")
async def unsave_recipe(request: Request, body: RecipeUrlBody):
    db = request.app.state.db
    url = str(body.url)
    # A screensaver card keeps its local photo: the deck shows it whether or
    # not the recipe is saved, and the warm pass only re-fetches once a day.
    if not deck_category(url):
        await delete_image_snapshot(db, url)
    await db.execute(
        "UPDATE recipe_cache SET saved = 0, saved_at = NULL WHERE url = ?", (url,))
    await request.app.state.hub.broadcast("recipes.updated", {})
    return {"ok": True}
