"""Seasonal screensaver deck for the idle CLOCK screen (see screensaver.py)."""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .. import screensaver
from .recipe import save_url

router = APIRouter(prefix="/api/screensaver", tags=["screensaver"])


class AddBody(BaseModel):
    url: str


@router.get("")
async def get_deck(request: Request):
    """{active, theme, items}. `active` is false out of season or when the
    screensaver is switched off — clients then show the plain clock."""
    deck = screensaver.active_deck(request.app.state.store)
    if not deck:
        return {"active": False, "theme": None, "items": []}
    items = await screensaver.deck_items(request.app.state.db, deck)
    return {"active": bool(items), "theme": deck["theme"], "items": items}


@router.post("/add")
async def add_to_my_recipes(request: Request, body: AddBody):
    """Save a screensaver card into My Recipes, tagged with the deck's tag and
    the card's category. Only deck URLs are accepted (it's not a general save)."""
    deck = screensaver.active_deck(request.app.state.store)
    item = screensaver.find_item(deck, body.url) if deck else None
    if not item:
        raise HTTPException(404, "Not a card in the current screensaver deck")
    tags = [deck.get("tag") or deck["theme"], screensaver.CATEGORY_TAGS[item["category"]]]
    return await save_url(request.app.state, item["url"], add_tags=tags)
