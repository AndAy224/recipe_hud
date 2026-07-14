"""Wine-pairing suggestions for extracted recipes.

Recipe pages almost never carry a pairing, so it is derived from the recipe:
an LLM (Claude) produces a nuanced pick when RECIPEHUD_ANTHROPIC_API_KEY is
set; otherwise a local keyword/cuisine heuristic always returns something so
the feature works offline. Results are cached in recipe_cache.meta_json by the
caller (see api/recipe.py), so generation happens once per recipe."""

import json
import logging
import re

import httpx

from .config import Config
from .settings_store import SettingsStore

log = logging.getLogger(__name__)

# Anthropic Messages API — Haiku is fast/cheap and plenty for a one-line pick.
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_MODEL = "claude-haiku-4-5"
ANTHROPIC_VERSION = "2023-06-01"


async def suggest_wine(recipe: dict, config: Config, store: SettingsStore) -> dict | None:
    """Return {"wine", "note", "source"} for a recipe, or None if disabled/not
    a recipe. Never raises: the rule-based path always yields a result."""
    if recipe.get("kind") != "recipe":
        return None
    if not store.get("wine_pairing_enabled"):
        return None
    if config.anthropic_api_key:
        try:
            pairing = await _llm_pairing(recipe, config.anthropic_api_key)
            if pairing:
                return {**pairing, "source": "llm"}
        except Exception as exc:  # network/timeout/parse — fall back to rules
            log.warning("LLM wine pairing failed, using rule fallback: %s", exc)
    rule = _rule_pairing(recipe)
    return {**rule, "source": "rule"} if rule else None


async def _llm_pairing(recipe: dict, api_key: str) -> dict | None:
    meta = recipe.get("meta") or {}
    ingredients = recipe.get("ingredients") or []
    facts = [f"Title: {recipe.get('title') or 'Unknown'}"]
    if meta.get("cuisine"):
        facts.append(f"Cuisine: {meta['cuisine']}")
    if meta.get("category"):
        facts.append(f"Category: {meta['category']}")
    if ingredients:
        facts.append("Ingredients:\n- " + "\n- ".join(str(i) for i in ingredients[:30]))
    prompt = (
        "Suggest a single wine pairing for this recipe. Reply with ONLY a JSON "
        'object of the form {"wine": "<specific wine or style>", "note": '
        '"<why it pairs, at most 12 words, no trailing period>"} and nothing '
        "else.\n\n" + "\n".join(facts)
    )
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            ANTHROPIC_URL,
            headers={
                "x-api-key": api_key,
                "anthropic-version": ANTHROPIC_VERSION,
                "content-type": "application/json",
            },
            json={
                "model": ANTHROPIC_MODEL,
                "max_tokens": 200,
                "messages": [{"role": "user", "content": prompt}],
            },
        )
        resp.raise_for_status()
    body = resp.json()
    text = "".join(
        block.get("text", "") for block in body.get("content", [])
        if block.get("type") == "text"
    ).strip()
    return _parse_pairing(text)


def _parse_pairing(text: str) -> dict | None:
    """Extract {"wine", "note"} from a model reply, tolerating stray prose."""
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        data = json.loads(text[start:end + 1])
    except (ValueError, TypeError):
        return None
    wine = str(data.get("wine") or "").strip()
    if not wine:
        return None
    return {"wine": wine, "note": str(data.get("note") or "").strip()}


# Keyword -> (wine, note). First match wins, so order most-specific first.
# Matched against the title, cuisine, category, and ingredient text.
_RULES: list[tuple[tuple[str, ...], str, str]] = [
    (("chocolate", "brownie", "fudge"), "Port", "sweet richness stands up to chocolate"),
    # Only unambiguous dessert words — "pie"/"tart" also name savory dishes
    # (pot pie, shepherd's pie, tomato tart), so they're deliberately excluded.
    (("dessert", "cake", "cupcake", "cheesecake", "cookie", "custard", "pudding"),
     "Moscato d'Asti", "light sweetness echoes the dessert"),
    (("shrimp", "scallop", "lobster", "crab", "oyster", "clam", "mussel"),
     "Chablis", "crisp minerality suits shellfish"),
    (("salmon", "tuna", "fish", "cod", "halibut", "seafood", "trout"),
     "Sauvignon Blanc", "bright acidity lifts the fish"),
    (("curry", "thai", "indian", "spicy", "chili", "sriracha", "szechuan", "korean"),
     "Off-dry Riesling", "a touch of sweetness tames the heat"),
    (("beef", "veal", "steak", "brisket", "burger", "short rib", "osso buco"),
     "Cabernet Sauvignon", "bold tannins match red meat"),
    (("lamb", "venison"), "Syrah", "peppery depth complements the game"),
    (("pork", "bacon", "ham", "sausage"),
     "Zinfandel", "jammy fruit plays off the pork"),
    (("chicken", "turkey", "poultry"),
     "Chardonnay", "round body suits poultry"),
    (("pasta", "tomato", "marinara", "pizza", "bolognese"),
     "Chianti", "savory acidity cuts the tomato"),
    (("mushroom", "risotto", "truffle"),
     "Pinot Noir", "earthy notes echo the mushrooms"),
    (("cheese", "creamy", "alfredo", "gratin"),
     "Chardonnay", "buttery weight matches the cream"),
    (("salad", "vegetable", "veggie", "greens", "tofu"),
     "Sauvignon Blanc", "fresh and herbaceous for greens"),
]


def _rule_pairing(recipe: dict) -> dict | None:
    meta = recipe.get("meta") or {}
    haystack = " ".join(
        str(x) for x in [
            recipe.get("title") or "",
            meta.get("cuisine") or "",
            meta.get("category") or "",
            *(recipe.get("ingredients") or []),
        ]
    ).lower()
    for keywords, wine, note in _RULES:
        # Whole-word match so e.g. "pie" doesn't fire on "pieces".
        if any(re.search(rf"\b{re.escape(kw)}\b", haystack) for kw in keywords):
            return {"wine": wine, "note": note}
    # Sensible default: a versatile, food-friendly red.
    return {"wine": "Pinot Noir", "note": "a versatile, food-friendly red"}
