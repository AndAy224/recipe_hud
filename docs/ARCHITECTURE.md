# Architecture

```
┌────────────────────────── Raspberry Pi 4 ──────────────────────────┐
│                                                                    │
│  Chromium (kiosk, portrait 720×2560)                               │
│  ├── Launcher page  /                (tiles, timers strip, clock)  │
│  ├── Clean view     /recipe?url=…    (extracted recipe)            │
│  ├── any recipe site                                               │
│  └── MV3 extension overlay on every page                           │
│      (Home btn · timers panel · alarm flash · idle scrims)         │
│              ▲ WS events        │ REST commands                    │
│              │                  ▼                                  │
│  FastAPI backend :8000 ── SQLite (sites, settings, presets,        │
│  │                        timer snapshots, recipe cache)           │
│  ├── timer_engine   asyncio, server-authoritative, 1 Hz ticks      │
│  ├── idle           ACTIVE→CLOCK→OFF, night window, inhibitors     │
│  ├── display_ctl    wlopm (Wayland) / xset (X11) / mock (dev)      │
│  │                  + ddcutil backlight (DDC/CI brightness)        │
│  ├── screensaver    seasonal photo deck for the idle clock         │
│  ├── input_watch    evdev touch listener → wake                    │
│  └── extractor      recipe-scrapers + readability fallback         │
│                                                                    │
└──────────────── LAN: http://pi.local:8000/admin ───────────────────┘
```

## Key decisions

**Timers live in the backend**, not the page. Pages navigate constantly in a
kiosk; a server-side engine with monotonic deadlines broadcasts 1 Hz ticks to
every client and snapshots to SQLite so a backend restart mid-cook keeps the
timers. `running → ringing` at zero; ringing auto-dismisses after
`alarm_auto_dismiss_s`.

**The overlay is a Chromium extension**, because recipe sites block iframes.
The content script builds its UI in a *closed shadow root* with constructed
stylesheets, so neither site CSS nor site CSP can touch it. Command traffic
goes **through the extension service worker**, never straight from the content
script: a content script's `fetch` counts as the page's request, and Chromium's
Local Network Access rules block a public page (any recipe site) from reaching
`localhost` without a per-site permission prompt. The content script sends
`{target:"sw", cmd:"api", …}` runtime messages; the worker, which holds the
`localhost:8000` host permission, performs the request and returns the result
(images cross as base64 and are rebuilt into blobs). Page scripts cannot reach
that listener, so the browser keeps third-party scripts on recipe sites away
from the backend — which is also why the backend sends **no CORS headers**.
The service worker also owns a single WebSocket and
relays events to content scripts through runtime ports; the 1 Hz tick traffic
keeps the MV3 worker alive precisely when alarms can fire, and a
`chrome.alarms` heartbeat reconnects it after idle sleeps. Alarm audio plays
in an offscreen document (extension origin → immune to page autoplay/CSP
rules; the kiosk also runs `--autoplay-policy=no-user-gesture-required`).

**Idle machine**: `ACTIVE -(idle_timeout_s)→ CLOCK -(clock_to_off_s)→ OFF`.
Clients render the clock/black scrim on `display.state` events — no navigation
happens, the recipe stays underneath. OFF is a black scrim plus a dimmed
backlight (DDC/CI); real panel power is cut only in OFF and only when the
opt-in `panel_power_off` setting is on, via `wlopm` (labwc; `vcgencmd` does
not work under KMS) — off by default because this panel's touch controller
loses power with it. Running/ringing
timers and the keep-awake toggle inhibit transitions; a ringing alarm forces
the display back on. Inside the night window ACTIVE goes straight to OFF after
a shorter timeout. Wake comes from any client's activity ping or from the
evdev touch listener (works while the panel is dark). The scrim swallows the
wake tap so it can't press a random button on the page beneath.

**Holiday screensaver**: a deck (`backend/recipehud/decks/*.json`) is a
curated list of real recipe URLs with a short name, category and blurb, plus
the date window it is in season (Christmas: Nov 1 – Jan 6). While one is in
season and `screensaver_enabled`, the CLOCK state *is* the screensaver — a
rotating photo card per recipe over the clock — and CLOCK stops timing out to
OFF in the daytime; the night window still blacks it out, and when the window
ends an OFF panel returns to the screensaver on its own. No new display state:
clients fetch `GET /api/screensaver` on entering CLOCK and fall back to the
plain clock when it isn't active. The UI has a single source,
`extension/content/screensaver.js`: the overlay runs it as a content script,
and the launcher's no-extension fallback loads the same file from
`/ext/screensaver.js`. Photos are local `/media` snapshots (a boot-time warm
pass extracts each deck URL and snapshots its image) fetched as blobs, so they
work offline and no recipe site's CSP can block them. Nothing on the
screensaver is tappable — the evdev watcher wakes on touch-down before
Chromium would deliver a click — so a card is saved from the toast that
appears right after a wake, or from the launcher's "Holiday ideas" section
(`POST /api/screensaver/add`, which tags it `christmas` + its category).

