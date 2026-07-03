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
        if self.state != ACTIVE:
            log.info("wake on activity (%s)", source)
            asyncio.get_event_loop().create_task(self.wake())

    async def wake(self) -> None:
        self.last_activity = time.monotonic()
        if self.state == ACTIVE and self.display.is_on():
            return
        self._set_state(ACTIVE)
        await self.display.on()
        await self.broadcast("display.state", {"state": ACTIVE})

    async def show_clock(self) -> None:
        self._set_state(CLOCK)
        await self.display.on()
        await self.broadcast("display.state", {"state": CLOCK})

    async def force_off(self) -> None:
        self._set_state(OFF)
        # Scrim first so the panel already shows black when power returns.
        await self.broadcast("display.state", {"state": OFF})
        await self.display.off()

    # -- loop ------------------------------------------------------------

    async def run(self) -> None:
        while True:
            try:
                await self._step()
            except Exception:
                log.exception("idle step failed")
            await asyncio.sleep(1)

    async def _step(self) -> None:
        # A ringing alarm must be seen/heard: force the display awake.
        if self.engine.has_ringing() and (self.state != ACTIVE or not self.display.is_on()):
            await self.wake()
            return
        if self.inhibitors():
            return
        now = time.monotonic()
        idle_for = now - self.last_activity
        in_state_for = now - self._state_since
        night = self._in_night_window()
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
            asyncio.get_event_loop().create_task(self.wake())


def _parse_hhmm(value: str) -> datetime.time:
    hours, minutes = value.split(":")
    return datetime.time(int(hours), int(minutes))
