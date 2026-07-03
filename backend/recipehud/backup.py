"""Backup and restore of the DB + saved-recipe images.

Backup: `VACUUM INTO` on the live connection produces a single consistent,
compacted file (no -wal/-shm sidecars), zipped together with data/media/.

Restore: destructive, so it is staged and applied at the NEXT startup, before
the database is opened — apply_pending_restore() runs first in the lifespan.
The previous DB is kept as recipehud.db.pre-restore, and the staged zip is
only deleted after a successful apply, so a crash mid-apply retries."""

import asyncio
import datetime
import json
import logging
import shutil
import sqlite3
import tempfile
import zipfile
from pathlib import Path

from .config import Config
from .db import SCHEMA_VERSION, Database
from . import sysinfo

log = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = 250 * 1024 * 1024
PENDING_NAME = "restore-pending.zip"


def pending_path(cfg: Config) -> Path:
    return cfg.db_path.parent / PENDING_NAME


# ---------------------------------------------------------------- backup

async def build_backup(db: Database, cfg: Config) -> tuple[Path, Path]:
    """Returns (zip_path, tmpdir). Caller must remove tmpdir when done."""
    tmpdir = Path(tempfile.mkdtemp(prefix="recipehud-backup-"))
    db_copy = tmpdir / "recipehud.db"
    await db.conn.execute("VACUUM INTO ?", (str(db_copy),))
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M")
    zip_path = tmpdir / f"recipehud-backup-{stamp}.zip"
    manifest = {
        "app": "recipehud",
        "schema_version": SCHEMA_VERSION,
        "version": sysinfo.version(),
        "git_rev": sysinfo.git_rev(),
        "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    await asyncio.to_thread(_build_zip, zip_path, db_copy, cfg.media_dir, manifest)
    return zip_path, tmpdir


def _build_zip(zip_path: Path, db_copy: Path, media_dir: Path, manifest: dict) -> None:
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(db_copy, "recipehud.db")
        zf.writestr("manifest.json", json.dumps(manifest, indent=2))
        if media_dir.is_dir():
            for f in media_dir.iterdir():
                if f.is_file():
                    zf.write(f, f"media/{f.name}")
    db_copy.unlink()


# --------------------------------------------------------------- restore

def validate_upload(zip_path: Path) -> None:
    """Raises ValueError with a user-facing message if the zip is unusable."""
    if zip_path.stat().st_size > MAX_UPLOAD_BYTES:
        raise ValueError("Backup file is too large")
    if not zipfile.is_zipfile(zip_path):
        raise ValueError("Not a zip file")
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        for name in names:
            if name.startswith("/") or ".." in Path(name).parts:
                raise ValueError("Unsafe path inside the zip")
        if "recipehud.db" not in names:
            raise ValueError("Zip does not contain recipehud.db — not a Recipe HUD backup")
        with tempfile.TemporaryDirectory() as tmp:
            extracted = Path(zf.extract("recipehud.db", tmp))
            conn = sqlite3.connect(f"file:{extracted}?mode=ro", uri=True)
            try:
                check = conn.execute("PRAGMA integrity_check").fetchone()[0]
                schema = conn.execute("PRAGMA user_version").fetchone()[0]
            except sqlite3.DatabaseError as exc:
                raise ValueError(f"Database inside the backup is unreadable ({exc})")
            finally:
                conn.close()
            if check != "ok":
                raise ValueError("Database inside the backup fails its integrity check")
            if schema > SCHEMA_VERSION:
                raise ValueError(
                    f"Backup schema v{schema} is newer than this app (v{SCHEMA_VERSION}) "
                    "— update the app first")


def stage_restore(upload_tmp: Path, cfg: Config) -> None:
    cfg.db_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(upload_tmp), pending_path(cfg))


def apply_pending_restore(cfg: Config) -> None:
    """Runs at startup BEFORE the database is opened."""
    pending = pending_path(cfg)
    if not pending.exists():
        return
    log.warning("applying staged restore from %s", pending)
    db_path = cfg.db_path
    backup_copy = db_path.with_suffix(".db.pre-restore")
    try:
        if db_path.exists():
            shutil.copy2(db_path, backup_copy)
        # Stale WAL/SHM replayed against a different main file corrupts it.
        for suffix in ("-wal", "-shm"):
            Path(str(db_path) + suffix).unlink(missing_ok=True)
        with zipfile.ZipFile(pending) as zf:
            with zf.open("recipehud.db") as src, open(db_path, "wb") as dst:
                shutil.copyfileobj(src, dst)
            if cfg.media_dir.is_dir():
                shutil.rmtree(cfg.media_dir)
            cfg.media_dir.mkdir(parents=True, exist_ok=True)
            for name in zf.namelist():
                if name.startswith("media/") and not name.endswith("/"):
                    target = cfg.media_dir / Path(name).name
                    with zf.open(name) as src, open(target, "wb") as dst:
                        shutil.copyfileobj(src, dst)
        pending.unlink()  # only after full success, so a crash retries
        log.warning("restore applied; previous database kept at %s", backup_copy)
    except Exception:
        log.exception("restore failed; putting the previous database back")
        if backup_copy.exists():
            shutil.copy2(backup_copy, db_path)
        pending.unlink(missing_ok=True)