**Backlight**: DPMS-off can't be used on this panel (it powers the touch
controller), but DDC/CI brightness can: `display_ctl/ddc.py` drives VCP 0x10
via `ddcutil`. The idle machine sets a target per state — `brightness_active`
by day, `brightness_night_pct` of it inside the night window, `brightness_off`
behind the black OFF scrim — and a single worker applies it. Writes happen only
when the target changes (scalers often persist brightness to EEPROM), every
write is read back (ddcutil's exit code is 0 even for a rejected value), and a
failed write retries until it lands so the panel can't be stranded dark. On
the first probe the panel's own level is adopted as `brightness_active`. The
mock display backend never gets a real backlight, so a scratch instance can't
fight the appliance for the panel.

**Clean view**: the backend fetches the URL and parses it with
`recipe-scrapers` (schema.org fallback via `supported_only=False`), falling
back to readability text paragraphs. Results cache in `recipe_cache` — stale
copies are served if a re-fetch fails, so saved recipes work offline. The
frontend turns durations found in step text into tap-to-start timer buttons.

Fetching is hardened in layers (verified against 18 popular sites —
`scripts/test_extraction.py`): a full Chrome header set first (some sites
403 a bare User-Agent), then a `curl_cffi` retry with a real Chrome TLS
fingerprint for sites behind JA3 fingerprinting (AllRecipes, Serious Eats,
Simply Recipes…). Parsing also falls back from the site-specific scraper to
the page's raw schema.org data when a redesign breaks the scraper's
selectors. Sites that serve no structured data at all (e.g. Smitten Kitchen)
land on the readable-article view by design.

**Auth model** (documented tradeoff for a home LAN): there is none. `/admin`
and every `/api/*` route — including the mutating admin ones and
`/api/system/*` — answer any request that reaches the backend. The appliance
is a kitchen panel on a trusted home network, and the friction of a login on
a touchscreen (and in the extension's requests) bought nothing
the network boundary wasn't already providing. This is also why `POST
/api/send` is open: driving the kiosk to a URL from a phone should be
zero-friction (`HttpUrl` validation still blocks `javascript:`/`file:`
payloads). Anyone exposing the backend beyond the LAN must put
authentication in front of it.

**My Recipes**: saving marks the cached extraction row (`recipe_cache.saved`)
— saved rows never expire and always serve from cache, so the library works
offline and survives the source page disappearing. Saving also downloads the
hero image into `data/media/` (served at `/media/…`, cleaned up on unsave,
backfilled at startup for older saves), making each saved recipe a fully
self-contained snapshot. Two guards protect a confirmed-good copy: a re-fetch
that fails leaves the cache untouched, and a re-fetch that "succeeds" but can
no longer parse a recipe (site redesign → article fallback) is discarded in
favor of the saved copy.

**Night dim**: when the configured night window flips, the idle loop
broadcasts `night.state`; every UI raises a `pointer-events: none` warm veil
(never a CSS `filter` on an ancestor — that would re-parent every
`position: fixed` scrim). Requires night mode to be enabled.

**Ops (admin → System)**: health via stdlib collectors (`sysinfo.py`;
throttle state read from the firmware sysfs node with a `vcgencmd` fallback);
logs from an in-memory ring handler (`log_buffer.py`). Backup = `VACUUM INTO`
(consistent single-file copy under WAL, no sidecars) zipped with `media/`.
Restore is staged as `data/restore-pending.zip` and applied at the NEXT
startup before the DB opens — the live `-wal`/`-shm` are deleted first and
the old DB is kept as `.pre-restore`. Restarting the backend never needs
sudo: the process exits itself and systemd's `Restart=always` revives it.
Self-update (`deploy/update.sh`) runs `git reset --hard origin/<branch>` in
`/opt/recipehud` (a real git checkout since install.sh rsyncs `.git`), runs
`pip install` only when `pyproject.toml` differs from the last successful
install (`data/deps.sha256`), then kills the main pid. It holds a `flock`, so
runs can't overlap. If the install does not complete — pip fails, or the
script is killed mid-way (it lives in the backend's cgroup, so a backend
restart takes it down too) — it resets the tree back to the commit it started
from and records `failed`, so the old code keeps running on the packages it
was started with. The status file carries the script's pid; the API treats an
update as "running" only while that pid is alive.

## WebSocket protocol

`GET /ws?role=launcher|overlay|admin|recipe|phone` — server → client events;
all commands go over REST. Client → server: throttled `{"type": "activity"}`
pings — which wake the panel, so the phone pages (`/timers`, `/list`) never
send them.

| Event | Payload |
|---|---|
| `snapshot` | `{timers, display, settings}` on connect |
| `timer.tick` | full timer list, 1 Hz while any timer exists |
| `timer.created/updated/cancelled` | timer object / `{id}` |
| `alarm.start` / `alarm.stop` | `{id, label, volume}` / `{id}` |
| `display.state` | `{state: active\|clock\|off}` |
| `navigate` | `{url}` (admin "open on kiosk", `POST /api/send`) |
| `settings.updated` | changed keys |
| `night.state` | `{night: bool}` — night window flipped |
| `recipes.updated` | `{}` — a recipe was saved/unsaved |
| `heartbeat` | `{}` every 20 s — lets clients tell a live socket from a half-open one (they reconnect after ~70 s of silence) |

## Repo map

- `backend/recipehud/` — FastAPI app (`main.py` wires everything in lifespan);
  `decks/` holds the seasonal screensaver decks
- `frontend/{launcher,recipe,admin,shared}/` — vanilla JS, no build step;
  `frontend/{send,timers,list}/` are the phone pages (push a URL, live timers,
  a recipe's ingredients as a checklist)
- `extension/` — unpacked MV3 extension
- `deploy/` — install.sh, systemd unit, kiosk script, labwc/kanshi snippets
- `scripts/` — dev server, DB seed, alarm generator, and the standalone test
  scripts (`test_extraction`, `test_scale`, `test_wake`, `ws_smoke`), plus
  `screenshots.py` for the README images
