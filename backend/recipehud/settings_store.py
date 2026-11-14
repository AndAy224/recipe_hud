import json
import re

from .db import Database

# Runtime-tunable settings with defaults. Values are JSON-encoded in the DB.
DEFAULTS: dict = {
    "idle_timeout_s": 300,          # ACTIVE -> CLOCK
    "clock_to_off_s": 600,          # CLOCK -> OFF
    "night_mode_enabled": False,
    "night_off_start": "22:30",
    "night_off_end": "06:30",
    "night_idle_timeout_s": 60,     # ACTIVE -> OFF directly inside night window
    "night_dim_enabled": True,      # warm dim veil during the night window
    # Cut real panel power in OFF, instead of only painting the black scrim.
    # Off by default, and deliberately so: many HDMI touch panels power their
    # USB touch controller from the monitor, so a DPMS off makes the
    # touchscreen disconnect (or flap in and out) — and then a touch cannot
    # wake the kiosk, which is the only input it has. Verified true of this
    # appliance's panel (wch.cn 27C0:0859). Only turn this on for hardware
    # whose touch controller demonstrably stays alive with the panel off.
    "panel_power_off": False,
    "keep_awake": False,            # manual "cooking mode" toggle
    "alarm_volume": 80,             # 0-100
    "alarm_auto_dismiss_s": 600,    # stop ringing after this long
    "theme": "dark",                # dark | light
    "display_output": "HDMI-A-1",   # wlopm/wlr-randr output name
    "touch_device": "",             # optional /dev/input/eventN override
    "recipe_cache_max_age_days": 30,
    "weather_location": "",         # "lat,lon" for Open-Meteo (v1.5)
    "wine_pairing_enabled": True,   # suggest a wine pairing per recipe
    # Seasonal screensaver (decks/*.json, each with its own date window). While
    # one is in season it is how the CLOCK state looks, and CLOCK stops timing
    # out to OFF in the daytime — night still goes black via the night window.
    "screensaver_enabled": True,
    # Which seasonal deck to show: "auto" follows the calendar (each deck's own
    # season; the highest priority wins an overlap), or a deck's theme name
    # ("christmas", "thanksgiving", …) to show that one whatever the date.
    "screensaver_theme": "auto",
    # Which kinds of card a seasonal deck shows: any of drink, main, side,
    # dessert (at least one). Cards of the other kinds stay in the deck files
    # and are simply not shown.
    "screensaver_categories": ["drink", "main", "side", "dessert"],
    # Out of season, show the user's own saved recipes on the CLOCK screen
    # instead of the plain clock. Cosmetic only: CLOCK still times out to OFF.
    "screensaver_saved_enabled": True,
    # Backlight over DDC/CI (display_ctl/ddc.py). brightness_active is the
    # panel's own 0-100 value; it is adopted from the panel on the first probe
    # (see DdcBacklight) so taking control never changes what people are used
    # to. Night scales it; OFF is absolute.
    "backlight_control": True,
    "brightness_active": 100,
    "brightness_night_pct": 50,     # % of brightness_active inside the night window
    "brightness_off": 0,            # backlight behind the black OFF scrim
}


class SettingError(ValueError):
    """A rejected setting value; str() is the user-facing reason."""

    def __init__(self, key: str, reason: str):
        super().__init__(f"{key}: {reason}")
        self.key = key


# -- validators: each takes the raw value and returns the cleaned one, or
#    raises ValueError with a reason. The API is open and the idle loop reads
#    these every second, so a bad value must be refused at the door rather
#    than quietly misbehave (idle_timeout_s=0 put the panel back on the clock
#    a second after every touch; a malformed night time silently disabled
#    night mode; bool("false") is True).

def _bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str) and value.strip().lower() in ("true", "1", "on", "yes"):
        return True
    if isinstance(value, str) and value.strip().lower() in ("false", "0", "off", "no", ""):
        return False
    raise ValueError("must be true or false")


def _int(lo: int, hi: int):
    def check(value):
        if isinstance(value, bool):
            raise ValueError("must be a whole number")
        try:
            number = int(str(value).strip())
        except (TypeError, ValueError):
            raise ValueError("must be a whole number") from None
        if not lo <= number <= hi:
            raise ValueError(f"must be between {lo} and {hi}")
        return number
    return check


def _hhmm(value):
    match = re.fullmatch(r"\s*(\d{1,2}):(\d{2})(?::\d{2})?\s*", str(value))
    if not match or int(match.group(1)) > 23 or int(match.group(2)) > 59:
        raise ValueError("must be a time like 22:30")
    return f"{int(match.group(1)):02d}:{match.group(2)}"


