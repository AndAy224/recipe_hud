import json

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
    # Backlight over DDC/CI (display_ctl/ddc.py). brightness_active is the
    # panel's own 0-100 value; it is adopted from the panel on the first probe
    # (see DdcBacklight) so taking control never changes what people are used
    # to. Night scales it; OFF is absolute.
    "backlight_control": True,
    "brightness_active": 100,
    "brightness_night_pct": 50,     # % of brightness_active inside the night window
    "brightness_off": 0,            # backlight behind the black OFF scrim
}

# The admin panel has no login; a hash left over from when it did must still
# never be served to clients.
SECRET_KEYS = {"admin_password_hash"}


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
        return {k: v for k, v in self._cache.items() if k not in SECRET_KEYS}

    def on_change(self, listener) -> None:
        self._listeners.append(listener)

    async def patch(self, updates: dict) -> dict:
        """Apply updates for known keys; returns the changed subset."""
        changed = {}
        for key, value in updates.items():
            if key not in DEFAULTS:
                continue
            default = DEFAULTS[key]
            # Coerce to the default's type so e.g. "300" from a form works.
            if isinstance(default, bool):
                value = bool(value)
            elif isinstance(default, int) and not isinstance(value, bool):
                value = int(value)
            elif isinstance(default, str):
                value = str(value)
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
