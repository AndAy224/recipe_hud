import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from . import input_watch
from .api import debug, display, recipe, settings as settings_api, sites, system, timers
from .api.auth import UNAUTHORIZED_HEADERS, check_basic_header, is_local
from .config import CONFIG
from .db import Database
from .display_ctl import select_backend
from .idle import IdleController
from .settings_store import SettingsStore
from .timer_engine import TimerEngine
from .ws import Hub

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    db = Database(CONFIG.db_path)
    await db.connect()
    store = SettingsStore(db)
    await store.load()
    hub = Hub()
    display_backend = select_backend(CONFIG, store)
    engine = TimerEngine(db, hub.broadcast, store)
    idle = IdleController(display_backend, hub.broadcast, store, engine)

    app.state.cfg = CONFIG
    app.state.db = db
    app.state.store = store
    app.state.hub = hub
    app.state.engine = engine
    app.state.idle = idle

    await engine.restore()
    tasks = [asyncio.create_task(idle.run())]
    watcher = input_watch.start(idle, store)
    if watcher:
        tasks.append(watcher)
    log.info("recipehud up: display backend=%s debug=%s", display_backend.name, CONFIG.debug)
    yield
    for task in tasks:
        task.cancel()
    await engine.shutdown()
    await db.close()


app = FastAPI(title="Recipe HUD", lifespan=lifespan)

# The extension's content script fetches from recipe-site origins; timers and
# admin auth must work cross-origin. The API is LAN-open by design (see docs).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def admin_guard(request: Request, call_next):
    path = request.url.path
    if path == "/admin" or path.startswith("/admin/"):
        if not is_local(request) and not check_basic_header(
            request.app.state.store, request.headers.get("authorization")
        ):
            return Response(status_code=401, headers=UNAUTHORIZED_HEADERS)
    return await call_next(request)


app.include_router(sites.router)
app.include_router(timers.router)
app.include_router(settings_api.router)
app.include_router(display.router)
app.include_router(system.router)
app.include_router(recipe.router)
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


@app.get("/recipe")
async def recipe_page():
    return FileResponse(FRONTEND / "recipe" / "index.html")


app.mount("/shared", StaticFiles(directory=FRONTEND / "shared"), name="shared")
app.mount("/admin", StaticFiles(directory=FRONTEND / "admin", html=True), name="admin")
app.mount("/recipe", StaticFiles(directory=FRONTEND / "recipe"), name="recipe")
app.mount("/", StaticFiles(directory=FRONTEND / "launcher", html=True), name="launcher")
