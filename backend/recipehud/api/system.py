import asyncio
import json
import logging
import os
import shutil
import signal
import sys
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from .. import backup, sysinfo
from ..config import CONFIG
from ..log_buffer import ring
from ..models import NavigateBody
from .auth import require_admin

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/system", tags=["system"],
                   dependencies=[Depends(require_admin)])

REPO_ROOT = CONFIG.frontend_dir.parent
UPDATE_STATUS = CONFIG.db_path.parent / "update-status.json"
UPDATE_LOG = CONFIG.db_path.parent / "update.log"


def _schedule_exit(delay: float = 0.5) -> None:
    """Graceful self-restart: exit after the response is sent; systemd's
    Restart=always brings the backend back up (see recipehud-backend.service)."""
    loop = asyncio.get_event_loop()
    loop.call_later(delay, os.kill, os.getpid(), signal.SIGTERM)


# ------------------------------------------------------------------ kiosk

@router.post("/kiosk/restart")
async def restart_kiosk():
    """Kill the kiosk Chromium; start-kiosk.sh's loop relaunches it."""
    if sys.platform != "linux":
        raise HTTPException(400, "Kiosk restart only works on the Pi")
    proc = await asyncio.create_subprocess_exec(
        "pkill", "-f", "recipehud-chromium",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await proc.communicate()
    return {"ok": True}


@router.post("/navigate")
async def navigate(request: Request, body: NavigateBody):
    """Push a URL to the kiosk (overlay/launcher navigates on the event)."""
    await request.app.state.hub.broadcast("navigate", {"url": str(body.url)})
    return {"ok": True}


# ----------------------------------------------------------------- health

@router.get("/health")
async def health(request: Request):
    data = await sysinfo.health()
    data["ws_clients"] = request.app.state.hub.client_count()
    return data


@router.get("/logs")
async def logs(limit: int = 200):
    records = list(ring.records)[-limit:]
    update_log = []
    if UPDATE_LOG.exists():
        try:
            update_log = UPDATE_LOG.read_text(errors="replace").splitlines()[-50:]
        except OSError:
            pass
    return {"records": records, "update_log": update_log}


@router.post("/restart-backend")
async def restart_backend():
    if sys.platform != "linux":
        raise HTTPException(400, "Self-restart requires systemd (restart the dev server by hand)")
    _schedule_exit()
    return {"ok": True, "restarting": True}


# --------------------------------------------------------- backup/restore

@router.get("/backup")
async def download_backup(request: Request):
    zip_path, tmpdir = await backup.build_backup(request.app.state.db, CONFIG)
    return FileResponse(
        zip_path,
        filename=zip_path.name,
        media_type="application/zip",
        background=BackgroundTask(shutil.rmtree, tmpdir, ignore_errors=True),
    )


@router.post("/restore")
async def restore(file: UploadFile):
    fd, tmp_name = tempfile.mkstemp(suffix=".zip")
    os.close(fd)  # keep no handle open, or Windows can't move/unlink it
    tmp = Path(tmp_name)
    try:
        with open(tmp, "wb") as out:
            await asyncio.to_thread(shutil.copyfileobj, file.file, out)
        try:
            await asyncio.to_thread(backup.validate_upload, tmp)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        backup.stage_restore(tmp, CONFIG)
    finally:
        tmp.unlink(missing_ok=True)
    if sys.platform == "linux":
        _schedule_exit()
        return {"ok": True, "restarting": True}
    return {"ok": True, "restarting": False,
            "note": "Staged. Restart the dev server to apply."}


# ------------------------------------------------------------- self-update

def _update_status() -> dict | None:
    try:
        return json.loads(UPDATE_STATUS.read_text())
    except (OSError, ValueError):
        return None


@router.post("/update")
async def update():
    if sys.platform != "linux":
        raise HTTPException(400, "Self-update only works on the Pi")
    status = _update_status()
    if status and status.get("phase") in ("fetching", "installing") and status.get("ok"):
        raise HTTPException(409, "An update is already running")
    script = REPO_ROOT / "deploy" / "update.sh"
    if not script.exists():
        raise HTTPException(400, "deploy/update.sh not found — re-run install.sh once")
    await asyncio.create_subprocess_exec(
        "bash", str(script),
        start_new_session=True,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    return {"ok": True, "started": True}


@router.get("/update/status")
async def update_status():
    tail = []
    if UPDATE_LOG.exists():
        try:
            tail = UPDATE_LOG.read_text(errors="replace").splitlines()[-50:]
        except OSError:
            pass
    return {"status": _update_status(), "log": tail}
