import asyncio
import datetime
import logging
import time

from .settings_store import SettingsStore
from .timer_engine import TimerEngine

log = logging.getLogger(__name__)

ACTIVE = "active"
CLOCK = "clock"
OFF = "off"

# How often touch may re-announce an unchanged ACTIVE state. Cheap insurance
# against a client whose view of the state has drifted (see activity()).
RESYNC_INTERVAL_S = 2.0


def _spawn(coro) -> None:
    """Fire-and-forget from a sync caller inside the running loop."""
    asyncio.get_running_loop().create_task(coro)


class IdleController:
    """Idle state machine: ACTIVE -(idle_timeout)-> CLOCK -(clock_to_off)-> OFF.

    Clients render clock/black scrims on `display.state` events; we only cut
    panel power in OFF. Inside the night window, ACTIVE goes straight to OFF
    after the (shorter) night timeout. Touch/WS activity wakes from any state.
    """

    def __init__(self, display, broadcast, store: SettingsStore, engine: TimerEngine):
        self.display = display
        self.broadcast = broadcast
        self.store = store
        self.engine = engine
        self.state = ACTIVE
        self.last_activity = time.monotonic()
        self._state_since = time.monotonic()
        self._last_announce = 0.0
        self._waking = False
        self._night: bool | None = None
        store.on_change(self._on_settings_change)

    # -- queries -------------------------------------------------------

    def inhibitors(self) -> list[str]:
        result = []
        if self.engine.has_active():
            result.append("timer")
        if self.store.get("keep_awake"):
            result.append("keep_awake")
        return result

    def status(self) -> dict:
        return {
            "state": self.state,
            "power": self.display.is_on(),
            "inhibitors": self.inhibitors(),
            "night": self._in_night_window(),
            "backend": self.display.name,
        }

    # -- activity / manual control --------------------------------------

    def activity(self, source: str = "unknown") -> None:
        self.last_activity = time.monotonic()
        if self.state != ACTIVE or not self.display.is_on():
            log.info("wake on activity (%s)", source)
            _spawn(self.wake())
            return
        # Already ACTIVE here, but a client can still be painting a stale
        # scrim: a dropped event, a zombie socket, or a late /api/display
        # response that landed after a newer live one. Nothing else ever
        # re-announces an unchanged ACTIVE, so without this a stuck client is
        # unrecoverable by touch — the scrim swallows every tap and the only
        # way out is a reboot. Re-announce instead, throttled.
        now = time.monotonic()
        if now - self._last_announce >= RESYNC_INTERVAL_S:
            _spawn(self._announce(ACTIVE))

    async def wake(self) -> None:
        self.last_activity = time.monotonic()
        if self.state == ACTIVE and self.display.is_on():
            return
        # display.on() can take seconds (wlopm retries), and a finger on the
        # panel calls activity() twice a second. Without this, a display
        # backend that keeps failing would pile up one retry ladder per tap.
        if self._waking:
            return
        self._waking = True
        try:
            self._set_state(ACTIVE)
            # Announce before powering on: clients must drop the scrim even if
            # the panel backend is slow or broken, otherwise a failing display
            # backend also blacks out the UI.
            await self._announce(ACTIVE)
            await self.display.on()
        finally:
            self._waking = False

    async def show_clock(self) -> None:
        self._set_state(CLOCK)
        await self._announce(CLOCK)
        await self.display.on()

    async def force_off(self) -> None:
        self._set_state(OFF)
        # Scrim first so the panel already shows black when power returns.
        await self._announce(OFF)
        await self.display.off()

    async def _announce(self, state: str) -> None:
        self._last_announce = time.monotonic()
        await self.broadcast("display.state", {"state": state})

    # -- loop ------------------------------------------------------------

    async def run(self) -> None:
        while True:
            try:
                await self._step()
            except Exception:
                log.exception("idle step failed")
            await asyncio.sleep(1)

    async def _step(self) -> None:
        # Night flip check runs before any early return, so the dim veil
        # engages even while a timer inhibits blanking.
        night = self._in_night_window()
        if night != self._night:
            self._night = night
            await self.broadcast("night.state", {"night": night})
        # A ringing alarm must be seen/heard: force the display awake.
        if self.engine.has_ringing() and (self.state != ACTIVE or not self.display.is_on()):
            await self.wake()
            return
        now = time.monotonic()
        if self.inhibitors():
            # Hold the idle clock while blanking is inhibited. Otherwise the
            # moment the inhibitor clears (a long timer finishes, keep_awake
            # goes off) idle_for is already hours old and the panel blanks
            # instantly — which reads as "it went to sleep for no reason".
            self.last_activity = now
            self._state_since = now
            return
        idle_for = now - self.last_activity
        in_state_for = now - self._state_since
        if self.state == ACTIVE:
            if night:
                if idle_for > self.store.get("night_idle_timeout_s"):
                    await self.force_off()
            elif idle_for > self.store.get("idle_timeout_s"):
                await self.show_clock()
        elif self.state == CLOCK:
            if idle_for < 1:
                # Activity raced the loop; wake handles the transition.
                return
            if night or in_state_for > self.store.get("clock_to_off_s"):
                await self.force_off()

    # -- internals -------------------------------------------------------

    def _set_state(self, state: str) -> None:
        if state != self.state:
            log.info("display state: %s -> %s", self.state, state)
        self.state = state
        self._state_since = time.monotonic()

    def _in_night_window(self) -> bool:
        if not self.store.get("night_mode_enabled"):
            return False
        try:
            start = _parse_hhmm(self.store.get("night_off_start"))
            end = _parse_hhmm(self.store.get("night_off_end"))
        except ValueError:
            return False
        now = datetime.datetime.now().time()
        if start <= end:
            return start <= now < end
        return now >= start or now < end  # window wraps past midnight

    def _on_settings_change(self, changed: dict) -> None:
        if changed.get("keep_awake") and self.state != ACTIVE:
            _spawn(self.wake())


def _parse_hhmm(value: str) -> datetime.time:
    hours, minutes = value.split(":")
    return datetime.time(int(hours), int(minutes))
