"""Seasonal screensaver decks (decks/*.json).

A deck is a curated list of real recipe URLs with a short display name, a
category and a one-line blurb, plus the date window it is "in season". While
one is in season (and `screensaver_enabled`), the idle CLOCK state renders it
as a rotating photo screensaver and stops timing out to OFF in the daytime
(see idle.py).

A deck is data, not code: its `ui` block carries everything the screensaver
shows that is specific to it (category labels, colours, effects, the
countdown line), so a new season is a new JSON file. When seasons overlap the
deck with the highest `priority` wins — unless one is picked by hand: the
`screensaver_theme` setting (admin panel) names a deck to show whatever the
date, and "auto" goes back to the calendar.

Out of season there is a second, built-in deck: the user's own saved recipes
(`saved_deck`, setting `screensaver_saved_enabled`). It only changes what the
CLOCK state looks like — unlike a seasonal deck it does not hold the screen
on, so CLOCK still times out to OFF as usual.

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
import re
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

EFFECTS = ("lights", "snow", "leaves")
_HEX = re.compile(r"#[0-9a-fA-F]{3,8}")
_MM_DD = re.compile(r"\d{2}-\d{2}")

# The saved-recipes deck needs a few photos to be worth rotating.
SAVED_DECK_MIN = 3
SAVED_DECK_MAX = 60
SAVED_UI = {
    "ideas_title": "",
    "hint": "Tap to wake",
    "default_emoji": "🍽",
    "categories": {},
    "palette": {
        "bg": ["#2b3140", "#171a22", "#0a0b0f"],
        "gold": "#e0a15f",
        "cream": "#f2f0eb",
        "soft": "#b4bac6",
        "accent": "#e07a5f",
    },
    "effects": [],
    "countdown": None,
}

WARM_DELAY_S = 90        # let boot settle before fetching anything
WARM_EVERY_S = 24 * 3600
WARM_GAP_S = 3           # between fetches: polite to the sites, light on the Pi

def _month_day(value: str) -> tuple[int, int]:
    month, day = value.split("-")
    return int(month), int(day)


def _validate_ui(ui: dict, where: str) -> dict:
    """Normalize a deck's `ui` block, failing loudly on a typo. A bad deck
    should stop the backend at startup, not render a half-themed screensaver."""
    def fail(reason: str):
        raise ValueError(f"{where}: ui.{reason}")

    if not isinstance(ui, dict):
        fail("must be an object")
    out = {
        "ideas_title": str(ui.get("ideas_title") or ""),
        "hint": str(ui.get("hint") or "Tap to wake"),
        "default_emoji": str(ui.get("default_emoji") or "🍽"),
        "categories": {},
        "palette": {},
        "effects": [],
        "countdown": None,
    }
    for key, cat in (ui.get("categories") or {}).items():
        if key not in CATEGORIES:
            fail(f"categories has unknown category {key!r}")
        if not isinstance(cat, dict) or not cat.get("label") or not _HEX.fullmatch(str(cat.get("color", ""))):
            fail(f"categories.{key} needs a label and a #hex color")
        out["categories"][key] = {"label": str(cat["label"]), "color": cat["color"]}
    palette = ui.get("palette") or {}
    for key, value in palette.items():
        if key == "bg":
            if not (isinstance(value, list) and len(value) == 3 and all(_HEX.fullmatch(str(v)) for v in value)):
                fail("palette.bg must be three #hex colours (top, middle, bottom)")
        elif key in ("gold", "cream", "soft", "accent"):
            if not _HEX.fullmatch(str(value)):
                fail(f"palette.{key} must be a #hex colour")
        else:
            fail(f"palette has unknown key {key!r}")
        out["palette"][key] = value
    for effect in ui.get("effects") or []:
        if effect not in EFFECTS:
            fail(f"effects has unknown effect {effect!r} (known: {', '.join(EFFECTS)})")
        out["effects"].append(effect)
    countdown = ui.get("countdown")
    if countdown:
        to = countdown.get("to")
        if isinstance(to, dict):
            # e.g. {"month": 11, "weekday": 4, "nth": 4}: 4th Thursday of November
            # (weekday 1 = Monday … 7 = Sunday).
            if not (1 <= to.get("month", 0) <= 12 and 1 <= to.get("weekday", 0) <= 7
                    and 1 <= to.get("nth", 0) <= 5):
                fail("countdown.to rule needs month 1-12, weekday 1-7 (Mon-Sun), nth 1-5")
        elif not (isinstance(to, str) and _MM_DD.fullmatch(to)):
            fail("countdown.to must be MM-DD or a {month, weekday, nth} rule")
        if "{n}" not in str(countdown.get("many", "")):
            fail("countdown.many must contain {n}")
        days = countdown.get("days") or {}
        if not all(_MM_DD.fullmatch(str(k)) for k in days):
            fail("countdown.days keys must be MM-DD")
        out["countdown"] = {
            "to": to,
            "many": str(countdown["many"]),
            "one": str(countdown.get("one") or countdown["many"].replace("{n}", "1")),
            "days": {str(k): str(v) for k, v in days.items()},
            "today": str(countdown.get("today") or ""),
            "after": str(countdown.get("after") or ""),
        }
    return out


@lru_cache
def load_decks() -> tuple[dict, ...]:
    decks = []
    for path in sorted(DECK_DIR.glob("*.json")):
        deck = json.loads(path.read_text(encoding="utf-8"))
        deck.setdefault("theme", path.stem)
        deck["theme"] = str(deck["theme"]).lower()
        deck.setdefault("name", deck["theme"].replace("-", " ").title())
        deck["priority"] = int(deck.get("priority", 0))
        deck["ui"] = _validate_ui(deck.get("ui") or {}, path.name)
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


def calendar_deck(today: datetime.date | None = None) -> dict | None:
    """The deck the calendar picks: in season today, highest priority first
    (file order breaks a tie)."""
    today = today or datetime.date.today()
    candidates = [deck for deck in load_decks() if deck["items"] and in_season(deck, today)]
    return max(candidates, key=lambda deck: deck["priority"], default=None)


def active_deck(store: SettingsStore, today: datetime.date | None = None) -> dict | None:
    if not store.get("screensaver_enabled"):
        return None
    choice = store.get("screensaver_theme")
    if choice != "auto":
        # Picked by hand in the admin panel: shown whatever the date. A name
        # that no longer matches a deck (its file was removed) falls back to
        # the calendar instead of leaving the screen blank.
        picked = next((deck for deck in load_decks()
                       if deck["theme"] == choice and deck["items"]), None)
        if picked:
            return picked
    return calendar_deck(today)


def deck_choices(store: SettingsStore, today: datetime.date | None = None) -> dict:
    """What the admin panel's theme picker needs: every deck, which one the
    calendar would pick today, and the current selection."""
    today = today or datetime.date.today()
    auto = calendar_deck(today)
    return {
        "selected": store.get("screensaver_theme"),
        "auto": auto["theme"] if auto else None,
        "decks": [{
            "theme": deck["theme"],
            "name": deck["name"],
            "season": deck["season"],
            "in_season": in_season(deck, today),
            "cards": len(deck["items"]),
        } for deck in load_decks() if deck["items"]],
    }


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


async def saved_deck_items(db: Database) -> list[dict]:
    """The out-of-season deck: the user's own saved recipes that have a local
    photo, newest first. Same item shape as deck_items()."""
    rows = await db.fetchall(
        "SELECT url, title, total_time_s, yields, image_local, source_host, meta_json "
        "FROM recipe_cache WHERE saved = 1 AND kind = 'recipe' AND image_local IS NOT NULL "
        "ORDER BY saved_at DESC LIMIT ?", (SAVED_DECK_MAX,))
    items = []
    for row in rows:
        meta = json.loads(row["meta_json"]) if row["meta_json"] else {}
        user = meta.get("user") if isinstance(meta.get("user"), dict) else {}
        note = " ".join(str(user.get("notes") or "").split())
        # The cook's own note is the best one-liner there is; else say where it's from.
        blurb = (note[:117] + "…" if len(note) > 118 else note) or f"from {row['source_host']}"
        items.append({
            "url": row["url"],
            "name": row["title"],
            "category": None,
            "emoji": SAVED_UI["default_emoji"],
            "blurb": blurb,
            "image": local_image_url(row),
            "saved": True,
            "total_time_s": row["total_time_s"],
            "yields": row["yields"],
        })
    return items


async def current_deck(db: Database, store: SettingsStore) -> dict:
    """What the CLOCK screen should show right now, as the API payload:
    {active, kind, theme, addable, ui, items}. `kind` is "seasonal" (a deck in
    season — holds the screen on, cards can be added to My Recipes) or "saved"
    (the user's own recipes — purely cosmetic); inactive means the plain clock."""
    deck = active_deck(store)
    if deck:
        items = await deck_items(db, deck)
        if items:
            return {"active": True, "kind": "seasonal", "theme": deck["theme"],
                    "addable": True, "ui": deck["ui"], "items": items}
    elif store.get("screensaver_saved_enabled"):
        items = await saved_deck_items(db)
        if len(items) >= SAVED_DECK_MIN:
            return {"active": True, "kind": "saved", "theme": "saved",
                    "addable": False, "ui": SAVED_UI, "items": items}
    return {"active": False, "kind": None, "theme": None, "addable": False,
            "ui": None, "items": []}


_warm_lock = asyncio.Lock()


async def warm(db: Database, store: SettingsStore) -> dict:
    """Make sure every card of the active deck has a cached recipe and a local
    photo. Rows that already have both are left alone (no refetch), so after
    the first pass this costs nothing but retries of earlier failures."""
    # One pass at a time: the daily loop and a theme change can coincide.
    async with _warm_lock:
        return await _warm(db, store)


async def _warm(db: Database, store: SettingsStore) -> dict:
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
