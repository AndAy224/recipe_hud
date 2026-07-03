from fastapi import APIRouter, HTTPException, Query, Request

from ..extractor import ExtractionError, extract

router = APIRouter(prefix="/api/recipe", tags=["recipe"])


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
