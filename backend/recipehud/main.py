import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.routing import Mount

from . import backup, input_watch, log_buffer, screensaver
from .api import (
    debug, display, recipe, screensaver as screensaver_api, send, settings as settings_api,
    sites, system, timers, weather,
)
from .config import CONFIG
from .db import Database
from .display_ctl import select_backend, select_backlight
from .extractor import snapshot_image
from .idle import IdleController
from .settings_store import SettingsStore
from .timer_engine import TimerEngine
from .ws import Hub

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
logging.getLogger().addHandler(log_buffer.ring)
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # A staged restore must swap the DB before it is opened.
    backup.apply_pending_restore(CONFIG)
    backup.clear_scratch(CONFIG)
    db = Database(CONFIG.db_path)
    await db.connect()
    store = SettingsStore(db)
    await store.load()
    hub = Hub()
    display_backend = select_backend(CONFIG, store)
    backlight = select_backlight(CONFIG, store)
    engine = TimerEngine(db, hub.broadcast, store)
    idle = IdleController(display_backend, hub.broadcast, store, engine, backlight)

    app.state.cfg = CONFIG
    app.state.db = db
    app.state.store = store
    app.state.hub = hub
    app.state.engine = engine
    app.state.idle = idle
    app.state.backlight = backlight

    await engine.restore()
    tasks = [asyncio.create_task(idle.run()),
             asyncio.create_task(backlight.run()),
             asyncio.create_task(_heartbeat(hub)),
             asyncio.create_task(_backfill_image_snapshots(db)),
             asyncio.create_task(screensaver.warm_loop(db, store))]
    watcher = input_watch.start(idle, store)
    if watcher:
        tasks.append(watcher)
    log.info("recipehud up: display backend=%s backlight=%s debug=%s",
             display_backend.name, backlight.name, CONFIG.debug)
    yield
    for task in tasks:
        task.cancel()
    # A stopped backend must not leave the panel dimmed (bounded, best-effort).
    await backlight.restore(store.get("brightness_active"))
    await engine.shutdown()
    await db.close()


HEARTBEAT_S = 20


async def _heartbeat(hub: Hub) -> None:
    """Periodic beat so a client can tell a live socket from a half-open one.

    A wedged TCP connection never fires `onclose` in the browser, so without a
    steady signal the overlay can sit on a zombie socket indefinitely and never
    learn the display woke up — a black scrim with no way back. It also gives
    Hub.broadcast a regular chance to time out and reap such clients server-side.
    """
    while True:
        await asyncio.sleep(HEARTBEAT_S)
        await hub.broadcast("heartbeat", {})


async def _backfill_image_snapshots(db: Database) -> None:
    """Recipes saved before local image snapshots existed get theirs on boot."""
    rows = await db.fetchall(
        "SELECT url FROM recipe_cache WHERE saved = 1 "
        "AND image_local IS NULL AND image_url IS NOT NULL")
    for row in rows:
        await snapshot_image(db, row["url"])


app = FastAPI(title="Recipe HUD", lifespan=lifespan)

# No CORS headers, on purpose. The API (admin included) is LAN-open by design,
# but nothing legitimate calls it cross-origin: the served pages are same-
# origin, and the overlay reaches it through the extension's service worker,
# whose host permission needs no CORS. A wildcard here would let any web page
# in any browser on the LAN read every response and drive /api/system/*.


@app.middleware("http")
async def static_mount_trailing_slash(request: Request, call_next):
    # A StaticFiles(html=True) mount at "/x" only serves "/x/", so the bare "/x"
    # people type or link (e.g. /admin, /send) 404s. Redirect it to the slash
    # form. Driven by the mount table (_STATIC_HTML_MOUNTS, built after the
    # mounts below), so every future html=True mount is covered automatically.
    path = request.url.path
    if request.method in ("GET", "HEAD") and path in _STATIC_HTML_MOUNTS:
        target = f"{path}/?{request.url.query}" if request.url.query else f"{path}/"
        return RedirectResponse(target, status_code=307)
    return await call_next(request)


app.include_router(sites.router)
app.include_router(timers.router)
app.include_router(settings_api.router)
app.include_router(display.router)
app.include_router(system.router)
app.include_router(recipe.router)
app.include_router(weather.router)
app.include_router(send.router)
app.include_router(screensaver_api.router)
if CONFIG.debug:
    app.include_router(debug.router)


@app.get("/healthz")
async def healthz():
    return {"ok": True}


def build_snapshot(state) -> dict:
    return {
        "timers": state.engine.list(),
        "display": state.idle.status(),
        "settings": state.store.public(),
    }


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    role = ws.query_params.get("role", "unknown")
    hub: Hub = ws.app.state.hub
    await hub.connect(ws, role)
    try:
        await ws.send_json({"type": "snapshot", "data": build_snapshot(ws.app.state)})
        while True:
            msg = await ws.receive_json()
            if msg.get("type") == "activity":
                ws.app.state.idle.activity(f"ws:{role}")
    except WebSocketDisconnect:
        pass
    except Exception:
        log.debug("ws client dropped", exc_info=True)
    finally:
        hub.disconnect(ws)


FRONTEND = CONFIG.frontend_dir


# /recipe keeps an explicit route: its mount below is NOT html=True (it serves
# assets only), and the page is reached with a ?url= query the redirect
# middleware would bounce. /send and /admin are html=True mounts, so the
# trailing-slash middleware handles their bare paths — no per-route code needed.
@app.get("/recipe")
async def recipe_page():
    return FileResponse(FRONTEND / "recipe" / "index.html")


# The screensaver UI has exactly one source: the extension's content script.
# The launcher's no-extension fallback loads the very same file from here, so
# the two can never drift. (A change to it therefore needs a backend update for
# the launcher AND a kiosk restart for the extension.)
@app.get("/ext/screensaver.js")
async def screensaver_js():
    return FileResponse(CONFIG.extension_dir / "content" / "screensaver.js",
                        media_type="text/javascript")


CONFIG.media_dir.mkdir(parents=True, exist_ok=True)
app.mount("/media", StaticFiles(directory=CONFIG.media_dir), name="media")
app.mount("/shared", StaticFiles(directory=FRONTEND / "shared"), name="shared")
app.mount("/admin", StaticFiles(directory=FRONTEND / "admin", html=True), name="admin")
app.mount("/recipe", StaticFiles(directory=FRONTEND / "recipe"), name="recipe")
app.mount("/send", StaticFiles(directory=FRONTEND / "send", html=True), name="send")
app.mount("/", StaticFiles(directory=FRONTEND / "launcher", html=True), name="launcher")

# Bare-path prefixes for html=True mounts (e.g. "/admin", "/send"), read by the
# static_mount_trailing_slash middleware so "/admin" redirects to "/admin/"
# instead of 404ing. The root ("/") mount is excluded — it has no bare form.
_STATIC_HTML_MOUNTS = frozenset(
    route.path
    for route in app.routes
    if isinstance(route, Mount)
    and isinstance(route.app, StaticFiles)
    and getattr(route.app, "html", False)
    and route.path not in ("", "/")
)
