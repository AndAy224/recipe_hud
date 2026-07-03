"""System-health collectors for the admin panel. All stdlib, all best-effort:
every field degrades to None off the Pi (Windows dev, containers, etc.)."""

import asyncio
import importlib.metadata
import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from .config import CONFIG

log = logging.getLogger(__name__)

STARTED_AT = time.time()

THROTTLE_SYSFS = Path("/sys/devices/platform/soc/soc:firmware/get_throttled")
TEMP_SYSFS = Path("/sys/class/thermal/thermal_zone0/temp")

THROTTLE_BITS = {
    0: "under_voltage",
    1: "freq_capped",
    2: "throttled",
    3: "soft_temp_limit",
}


def version() -> str:
    try:
        return importlib.metadata.version("recipehud")
    except importlib.metadata.PackageNotFoundError:
        return "dev"


def git_rev() -> str | None:
    repo = CONFIG.frontend_dir.parent
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=2,
        )
        if out.returncode == 0:
            return out.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    # Fallback: read .git/HEAD by hand (works without git in PATH).
    try:
        head = (repo / ".git" / "HEAD").read_text().strip()
        if head.startswith("ref: "):
            return (repo / ".git" / head[5:]).read_text().strip()[:7]
        return head[:7]
    except OSError:
        return None


def memory() -> dict | None:
    try:
        fields = {}
        for line in Path("/proc/meminfo").read_text().splitlines():
            key, _, rest = line.partition(":")
            if key in ("MemTotal", "MemAvailable"):
                fields[key] = int(rest.strip().split()[0]) * 1024
        if "MemTotal" in fields:
            return {"total": fields["MemTotal"], "available": fields.get("MemAvailable")}
    except OSError:
        pass
    return None


def cpu_temp_c() -> float | None:
    try:
        return int(TEMP_SYSFS.read_text().strip()) / 1000
    except (OSError, ValueError):
        return None


def _decode_throttled(raw: int) -> dict:
    return {
        "raw": hex(raw),
        "current": {name: bool(raw & (1 << bit)) for bit, name in THROTTLE_BITS.items()},
        # "Since boot" bits are best-effort: the kernel hwmon poller can
        # interact with the firmware's sticky flags.
        "occurred": {name: bool(raw & (1 << (bit + 16))) for bit, name in THROTTLE_BITS.items()},
    }


async def throttled() -> dict | None:
    try:
        return _decode_throttled(int(THROTTLE_SYSFS.read_text().strip(), 16))
    except (OSError, ValueError):
        pass
    if sys.platform != "linux":
        return None
    try:
        proc = await asyncio.create_subprocess_exec(
            "vcgencmd", "get_throttled",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=1)
        # Output: "throttled=0x50000"
        return _decode_throttled(int(out.decode().strip().split("=")[1], 16))
    except Exception:
        return None


def _dir_stats(path: Path) -> tuple[int, int]:
    total, count = 0, 0
    if path.is_dir():
        for f in path.iterdir():
            if f.is_file():
                total += f.stat().st_size
                count += 1
    return total, count


async def health() -> dict:
    db_bytes = sum(
        p.stat().st_size
        for suffix in ("", "-wal", "-shm")
        if (p := Path(str(CONFIG.db_path) + suffix)).exists()
    )
    media_bytes, media_files = _dir_stats(CONFIG.media_dir)
    disk = shutil.disk_usage(CONFIG.db_path.parent)
    return {
        "version": version(),
        "git_rev": git_rev(),
        "started_at": STARTED_AT,
        "uptime_s": round(time.time() - STARTED_AT, 1),
        "platform": sys.platform,
        "disk": {"free": disk.free, "total": disk.total},
        "memory": memory(),
        "cpu_temp_c": cpu_temp_c(),
        "throttled": await throttled(),
        "load_avg": list(os.getloadavg()) if hasattr(os, "getloadavg") else None,
        "db_bytes": db_bytes,
        "media_bytes": media_bytes,
        "media_files": media_files,
    }
