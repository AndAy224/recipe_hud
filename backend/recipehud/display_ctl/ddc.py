"""Panel backlight brightness over DDC/CI (ddcutil, VCP 0x10).

Separate from DisplayBackend on purpose: that one cuts panel *power*, which on
this appliance's panel also kills the USB touch controller (see
settings_store.panel_power_off). Dimming the backlight over DDC leaves the
touch controller alone — verified at brightness 0 — so this is the safe way to
make the black OFF screen actually dark.

Two facts about ddcutil drive the shape of this file:
  * its exit code lies: `setvcp 10 999` exits 0 (and maxes the panel), so
    every write is clamped and then read back to confirm it landed;
  * many scalers persist brightness to EEPROM, so we write only when the
    target changes — a handful of writes a night, never a periodic re-assert.
"""

import asyncio
import logging
import re

from ..settings_store import SettingsStore

log = logging.getLogger(__name__)

CALL_TIMEOUT_S = 10
PROBE_RETRY_S = 120
BACKOFF_MIN_S = 5
BACKOFF_MAX_S = 60
# Below this, an adopted "active" level would be indistinguishable from a
# panel we left dimmed ourselves (e.g. killed mid-night), so never adopt it.
ADOPT_FLOOR = 5

_BUS_RE = re.compile(r"I2C bus:\s+/dev/i2c-(\d+)")
_DRM_RE = re.compile(r"DRM connector:\s+(\S+)")
_VCP_RE = re.compile(r"VCP 10 C (\d+) (\d+)")


class NullBacklight:
    """No backlight control (dev/mock, or no ddcutil / no DDC display)."""

    name = "none"
    available = False

    def __init__(self, reason: str = "disabled"):
        self.reason = reason

    def request(self, value: int) -> None:
        pass

    async def run(self) -> None:
        return

    async def restore(self, value: int) -> None:
        return

    def status(self) -> dict:
        return {"backend": self.name, "available": False, "level": None,
                "target": None, "error": self.reason}


