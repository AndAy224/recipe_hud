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

There is **no per-case filter** — `--refresh` is `test_extraction.py`'s only flag. To exercise a single site, call the endpoint directly: `curl "localhost:8000/api/recipe/extract?url=<url>&refresh=1"`. Only `test_extraction.py` honours `RECIPEHUD_TEST_BASE` (point it at a scratch instance, e.g. `RECIPEHUD_TEST_BASE=http://127.0.0.1:8010`); `test_scale.py`, `ws_smoke.py` and `screenshots.py` hardcode `:8000`. There is no linter or formatter configured.

Without hardware, drive the idle state machine with `POST /api/debug/idle/{active|clock|off}` and `POST /api/debug/touch` (only mounted when `RECIPEHUD_DEBUG=1`).

On the Pi, the running appliance already holds `:8000` and `data/recipehud.db`, and the dev checkout may have **no `.venv`** — the deps live in the appliance's interpreter at `/opt/recipehud/.venv/bin/python`. To try a change without disturbing the appliance, run a throwaway instance on another port against a scratch/copied DB: `RECIPEHUD_DISPLAY_BACKEND=mock RECIPEHUD_DB_PATH=/tmp/x.db /opt/recipehud/.venv/bin/uvicorn recipehud.main:app --port 8010 --app-dir backend`. For a real UI check without a browser, `chromium --headless --dump-dom "http://127.0.0.1:8010/recipe?url=..."` renders the page (JS + async fetches) and prints the resulting DOM.

## Deploying to the Pi (this checkout IS on the Pi)

The working directory (`/home/hud/recipe_hud`) is a **dev checkout**. The *running appliance* is a **separate checkout at `/opt/recipehud`** — that's what systemd runs and what the kiosk Chromium loads the extension from (`--load-extension=/opt/recipehud/extension`, see `deploy/kiosk/start-kiosk.sh`).

Deploy flow: **commit → push `origin/main` → trigger the Pi self-update.** `deploy/update.sh` (invoked by `POST /api/system/update`) hardcodes `TARGET=/opt/recipehud`, does `git reset --hard origin/<branch>` + `pip install`, then kills the backend pid so systemd revives the new code. `git reset --hard` means any manual edits inside `/opt/recipehud` are discarded.

**Gotcha — `POST /api/system/update` returns before the deploy lands.** It answers `{"ok":true,"started":true}` the moment `update.sh` is spawned, but the `pip install` inside it can run for minutes on the Pi, and the backend is only killed *after* that. Until then the **old process is still serving**, so an immediate curl against the new behaviour will report the old code and look like a failed deploy. Poll `GET /api/system/update/status`, watch `/opt/recipehud/data/update.log` for `== update finished`, or wait for `systemctl show -p MainPID --value recipehud-backend` to change.

