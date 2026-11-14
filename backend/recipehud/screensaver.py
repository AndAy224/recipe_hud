"""Seasonal screensaver decks (decks/*.json).

A deck is a curated list of real recipe URLs with a short display name, a
category and a one-line blurb, plus the date window it is "in season". While
one is in season (and `screensaver_enabled`), the idle CLOCK state renders it
as a rotating photo screensaver and stops timing out to OFF in the daytime
(see idle.py).

Photos come from the normal extraction cache: `warm()` runs each deck URL
through extract() + snapshot_image() so every card has a *local* /media copy.
Cards never hotlink the source CDN — the overlay paints them on arbitrary
recipe sites, and the screensaver should keep working offline.

Every deck URL must be verified to extract as kind=recipe with an image
before it is added (curl /api/recipe/extract?url=...&refresh=1).
"""

import asyncio
import datetime
import json
import logging
from functools import lru_cache
from pathlib import Path

from .db import Database
from .extractor import (
    ExtractionError, canonical_url as _canonical, extract, local_image_url, snapshot_image,
)
from .settings_store import SettingsStore

log = logging.getLogger(__name__)

DECK_DIR = Path(__file__).with_name("decks")
CATEGORIES = ("drink", "main", "side", "dessert")
# Tag added (with "christmas"/the deck's tag) when a card is saved.
CATEGORY_TAGS = {"drink": "drinks", "main": "mains", "side": "sides", "dessert": "dessert"}

WARM_DELAY_S = 90        # let boot settle before fetching anything
WARM_EVERY_S = 24 * 3600
WARM_GAP_S = 3           # between fetches: polite to the sites, light on the Pi

def _month_day(value: str) -> tuple[int, int]:
    month, day = value.split("-")
    return int(month), int(day)


@lru_cache
def load_decks() -> tuple[dict, ...]:
    decks = []
    for path in sorted(DECK_DIR.glob("*.json")):
        deck = json.loads(path.read_text(encoding="utf-8"))
        deck.setdefault("theme", path.stem)
        deck["start"] = _month_day(deck["season"]["start"])
        deck["end"] = _month_day(deck["season"]["end"])
        for item in deck["items"]:
            item["url"] = _canonical(item["url"])
            if item["category"] not in CATEGORIES:
                raise ValueError(f"{path.name}: bad category {item['category']!r}")
        decks.append(deck)
    return tuple(decks)


def in_season(deck: dict, today: datetime.date) -> bool:
    now = (today.month, today.day)
    start, end = deck["start"], deck["end"]
    if start <= end:
        return start <= now <= end
    return now >= start or now <= end  # window wraps past Jan 1


def active_deck(store: SettingsStore, today: datetime.date | None = None) -> dict | None:
    if not store.get("screensaver_enabled"):
        return None
    today = today or datetime.date.today()
    for deck in load_decks():
        if deck["items"] and in_season(deck, today):
            return deck
    return None


def deck_category(url: str) -> str | None:
    """A URL's category in ANY deck, in season or not (a cocktail stays a
    cocktail after Jan 6). Used to keep wine pairings off the drinks."""
    try:
        url = _canonical(url)
    except ValueError:
        return None
    for deck in load_decks():
        for item in deck["items"]:
            if item["url"] == url:
                return item["category"]
    return None


def find_item(deck: dict, url: str) -> dict | None:
    try:
        url = _canonical(url)
    except ValueError:
        return None
    return next((item for item in deck["items"] if item["url"] == url), None)


async def deck_items(db: Database, deck: dict) -> list[dict]:
    urls = [item["url"] for item in deck["items"]]
    if not urls:
        return []
    rows = await db.fetchall(
        "SELECT url, kind, saved, total_time_s, yields, image_local FROM recipe_cache "
        f"WHERE url IN ({','.join('?' * len(urls))})", tuple(urls))
    by_url = {row["url"]: row for row in rows}
    items = []
    for item in deck["items"]:
        row = by_url.get(item["url"]) or {}
        items.append({
            "url": item["url"],
            "name": item["name"],
            "category": item["category"],
            "emoji": item.get("emoji") or "🎄",
            "blurb": item.get("blurb") or "",
            "image": local_image_url(row) if row else None,
            "saved": bool(row.get("saved")),
            # A curated value in the deck (even null, to hide it) beats the
            # extracted one: sites report e.g. "3 servings" for 3 quarts of nog.
            "total_time_s": item["total_time_s"] if "total_time_s" in item
                            else row.get("total_time_s"),
            "yields": item["yields"] if "yields" in item else row.get("yields"),
        })
    return items


async def warm(db: Database, store: SettingsStore) -> dict:
    """Make sure every in-season card has a cached recipe and a local photo.
    Rows that already have both are left alone (no refetch), so after the
    first pass this costs nothing but retries of earlier failures."""
    deck = active_deck(store)
    if not deck:
        return {"active": False}
    stats = {"active": True, "ok": 0, "fetched": 0, "failed": []}
    for item in deck["items"]:
        url = item["url"]
        row = await db.fetchone(
            "SELECT kind, image_local FROM recipe_cache WHERE url = ?", (url,))
        if row and row["kind"] == "recipe" and row["image_local"]:
            stats["ok"] += 1
            continue
        try:
            data = await extract(db, store, url)
            if data["kind"] != "recipe":
                raise ExtractionError(f"extracted as {data['kind']}")
            if not await snapshot_image(db, url):
                raise ExtractionError("no image snapshot")
            stats["fetched"] += 1
        except Exception as exc:
            log.warning("screensaver warm: %s: %s", url, exc)
            stats["failed"].append(url)
        await asyncio.sleep(WARM_GAP_S)
    log.info("screensaver warm (%s): %s cached, %s fetched, %s failed",
             deck["theme"], stats["ok"], stats["fetched"], len(stats["failed"]))
    return stats


async def warm_loop(db: Database, store: SettingsStore) -> None:
    await asyncio.sleep(WARM_DELAY_S)
    while True:
        try:
            await warm(db, store)
        except Exception:
            log.exception("screensaver warm failed")
        await asyncio.sleep(WARM_EVERY_S)
