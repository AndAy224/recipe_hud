"""Clean-recipe extraction: fetch a page server-side, pull the recipe out of
its schema.org data (recipe-scrapers), fall back to readability article text.
Results are cached in recipe_cache so clean views also work offline."""

import datetime
import hashlib
import json
import logging
import re
from urllib.parse import urlparse

import httpx

from .config import CONFIG
from .db import Database
from .settings_store import SettingsStore

log = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# A full browser header set: several sites (e.g. Food Network) 403 anything
# that only sends a User-Agent.
BROWSER_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,"
              "image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Sec-Ch-Ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}

# Sites behind TLS fingerprinting (AllRecipes, Serious Eats, Simply Recipes…)
# 403 any plain-Python client no matter the headers; curl_cffi impersonates a
# real Chrome handshake. Optional: no prebuilt wheels on 32-bit ARM.
try:
    from curl_cffi.requests import AsyncSession as _CurlSession
except ImportError:
    _CurlSession = None

RETRY_IMPERSONATED = {401, 403, 406, 429}


class ExtractionError(Exception):
    pass


async def extract(db: Database, store: SettingsStore, url: str, refresh: bool = False) -> dict:
    cached = await db.fetchone("SELECT * FROM recipe_cache WHERE url = ?", (url,))
    # Saved recipes never expire (the source page may vanish; the cache IS the copy).
    if cached and not refresh and (
        cached["saved"] or _age_days(cached["fetched_at"]) <= store.get("recipe_cache_max_age_days")
    ):
        return _row_to_dict(cached)
    try:
        html = await _fetch(url)
    except Exception as exc:
        if cached:
            log.warning("re-fetch failed for %s, serving cached copy: %s", url, exc)
            return _row_to_dict(cached)
        raise ExtractionError(f"Could not fetch the page ({exc})") from exc
    data = _parse(html, url)
    if cached and cached["saved"] and cached["kind"] == "recipe" and data["kind"] != "recipe":
        # A redesign broke recipe parsing; never let a re-fetch degrade a
        # confirmed-good saved copy to a plain-text article.
        log.warning("refresh of %s degraded to %s; keeping the saved copy", url, data["kind"])
        return _row_to_dict(cached)
    await _save(db, data)
    data["saved"] = bool(cached["saved"]) if cached else False
    if data["saved"]:
        data["image_url"] = await snapshot_image(db, url) or data["image_url"]
    return data


async def _fetch(url: str) -> str:
    try:
        async with httpx.AsyncClient(
            headers=BROWSER_HEADERS, follow_redirects=True, timeout=20,
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.text
    except httpx.HTTPStatusError as exc:
        if _CurlSession is None or exc.response.status_code not in RETRY_IMPERSONATED:
            raise
        log.info("%s -> %d; retrying with browser impersonation",
                 url, exc.response.status_code)
        return await _fetch_impersonated(url)


async def _fetch_impersonated(url: str) -> str:
    async with _CurlSession() as session:
        resp = await session.get(url, impersonate="chrome", timeout=20,
                                 allow_redirects=True)
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

    def split_lines(text):
        return [line.strip() for line in (text or "").split("\n") if line.strip()]

    # Site-specific scrapers break when sites redesign (e.g. SimplyRecipes'
    # instructions selector); the page's schema.org data is usually still
    # intact, so fall back to scraper.schema for anything that comes up empty.
    schema = getattr(scraper, "schema", None)
    ingredients = grab(scraper.ingredients, [])
    if not ingredients and schema:
        ingredients = grab(schema.ingredients, [])
    steps = grab(scraper.instructions_list) or split_lines(grab(scraper.instructions))
    if not steps and schema:
        steps = split_lines(grab(schema.instructions))
    if not ingredients and not steps:
        return None
    total_time_min = grab(scraper.total_time)
    prep_time_min = grab(scraper.prep_time)
    cook_time_min = grab(scraper.cook_time)
    nutrients = grab(scraper.nutrients, {})
    if not nutrients and schema:
        nutrients = grab(schema.nutrients, {})
    return {
        "kind": "recipe",
        "title": grab(scraper.title, url),
        "image_url": grab(scraper.image),
        "yields": grab(scraper.yields),
        "total_time_s": int(total_time_min) * 60 if total_time_min else None,
        "ingredients": [str(i) for i in ingredients],
        "steps": [str(s) for s in steps],
        "meta": {
            "author": grab(scraper.author),
            "category": grab(scraper.category),
            "cuisine": grab(scraper.cuisine),
            "prep_time_s": int(prep_time_min) * 60 if prep_time_min else None,
            "cook_time_s": int(cook_time_min) * 60 if cook_time_min else None,
            "nutrition": _normalize_nutrition(nutrients),
        },
    }


# schema.org NutritionInformation keys -> canonical payload keys. Values are
# free-form strings ("240 kcal", "4 g"); only the leading number is trusted.
NUTRIENT_KEYS = {
    "calories": "calories",
    "proteinContent": "protein_g",
    "fatContent": "fat_g",
    "carbohydrateContent": "carbs_g",
    "fiberContent": "fiber_g",
    "sugarContent": "sugar_g",
    "sodiumContent": "sodium_mg",
}


def _normalize_nutrition(raw: dict) -> dict | None:
    if not raw:
        return None
    out = {}
    for src, dest in NUTRIENT_KEYS.items():
        match = re.match(r"[\d.]+", str(raw.get(src, "")).strip())
        if not match:
            continue
        try:
            value = float(match.group())
        except ValueError:
            continue
        out[dest] = int(value) if value == int(value) else round(value, 1)
    if not out:
        return None
    out["raw"] = raw
    return out


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
        "meta": {},
    }