**Gotcha — extension changes need a kiosk restart too.** `POST /api/system/update` restarts only the *backend*. The extension is loaded into Chromium at launch, so after an extension change you must also call `POST /api/system/kiosk/restart` (kills Chromium; `start-kiosk.sh`'s loop relaunches it and reloads the extension from disk). Backend-only changes don't need this.

**Gotcha — systemd unit changes need a manual re-render.** `POST /api/system/update` only `git reset`s + `pip install`s + restarts the backend *process*; it never touches `/etc/systemd/system/recipehud-backend.service`. That unit is rendered from the template `deploy/systemd/recipehud-backend.service` by `deploy/install.sh` (which `sed`s `__USER__`/`__UID__`). After editing the template, re-render it into place (rerun `install.sh`, or `sed` it yourself) and `sudo systemctl daemon-reload && sudo systemctl restart recipehud-backend` — a plain self-update won't pick it up.

**Gotcha — the kiosk MUST launch Chromium with `--password-store=basic`, or the panel boots to a blank white screen.** Chromium tries to unlock the GNOME keyring (Secret Service) to get its cookie-encryption key. On this headless autologin kiosk the keyring is locked and `gcr-prompter` pops a password dialog nobody can answer; Chromium then blocks during cookie init, its network service wedges, and **every page navigation hangs before the request is even sent** (fetch/`curl`/`file://` still work, which makes it look like the backend is fine — it is). Symptom: `start-kiosk.sh`'s Chromium sits on a blank white page and the backend access log shows no `GET /`. `--password-store=basic` makes Chromium skip the keyring and use its own cookie store (correct for a kiosk anyway). The flag is applied in **two independent places on purpose**: `deploy/kiosk/start-kiosk.sh` (in git) **and** `/etc/chromium.d/50-recipehud-keyring` (root-owned, *not* in the checkout) — the latter survives a `git reset --hard` from a self-update, so a deploy can never silently reintroduce the white screen. Keep both.

`/api/system/*` and the other admin routes are unauthenticated (see the architecture note below), so from the Pi — or anywhere on the LAN — you can just `curl -X POST http://localhost:8000/api/system/...`.

## Architecture notes that span files

- **`backend/recipehud/main.py` wires everything** in the `lifespan` context: it constructs the `Hub`, `TimerEngine`, `IdleController`, display backend and settings store, hangs them on `app.state`, and starts the idle loop + evdev touch watcher as tasks. Follow `app.state.*` to find any subsystem.
- **Commands are REST; state changes fan out over WebSocket.** Clients (`role=launcher|overlay|admin`) only *receive* events (`ws.py:Hub.broadcast`) plus send throttled `{"type":"activity"}` pings. Never push command results down the socket — mutate via a `/api/*` route, which broadcasts the resulting event. Event catalog is in `docs/ARCHITECTURE.md`.
- **Timers are server-authoritative** (`timer_engine.py`): monotonic deadlines, 1 Hz `timer.tick` broadcasts, snapshotted to SQLite so a mid-cook restart survives. Pages hold no timer state of their own.
- **The overlay is a Chromium extension, not an iframe** (recipe sites block framing). `extension/sw.js` owns the single backend WebSocket and relays events to per-page content scripts (`extension/content/overlay.js`) via runtime ports; the content script renders in a **closed shadow root** so site CSS/CSP can't touch it. The service worker caches the last snapshot and replays it to each newly-navigated page — treat that cache as possibly-stale and prefer authoritative state (see the `refreshDisplay` fetch of `GET /api/display` on port connect).
- **Idle/display** (`idle.py` + `display_ctl/`): `ACTIVE→CLOCK→OFF` state machine broadcasts `display.state`; clients paint clock/black scrims without navigating (the recipe stays underneath). Panel power is only cut in `OFF`. Backend is auto-selected: `wlopm` (Wayland/labwc on the Pi), `x11`, or `mock` (dev) — override with `RECIPEHUD_DISPLAY_BACKEND`.
- **Extraction** (`extractor.py`) is a **fallback ladder**, each layer only filling fields the previous left empty: `recipe-scrapers` → its `.schema` wrapper → **raw JSON-LD** (`_jsonld_recipe` walks every `ld+json` node for the `Recipe`, flattens `recipeInstructions`/`recipeIngredient`) → readability article text. The raw-JSON-LD layer exists because recipe-scrapers throws on some real pages whose data is otherwise intact (e.g. a `@type: ["Recipe","NewsArticle"]` node) — reach for it before hand-writing a site scraper. Hardened fetch (full Chrome headers → `curl_cffi` TLS-fingerprint retry). Results cache in `recipe_cache`; saved rows (`saved=1`) never expire and serve offline, and a re-fetch that fails or can no longer parse is discarded rather than overwriting a good saved copy.
- **Derived recipe fields live in `meta_json`, not new columns.** Both nutrition and the wine pairing hang off the `meta` dict that serializes to `recipe_cache.meta_json`, so a new derived field needs no schema migration — `_row_to_dict` surfaces the whole `meta` to clients. The **wine pairing** (`wine.py`, `GET /api/recipe/wine`) is the app's one outbound LLM call: it hits the Claude Messages API over raw `httpx` (no SDK) using `config.anthropic_api_key`, falls back to a local rule table when the key is unset or the call fails (so it works offline), and is generated **lazily on first view then cached into `meta_json`** — deliberately kept off `/extract` so the recipe paints first. `refresh=1` on the endpoint forces regeneration.
- **Schema changes need two edits, not one** (`db.py`): `schema.sql` is executed wholesale *only* on a fresh DB (`PRAGMA user_version == 0`) and must always describe the **latest** shape; existing DBs are upgraded by the `if version < N:` ALTER ladder in `Database._migrate`. So a column addition means editing `schema.sql`, appending a rung to the ladder, **and** bumping `SCHEMA_VERSION` — miss the ladder and the Pi's live DB silently lacks the column. This is why derived fields prefer `meta_json` (above).
- **The extension is hardwired to `localhost:8000`**; the served pages are not. `extension/sw.js` and `content/overlay.js` hardcode that origin and `manifest.json` lists only `http://localhost:8000/*` in `host_permissions`, so the overlay can only ever talk to the kiosk's own backend — a scratch instance on `:8010` is unreachable from it (test extension changes against the real `:8000`, or edit all three places). Pages under `frontend/` instead use `frontend/shared/ws-client.js`, which connects same-origin off `location.host` with exponential-backoff reconnect, so they work from any host that can load them.
- **Config split**: process-level env vars (`RECIPEHUD_*`) live in `config.py` (reached as `app.state.cfg` — note `cfg`, not `config`); user-tunable settings (timeouts, schedules, volume) live in the SQLite `settings` table via `settings_store.py`. Schema is `backend/recipehud/schema.sql`. **Secret** env vars (e.g. `RECIPEHUD_ANTHROPIC_API_KEY`) are kept out of git in `/etc/recipehud.env`, which the backend's systemd unit loads via `EnvironmentFile=-/etc/recipehud.env`.
- **There is no admin authentication.** `/admin` and every `/api/*` route (including `/api/system/*`) are open to anyone who can reach the backend — the appliance is meant to live on a trusted home LAN. Don't reintroduce a login without also revisiting the extension's cross-origin fetches and the kiosk's own localhost calls.
