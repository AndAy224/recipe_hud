import hashlib
import json
import secrets

from .db import Database

# Runtime-tunable settings with defaults. Values are JSON-encoded in the DB.
DEFAULTS: dict = {
    "idle_timeout_s": 300,          # ACTIVE -> CLOCK
    "clock_to_off_s": 600,          # CLOCK -> OFF
    "night_mode_enabled": False,
    "night_off_start": "22:30",
    "night_off_end": "06:30",
    "night_idle_timeout_s": 60,     # ACTIVE -> OFF directly inside night window
    "keep_awake": False,            # manual "cooking mode" toggle
    "alarm_volume": 80,             # 0-100
    "alarm_auto_dismiss_s": 600,    # stop ringing after this long
    "theme": "dark",                # dark | light
    "display_output": "HDMI-A-1",   # wlopm/wlr-randr output name
    "touch_device": "",             # optional /dev/input/eventN override
    "recipe_cache_max_age_days": 30,
    "weather_location": "",         # "lat,lon" for Open-Meteo (v1.5)
}

SECRET_KEYS = {"admin_password_hash"}
DEFAULT_ADMIN_PASSWORD = "recipehud"


def hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 200_000)
    return f"{salt}${digest.hex()}"


def verify_password(stored: str, password: str) -> bool:
    try:
        salt, _ = stored.split("$", 1)
    except ValueError:
        return False
    return secrets.compare_digest(hash_password(password, salt), stored)


class SettingsStore:
    """All settings cached in memory; the idle loop reads them every second."""

    def __init__(self, db: Database):
        self.db = db
        self._cache: dict = {}
        self._listeners: list = []  # callables invoked with dict of changed keys

    async def load(self) -> None:
        rows = await self.db.fetchall("SELECT key, value FROM settings")
        stored = {r["key"]: json.loads(r["value"]) for r in rows}
        self._cache = {**DEFAULTS, **stored}
        if "admin_password_hash" not in self._cache:
            self._cache["admin_password_hash"] = hash_password(DEFAULT_ADMIN_PASSWORD)
            await self._persist("admin_password_hash")

    def get(self, key: str):
        return self._cache[key]

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

    async def set_admin_password(self, password: str) -> None:
        self._cache["admin_password_hash"] = hash_password(password)
        await self._persist("admin_password_hash")

    async def _persist(self, key: str) -> None:
        await self.db.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(self._cache[key])),
        )
