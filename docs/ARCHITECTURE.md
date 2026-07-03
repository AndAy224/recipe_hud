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
stylesheets, so neither site CSS nor site CSP can touch it. Command traffic is
plain `fetch` to `http://localhost:8000` (the backend sends permissive CORS
headers for this). The extension service worker owns a single WebSocket and
relays events to content scripts through runtime ports; the 1 Hz tick traffic
keeps the MV3 worker alive precisely when alarms can fire, and a
`chrome.alarms` heartbeat reconnects it after idle sleeps. Alarm audio plays
in an offscreen document (extension origin → immune to page autoplay/CSP
rules; the kiosk also runs `--autoplay-policy=no-user-gesture-required`).

**Idle machine**: `ACTIVE -(idle_timeout_s)→ CLOCK -(clock_to_off_s)→ OFF`.
Clients render the clock/black scrim on `display.state` events — no navigation
happens, the recipe stays underneath. Panel power is only cut in OFF, via
`wlopm` (Bookworm labwc; `vcgencmd` does not work under KMS). Running/ringing
timers and the keep-awake toggle inhibit transitions; a ringing alarm forces
the display back on. Inside the night window ACTIVE goes straight to OFF after
a shorter timeout. Wake comes from any client's activity ping or from the
evdev touch listener (works while the panel is dark). The scrim swallows the
wake tap so it can't press a random button on the page beneath.

**Clean view**: the backend fetches the URL and parses it with
`recipe-scrapers` (schema.org fallback via `supported_only=False`), falling
back to readability text paragraphs. Results cache in `recipe_cache` — stale
copies are served if a re-fetch fails, so saved recipes work offline. The
frontend turns durations found in step text into tap-to-start timer buttons.

**Auth model** (documented tradeoff for a home LAN): requests from localhost
(the kiosk) are implicitly trusted; mutating admin routes and `/admin` require
HTTP Basic from other hosts. Read/timer routes are open on the LAN, as are
recipe save/unsave, `POST /api/send` (drive the kiosk to a URL — the point is
zero-friction from a phone; `HttpUrl` validation blocks `javascript:`/`file:`
payloads) and the weather proxy.

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
Self-update (`deploy/update.sh`) runs `git reset --hard origin/<branch>` +
`pip install` in `/opt/recipehud` (a real git checkout since install.sh
rsyncs `.git`), then kills the main pid — a pip/git failure aborts before
the kill, leaving the old code running.

## WebSocket protocol

`GET /ws?role=launcher|overlay|admin` — server → client events; all commands
go over REST. Client → server: throttled `{"type": "activity"}` pings.

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

## Repo map

- `backend/recipehud/` — FastAPI app (`main.py` wires everything in lifespan)
- `frontend/{launcher,recipe,admin,shared}/` — vanilla JS, no build step
- `extension/` — unpacked MV3 extension
- `deploy/` — install.sh, systemd unit, kiosk script, labwc/kanshi snippets
- `scripts/` — dev server, DB seed, alarm generator, WS smoke test
