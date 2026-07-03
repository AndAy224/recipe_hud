"""Clean-recipe extraction: fetch a page server-side, pull the recipe out of
its schema.org data (recipe-scrapers), fall back to readability article text.
Results are cached in recipe_cache so clean views also work offline."""

import datetime
import json
import logging
from urllib.parse import urlparse

import httpx

from .db import Database
from .settings_store import SettingsStore

log = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


class ExtractionError(Exception):
    pass


async def extract(db: Database, store: SettingsStore, url: str, refresh: bool = False) -> dict:
    cached = await db.fetchone("SELECT * FROM recipe_cache WHERE url = ?", (url,))
    if cached and not refresh and _age_days(cached["fetched_at"]) <= store.get("recipe_cache_max_age_days"):
        return _row_to_dict(cached)
    try:
        html = await _fetch(url)
    except Exception as exc:
        if cached:
            log.warning("re-fetch failed for %s, serving cached copy: %s", url, exc)
            return _row_to_dict(cached)
        raise ExtractionError(f"Could not fetch the page ({exc})") from exc
    data = _parse(html, url)
    await _save(db, data)
    return data


async def _fetch(url: str) -> str:
    async with httpx.AsyncClient(
        headers={"User-Agent": USER_AGENT, "Accept-Language": "en"},
        follow_redirects=True,
        timeout=20,
    ) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.text


def _parse(html: str, url: str) -> dict:
    data = _parse_recipe(html, url)
    if data is None:
        data = _parse_article(html, url)
    data["url"] = url
    data["source_host"] = urlparse(url).hostname or ""
    data["fetched_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    return data


def _parse_recipe(html: str, url: str) -> dict | None:
    from recipe_scrapers import scrape_html

    try:
        try:
            scraper = scrape_html(html, org_url=url, supported_only=False)
        except TypeError:  # older recipe-scrapers API
            scraper = scrape_html(html, org_url=url, wild_mode=True)
    except Exception as exc:
        log.debug("recipe-scrapers failed for %s: %s", url, exc)
        return None

    def grab(getter, default=None):
        try:
            value = getter()
            return value if value else default
        except Exception:
            return default

    ingredients = grab(scraper.ingredients, [])
    steps = grab(scraper.instructions_list) or [
        line.strip()
        for line in (grab(scraper.instructions, "") or "").split("\n")
        if line.strip()
    ]
    if not ingredients and not steps:
        return None
    total_time_min = grab(scraper.total_time)
    return {
        "kind": "recipe",
        "title": grab(scraper.title, url),
        "image_url": grab(scraper.image),
        "yields": grab(scraper.yields),
        "total_time_s": int(total_time_min) * 60 if total_time_min else None,
        "ingredients": [str(i) for i in ingredients],
        "steps": [str(s) for s in steps],
    }


def _parse_article(html: str, url: str) -> dict:
    try:
        import lxml.html
        from readability import Document

        doc = Document(html)
        tree = lxml.html.fromstring(doc.summary())
        paragraphs = []
        for el in tree.xpath("//p | //li | //h2 | //h3"):
            text = " ".join(el.text_content().split())
            if text and text not in paragraphs:
                paragraphs.append(text)
        title = doc.short_title() or url
    except Exception as exc:
        raise ExtractionError(f"Could not parse the page ({exc})") from exc
    if not paragraphs:
        raise ExtractionError("No recipe or readable article content found on this page")
    return {
        "kind": "article",
        "title": title,
        "image_url": None,
        "yields": None,
        "total_time_s": None,
        "ingredients": [],
        "steps": paragraphs,
    }


async def _save(db: Database, data: dict) -> None:
    await db.execute(
        "INSERT INTO recipe_cache (url, kind, title, image_url, yields, total_time_s, "
        "ingredients_json, steps_json, source_host, fetched_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(url) DO UPDATE SET kind=excluded.kind, title=excluded.title, "
        "image_url=excluded.image_url, yields=excluded.yields, total_time_s=excluded.total_time_s, "
        "ingredients_json=excluded.ingredients_json, steps_json=excluded.steps_json, "
        "source_host=excluded.source_host, fetched_at=excluded.fetched_at",
        (
            data["url"], data["kind"], data["title"], data["image_url"], data["yields"],
            data["total_time_s"], json.dumps(data["ingredients"]), json.dumps(data["steps"]),
            data["source_host"], data["fetched_at"],
        ),
    )


def _row_to_dict(row: dict) -> dict:
    return {
        "url": row["url"],
        "kind": row["kind"],
        "title": row["title"],
        "image_url": row["image_url"],
        "yields": row["yields"],
        "total_time_s": row["total_time_s"],
        "ingredients": json.loads(row["ingredients_json"]),
        "steps": json.loads(row["steps_json"]),
        "source_host": row["source_host"],
        "fetched_at": row["fetched_at"],
    }


def _age_days(fetched_at: str) -> float:
    try:
        fetched = datetime.datetime.fromisoformat(fetched_at)
    except ValueError:
        return float("inf")
    now = datetime.datetime.now(datetime.timezone.utc)
    return (now - fetched).total_seconds() / 86400