def _choice(*options: str):
    def check(value):
        if value not in options:
            raise ValueError(f"must be one of {', '.join(options)}")
        return value
    return check


def _text(max_len: int):
    def check(value):
        text = str(value).strip()
        if len(text) > max_len:
            raise ValueError(f"must be at most {max_len} characters")
        return text
    return check


def _lat_lon(value):
    text = str(value).strip()
    if not text:
        return ""
    try:
        lat, lon = (float(part) for part in text.split(","))
    except ValueError:
        raise ValueError("must be empty or 'latitude,longitude'") from None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError("latitude/longitude out of range")
    return text


def _deck_theme(value):
    from .screensaver import load_decks  # lazy: screensaver.py imports this module

    choice = str(value).strip().lower() or "auto"
    themes = [deck["theme"] for deck in load_decks()]
    if choice != "auto" and choice not in themes:
        raise ValueError(f"must be auto or one of: {', '.join(themes)}")
    return choice


def _deck_categories(value):
    from .screensaver import CATEGORIES  # lazy: screensaver.py imports this module

    if isinstance(value, str):
        value = [part for part in re.split(r"[,\s]+", value) if part]
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"must be a list of: {', '.join(CATEGORIES)}")
    wanted = {str(part).strip().lower() for part in value}
    if not wanted or wanted - set(CATEGORIES):
        raise ValueError(f"must be one or more of: {', '.join(CATEGORIES)}")
    return [cat for cat in CATEGORIES if cat in wanted]


VALIDATORS: dict = {
    "idle_timeout_s": _int(1, 86400),
    "clock_to_off_s": _int(1, 86400),
    "night_mode_enabled": _bool,
    "night_off_start": _hhmm,
    "night_off_end": _hhmm,
    "night_idle_timeout_s": _int(1, 86400),
    "night_dim_enabled": _bool,
    "panel_power_off": _bool,
    "keep_awake": _bool,
    "alarm_volume": _int(0, 100),
    "alarm_auto_dismiss_s": _int(1, 86400),
    "theme": _choice("dark", "light"),
    "display_output": _text(64),
    "touch_device": _text(128),
    "recipe_cache_max_age_days": _int(1, 3650),
    "weather_location": _lat_lon,
    "wine_pairing_enabled": _bool,
    "screensaver_enabled": _bool,
    "screensaver_theme": _deck_theme,
    "screensaver_categories": _deck_categories,
    "screensaver_saved_enabled": _bool,
    "backlight_control": _bool,
    "brightness_active": _int(0, 100),
    "brightness_night_pct": _int(0, 100),
    "brightness_off": _int(0, 100),
}
assert set(VALIDATORS) == set(DEFAULTS), "every setting needs a validator"


class SettingsStore:
    """All settings cached in memory; the idle loop reads them every second."""

    def __init__(self, db: Database):
        self.db = db
        self._cache: dict = {}
        self._stored: set[str] = set()  # keys with a row in the DB (vs. defaults)
        self._listeners: list = []  # callables invoked with dict of changed keys

    async def load(self) -> None:
        rows = await self.db.fetchall("SELECT key, value FROM settings")
        stored = {r["key"]: json.loads(r["value"]) for r in rows}
        self._stored = set(stored)
        self._cache = {**DEFAULTS, **stored}

    def get(self, key: str):
        return self._cache[key]

    def is_stored(self, key: str) -> bool:
        """True once the key has been explicitly set (it has a DB row)."""
        return key in self._stored

    def public(self) -> dict:
        """Settings as clients see them: the known keys only. A row left in the
        table by an older version (the admin password hash was one, until the
        v6 migration deleted it) is never served."""
        return {k: v for k, v in self._cache.items() if k in DEFAULTS}

    def on_change(self, listener) -> None:
        self._listeners.append(listener)

    async def patch(self, updates: dict) -> dict:
        """Apply updates for known keys; returns the changed subset.

        Values are validated and coerced (so "300" from a form works). All or
        nothing: one bad value raises SettingError and nothing is applied.
        Unknown keys are ignored."""
        if not isinstance(updates, dict):
            raise SettingError("settings", "expected an object of setting names to values")
        cleaned = {}
        for key, value in updates.items():
            if key not in DEFAULTS:
                continue
            try:
                cleaned[key] = VALIDATORS[key](value)
            except ValueError as exc:
                raise SettingError(key, str(exc)) from None
        changed = {}
        for key, value in cleaned.items():
            if self._cache.get(key) != value:
                self._cache[key] = value
                changed[key] = value
        for key in changed:
            await self._persist(key)
        if changed:
            for listener in self._listeners:
                listener(changed)
        return changed

    async def _persist(self, key: str) -> None:
        await self.db.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(self._cache[key])),
        )
        self._stored.add(key)
