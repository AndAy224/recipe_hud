# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Recipe HUD is a kitchen recipe kiosk for a Raspberry Pi 4 driving a portrait touch panel. Read `README.md` for the feature set and `docs/ARCHITECTURE.md` for the full design rationale and the WebSocket event table — this file covers only what those don't, plus how to work in the repo.

## Commands

There is **no build step** — the frontend (`frontend/`) and the MV3 extension (`extension/`) are vanilla JS/HTML loaded as-is. Only the Python backend is a package.

```bash
# Install (editable) — Python 3.11+
python -m venv .venv && .venv/bin/pip install -e .

# Run the backend for dev (mock display, debug endpoints on)
RECIPEHUD_DISPLAY_BACKEND=mock RECIPEHUD_DEBUG=1 \
  .venv/bin/uvicorn recipehud.main:app --reload --host 0.0.0.0 --port 8000 --app-dir backend
# (scripts/dev.ps1 is the Windows equivalent; it also seeds the DB first)

.venv/bin/python scripts/seed_db.py    # seed sites/presets/settings into a fresh DB
```

Load the extension in dev via `chrome://extensions` → Developer mode → *Load unpacked* → `extension/`.

### Tests

Tests are **standalone scripts, not pytest**, and each needs the backend running on `:8000`:

```bash
.venv/bin/python scripts/test_extraction.py       # grades clean-view extraction across ~18 sites
.venv/bin/python scripts/test_extraction.py --refresh   # bypass recipe_cache
.venv/bin/python scripts/test_scale.py            # ingredient-scaling cases (requires playwright)
.venv/bin/python scripts/ws_smoke.py              # WebSocket/alarm smoke test
```

Without hardware, drive the idle state machine with `POST /api/debug/idle/{active|clock|off}` and `POST /api/debug/touch` (only mounted when `RECIPEHUD_DEBUG=1`).

## Deploying to the Pi (this checkout IS on the Pi)

The working directory (`/home/hud/recipe_hud`) is a **dev checkout**. The *running appliance* is a **separate checkout at `/opt/recipehud`** — that's what systemd runs and what the kiosk Chromium loads the extension from (`--load-extension=/opt/recipehud/extension`, see `deploy/kiosk/start-kiosk.sh`).

Deploy flow: **commit → push `origin/main` → trigger the Pi self-update.** `deploy/update.sh` (invoked by `POST /api/system/update`) hardcodes `TARGET=/opt/recipehud`, does `git reset --hard origin/<branch>` + `pip install`, then kills the backend pid so systemd revives the new code. `git reset --hard` means any manual edits inside `/opt/recipehud` are discarded.

**Gotcha — extension changes need a kiosk restart too.** `POST /api/system/update` restarts only the *backend*. The extension is loaded into Chromium at launch, so after an extension change you must also call `POST /api/system/kiosk/restart` (kills Chromium; `start-kiosk.sh`'s loop relaunches it and reloads the extension from disk). Backend-only changes don't need this.

`/api/system/*` and admin routes require `require_admin`, but **localhost is implicitly trusted** (`api/auth.py:is_local`) — so from the Pi you can `curl -X POST http://localhost:8000/api/system/...` without credentials.

## Architecture notes that span files

- **`backend/recipehud/main.py` wires everything** in the `lifespan` context: it constructs the `Hub`, `TimerEngine`, `IdleController`, display backend and settings store, hangs them on `app.state`, and starts the idle loop + evdev touch watcher as tasks. Follow `app.state.*` to find any subsystem.
- **Commands are REST; state changes fan out over WebSocket.** Clients (`role=launcher|overlay|admin`) only *receive* events (`ws.py:Hub.broadcast`) plus send throttled `{"type":"activity"}` pings. Never push command results down the socket — mutate via a `/api/*` route, which broadcasts the resulting event. Event catalog is in `docs/ARCHITECTURE.md`.
- **Timers are server-authoritative** (`timer_engine.py`): monotonic deadlines, 1 Hz `timer.tick` broadcasts, snapshotted to SQLite so a mid-cook restart survives. Pages hold no timer state of their own.
- **The overlay is a Chromium extension, not an iframe** (recipe sites block framing). `extension/sw.js` owns the single backend WebSocket and relays events to per-page content scripts (`extension/content/overlay.js`) via runtime ports; the content script renders in a **closed shadow root** so site CSS/CSP can't touch it. The service worker caches the last snapshot and replays it to each newly-navigated page — treat that cache as possibly-stale and prefer authoritative state (see the `refreshDisplay` fetch of `GET /api/display` on port connect).
- **Idle/display** (`idle.py` + `display_ctl/`): `ACTIVE→CLOCK→OFF` state machine broadcasts `display.state`; clients paint clock/black scrims without navigating (the recipe stays underneath). Panel power is only cut in `OFF`. Backend is auto-selected: `wlopm` (Wayland/labwc on the Pi), `x11`, or `mock` (dev) — override with `RECIPEHUD_DISPLAY_BACKEND`.
- **Extraction** (`extractor.py`): `recipe-scrapers` with a schema.org fallback, then readability. Hardened fetch (full Chrome headers → `curl_cffi` TLS-fingerprint retry). Results cache in `recipe_cache`; saved rows (`saved=1`) never expire and serve offline, and a re-fetch that fails or can no longer parse is discarded rather than overwriting a good saved copy.
- **Config split**: process-level env vars (`RECIPEHUD_*`) live in `config.py`; user-tunable settings (timeouts, schedules, volume, admin password) live in the SQLite `settings` table via `settings_store.py`. Schema is `backend/recipehud/schema.sql`.
