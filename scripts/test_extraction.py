"""Multi-site extraction harness: runs the clean-view extractor against a
broad set of popular recipe sites and grades the results.

Usage: python scripts/test_extraction.py          (backend on :8000, fresh fetches)
       python scripts/test_extraction.py --refresh (bypass cache)

Grades:
  OK      kind=recipe with sane counts
  THIN    kind=recipe but suspiciously little content (needs a look)
  ARTICLE fell back to readability (no recipe schema found — needs a look)
  FETCH   the site refused/failed the fetch (403/404/timeout — not always fixable)
  ERROR   extraction endpoint errored
"""

import os
import sys
from urllib.parse import quote

import httpx

BASE = os.environ.get("RECIPEHUD_TEST_BASE", "http://127.0.0.1:8000")

# Deliberately diverse: big media sites, WP food blogs, UK sites, one paywall.
SITES = [
    ("AllRecipes", "https://www.allrecipes.com/recipe/23600/worlds-best-lasagna/"),
    ("Serious Eats", "https://www.seriouseats.com/foolproof-pan-pizza-recipe"),
    ("BBC Good Food", "https://www.bbcgoodfood.com/recipes/best-spaghetti-bolognese-recipe"),
    ("Budget Bytes", "https://www.budgetbytes.com/dragon-noodles/"),
    ("RecipeTin Eats", "https://www.recipetineats.com/butter-chicken/"),
    ("Simply Recipes", "https://www.simplyrecipes.com/recipes/banana_bread/"),
    ("Pinch of Yum", "https://pinchofyum.com/the-best-soft-chocolate-chip-cookies"),
    ("Damn Delicious", "https://damndelicious.net/2014/10/03/easy-lo-mein/"),
    ("Woks of Life", "https://thewoksoflife.com/beef-with-broccoli-all-purpose-stir-fry-sauce/"),
    ("Bon Appétit", "https://www.bonappetit.com/recipe/bas-best-chocolate-chip-cookies"),
    ("Food Network", "https://www.foodnetwork.com/recipes/alton-brown/good-eats-roast-turkey-recipe-1950271"),
    ("King Arthur", "https://www.kingarthurbaking.com/recipes/chocolate-chip-cookies-recipe"),
    ("Sally's Baking", "https://sallysbakingaddiction.com/chocolate-chip-cookies/"),
    ("Love & Lemons", "https://www.loveandlemons.com/banana-bread-recipe/"),
    ("Cookie + Kate", "https://cookieandkate.com/healthy-banana-bread-recipe/"),
    ("Once Upon a Chef", "https://www.onceuponachef.com/recipes/banana-bread.html"),
    # Smitten Kitchen serves no schema.org data at all — the article fallback
    # (which contains the recipe prose) is the expected outcome there.
    ("Smitten Kitchen (expect ARTICLE)", "https://smittenkitchen.com/2016/08/even-more-perfect-blueberry-muffins/"),
    ("NYT Cooking (paywall)", "https://cooking.nytimes.com/recipes/1015819-chocolate-chip-cookies"),
]


def grade(data: dict) -> tuple[str, str]:
    if data["kind"] != "recipe":
        return "ARTICLE", f"fell back to article ({len(data['steps'])} paragraphs)"
    problems = []
    if len(data["ingredients"]) < 3:
        problems.append(f"only {len(data['ingredients'])} ingredients")
    if len(data["steps"]) < 2:
        problems.append(f"only {len(data['steps'])} steps")
    if not data["title"] or data["title"].startswith("http"):
        problems.append("bad title")
    if any("<" in s and ">" in s for s in data["steps"] + data["ingredients"]):
        problems.append("html tags leaked into text")
    if any(len(s) > 2000 for s in data["steps"]):
        problems.append("suspiciously huge step")
    extras = []
    if not data["image_url"]:
        extras.append("no image")
    if not data["yields"]:
        extras.append("no yields")
    if not data["total_time_s"]:
        extras.append("no time")
    if not data.get("meta", {}).get("nutrition"):
        extras.append("no nutrition")
    if problems:
        return "THIN", "; ".join(problems + extras)
    note = (f"{len(data['ingredients'])} ing / {len(data['steps'])} steps"
            + (f" ({', '.join(extras)})" if extras else ""))
    return "OK", note


def main() -> None:
    refresh = "--refresh" in sys.argv
    results = []
    with httpx.Client(base_url=BASE, timeout=60) as client:
        for name, url in SITES:
            query = f"url={quote(url, safe='')}" + ("&refresh=1" if refresh else "")
            try:
                resp = client.get(f"/api/recipe/extract?{query}")
            except httpx.HTTPError as exc:
                results.append((name, "ERROR", str(exc)))
                continue
            if resp.status_code == 422:
                results.append((name, "FETCH", resp.json().get("detail", "")[:90]))
            elif resp.status_code != 200:
                results.append((name, "ERROR", f"HTTP {resp.status_code}"))
            else:
                results.append((name, *grade(resp.json())))

    width = max(len(n) for n, _, _ in results)
    counts: dict[str, int] = {}
    for name, status, note in results:
        counts[status] = counts.get(status, 0) + 1
        print(f"{name:<{width}}  {status:<7}  {note}")
    print("\nsummary:", ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))


if __name__ == "__main__":
    main()