async def _save(db: Database, data: dict) -> None:
    await db.execute(
        "INSERT INTO recipe_cache (url, kind, title, image_url, yields, total_time_s, "
        "ingredients_json, steps_json, source_host, fetched_at, meta_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(url) DO UPDATE SET kind=excluded.kind, title=excluded.title, "
        "image_url=excluded.image_url, yields=excluded.yields, total_time_s=excluded.total_time_s, "
        "ingredients_json=excluded.ingredients_json, steps_json=excluded.steps_json, "
        "source_host=excluded.source_host, fetched_at=excluded.fetched_at, "
        "meta_json=excluded.meta_json",
        (
            data["url"], data["kind"], data["title"], data["image_url"], data["yields"],
            data["total_time_s"], json.dumps(data["ingredients"]), json.dumps(data["steps"]),
            data["source_host"], data["fetched_at"], json.dumps(data.get("meta") or {}),
        ),
    )


# -- image snapshots ------------------------------------------------------
# Saved recipes must survive the source site vanishing, so the hero image is
# downloaded next to the DB and served from /media instead of hotlinked.

IMAGE_EXT = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/avif": ".avif",
}
IMAGE_MAX_BYTES = 8 * 1024 * 1024


def local_image_url(row: dict) -> str | None:
    return f"/media/{row['image_local']}" if row.get("image_local") else None


async def snapshot_image(db: Database, url: str) -> str | None:
    """Download the recipe's hero image locally; returns its /media URL.
    Best-effort: any failure leaves the remote image_url in use."""
    row = await db.fetchone(
        "SELECT image_url, image_local FROM recipe_cache WHERE url = ?", (url,))
    if not row or not row["image_url"]:
        return None
    if row["image_local"]:
        return local_image_url(row)
    try:
        try:
            async with httpx.AsyncClient(
                headers={"User-Agent": USER_AGENT, "Referer": url},
                follow_redirects=True,
                timeout=20,
            ) as client:
                resp = await client.get(row["image_url"])
                resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if _CurlSession is None or exc.response.status_code not in RETRY_IMPERSONATED:
                raise
            async with _CurlSession() as session:
                resp = await session.get(row["image_url"], impersonate="chrome",
                                         timeout=20, allow_redirects=True)
                resp.raise_for_status()
        content_type = resp.headers.get("content-type", "").split(";")[0].strip()
        ext = IMAGE_EXT.get(content_type)
        if not ext or len(resp.content) > IMAGE_MAX_BYTES:
            log.warning("not snapshotting image for %s (%s, %d bytes)",
                        url, content_type, len(resp.content))
            return None
        name = hashlib.sha1(url.encode()).hexdigest()[:16] + ext
        CONFIG.media_dir.mkdir(parents=True, exist_ok=True)
        (CONFIG.media_dir / name).write_bytes(resp.content)
    except Exception as exc:
        log.warning("image snapshot failed for %s: %s", url, exc)
        return None
    await db.execute(
        "UPDATE recipe_cache SET image_local = ? WHERE url = ?", (name, url))
    return f"/media/{name}"


async def delete_image_snapshot(db: Database, url: str) -> None:
    row = await db.fetchone(
        "SELECT image_local FROM recipe_cache WHERE url = ?", (url,))
    if row and row["image_local"]:
        (CONFIG.media_dir / row["image_local"]).unlink(missing_ok=True)
        await db.execute(
            "UPDATE recipe_cache SET image_local = NULL WHERE url = ?", (url,))


def _row_to_dict(row: dict) -> dict:
    return {
        "url": row["url"],
        "kind": row["kind"],
        "title": row["title"],
        "image_url": local_image_url(row) or row["image_url"],
        "yields": row["yields"],
        "total_time_s": row["total_time_s"],
        "ingredients": json.loads(row["ingredients_json"]),
        "steps": json.loads(row["steps_json"]),
        "source_host": row["source_host"],
        "fetched_at": row["fetched_at"],
        "saved": bool(row["saved"]),
        "tags": [t for t in (row.get("tags") or "").split(",") if t],
        "meta": json.loads(row["meta_json"]) if row.get("meta_json") else {},
    }


def _age_days(fetched_at: str) -> float:
    try:
        fetched = datetime.datetime.fromisoformat(fetched_at)
    except ValueError:
        return float("inf")
    now = datetime.datetime.now(datetime.timezone.utc)
    return (now - fetched).total_seconds() / 86400