class DdcBacklight:
    name = "ddcutil"

    def __init__(self, store: SettingsStore):
        self.store = store
        self.available = False
        self.bus: int | None = None
        self.max = 100
        self.applied: int | None = None  # 0-100, as last read back from the panel
        self.desired: int | None = None
        self.error: str | None = None
        self._wake = asyncio.Event()
        self._lock = asyncio.Lock()  # one ddcutil on the bus at a time

    # -- public -----------------------------------------------------------

    def request(self, value: int) -> None:
        """Non-blocking: record the target; the run() worker applies it."""
        value = max(0, min(100, int(value)))
        if value != self.desired:
            self.desired = value
            self._wake.set()

    def status(self) -> dict:
        return {"backend": self.name, "available": self.available, "level": self.applied,
                "target": self.desired, "bus": self.bus, "error": self.error}

    async def run(self) -> None:
        while not await self._probe():
            if self.error == "ddcutil not installed":
                return
            await asyncio.sleep(PROBE_RETRY_S)
        # Targets requested before the probe were computed from settings the
        # probe may just have changed (the adopted brightness_active) — acting
        # on one would flash the panel to the generic default for a second.
        # The idle loop re-requests the right target within a tick.
        self.desired = None
        backoff = BACKOFF_MIN_S
        while True:
            # Clear before checking: request() can only run at an await, so
            # nothing slips in between and a wakeup can never be lost.
            self._wake.clear()
            if self.desired is None or self.desired == self.applied:
                await self._wake.wait()
                continue
            target = self.desired
            if await self._set(target):
                backoff = BACKOFF_MIN_S
            else:
                # Keep retrying for as long as it takes: giving up could leave
                # the panel stuck dark while someone is trying to use it.
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, BACKOFF_MAX_S)

    async def restore(self, value: int) -> None:
        """Best-effort, bounded: used at shutdown so a stopped backend never
        leaves the panel dimmed. (A killed one is fixed on the next start,
        which comes up ACTIVE.)"""
        if not self.available:
            return
        try:
            await asyncio.wait_for(self._set(max(0, min(100, int(value)))), 3)
        except Exception as exc:
            log.warning("backlight restore failed: %s", exc)

    # -- internals ----------------------------------------------------------

    async def _probe(self) -> bool:
        try:
            rc, out, err = await self._ddc("detect", "--brief")
        except FileNotFoundError:
            self.error = "ddcutil not installed"
            log.info("ddcutil not installed; backlight control disabled")
            return False
        except Exception as exc:
            self.error = f"detect failed: {exc}"
            log.warning("ddcutil %s", self.error)
            return False
        bus = self._pick_bus(out)
        if bus is None:
            self.error = "no DDC/CI display found"
            log.warning("backlight: %s (%s)", self.error, (err or out).strip()[:200])
            return False
        self.bus = bus
        current = await self._read()
        if current is None:
            self.error = f"brightness unreadable on /dev/i2c-{bus}"
            log.warning("backlight: %s", self.error)
            return False
        self.applied = current
        self.available = True
        self.error = None
        log.info("backlight: ddcutil on /dev/i2c-%s, brightness %s/100", bus, current)
        # First time we ever take control: adopt the panel's own level as the
        # day brightness, instead of jumping to the generic default. Only on a
        # panel we have never driven, and never a near-black level.
        if not self.store.is_stored("brightness_active") and current >= ADOPT_FLOOR:
            await self.store.patch({"brightness_active": current})
            log.info("backlight: adopted brightness_active=%s from the panel", current)
        return True

    def _pick_bus(self, detect_out: str) -> int | None:
        """Prefer the display on the configured output; else the first one."""
        wanted = str(self.store.get("display_output") or "").strip()
        first = None
        for block in detect_out.split("\n\n"):
            bus = _BUS_RE.search(block)
            if not bus:
                continue
            if "Invalid display" in block:
                continue
            first = first if first is not None else int(bus.group(1))
            drm = _DRM_RE.search(block)
            if wanted and drm and drm.group(1).endswith(wanted):
                return int(bus.group(1))
        return first

    async def _read(self) -> int | None:
        """Current brightness as 0-100 (also refreshes self.max)."""
        raw = await self._read_raw()
        return None if raw is None else round(raw * 100 / self.max)

    async def _read_raw(self) -> int | None:
        try:
            rc, out, _ = await self._ddc("--bus", str(self.bus), "getvcp", "10", "--brief")
        except Exception as exc:
            log.warning("backlight read failed: %s", exc)
            return None
        match = _VCP_RE.search(out)
        if rc != 0 or not match:
            return None
        self.max = int(match.group(2)) or 100
        return int(match.group(1))

    async def _set(self, value: int) -> bool:
        raw = round(value * self.max / 100)
        try:
            await self._ddc("--bus", str(self.bus), "setvcp", "10", str(raw))
        except Exception as exc:
            self.error = f"set failed: {exc}"
            log.warning("backlight %s", self.error)
            return False
        # The exit code is meaningless (see module doc) — trust the read-back.
        current = await self._read_raw()
        if current is None:
            self.error = "set not confirmed: read-back failed"
            log.warning("backlight %s", self.error)
            return False
        if current != raw:
            # The panel answered but clamped/quantized the value. Accept it:
            # retrying would rewrite (possibly EEPROM-backed) settings every
            # backoff tick forever without ever converging.
            log.warning("backlight: asked for raw %s, panel settled on %s", raw, current)
        if self.applied != value:
            log.info("backlight: %s -> %s", self.applied, value)
        self.applied = value
        self.error = None
        return True

    async def _ddc(self, *args: str) -> tuple[int, str, str]:
        async with self._lock:
            proc = await asyncio.create_subprocess_exec(
                "ddcutil", *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                out, err = await asyncio.wait_for(proc.communicate(), CALL_TIMEOUT_S)
            except asyncio.TimeoutError:
                raise RuntimeError(f"ddcutil {' '.join(args)} timed out")
            finally:
                # Timeout or task cancellation (shutdown): never leave a stray
                # ddcutil holding the I2C bus for the next call.
                if proc.returncode is None:
                    proc.kill()
                    await proc.wait()
            return proc.returncode, out.decode(errors="replace"), err.decode(errors="replace")
