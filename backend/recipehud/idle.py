import asyncio
import datetime
import logging
import time

from .display_ctl.ddc import NullBacklight
from .screensaver import active_deck
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

    While a seasonal screensaver deck is in season, CLOCK *is* the
    screensaver and never times out to OFF in the daytime; the night window
    still blacks it out, and when the window ends an OFF panel comes back to
    the screensaver by itself. The backlight follows the state: dimmed at
    night, down to `brightness_off` behind the black OFF scrim.
    """

    def __init__(self, display, broadcast, store: SettingsStore, engine: TimerEngine,
                 backlight=None):
        self.display = display
        self.backlight = backlight or NullBacklight()
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
            "screensaver": self._saver_active(),
            "backlight": self.backlight.status(),
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
        # Cutting power is opt-in: on panels that feed the USB touch
        # controller, DPMS off takes the touchscreen down with it and nothing
        # can wake the kiosk again. The scrim is already full black, so the
        # only thing gained by the real cut is backlight power.
        if self.store.get("panel_power_off"):
            await self.display.off()

    async def _announce(self, state: str) -> None:
        self._last_announce = time.monotonic()
        await self.broadcast("display.state", {"state": state})

    # -- loop ------------------------------------------------------------

    async def run(self) -> None:
        while True:
            try:
                await self._step()
                # Every tick, not just on transitions: settings edits and the
                # night flip land here too. Cheap — the backlight worker only
                # writes when the target actually changes.
                self._sync_backlight()
            except Exception:
                log.exception("idle step failed")
            await asyncio.sleep(1)

    async def _step(self) -> None:
        # Night flip check runs before any early return, so the dim veil
        # engages even while a timer inhibits blanking.
        night = self._in_night_window()
        if night != self._night:
            # `_night` starts as None, so a backend (re)start is not a flip.
            night_ended = self._night is True and not night
            self._night = night
            await self.broadcast("night.state", {"night": night})
            if night_ended and self.state == OFF and self._saver_active():
                # Morning: the screensaver replaces the black screen in the
                # daytime, so don't leave the panel black until someone taps.
                log.info("night window ended; back to the screensaver")
                await self.show_clock()
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
            if night:
                await self.force_off()
            elif not self._saver_active() and in_state_for > self.store.get("clock_to_off_s"):
                await self.force_off()

    # -- internals -------------------------------------------------------

    def _set_state(self, state: str) -> None:
        if state != self.state:
            log.info("display state: %s -> %s", self.state, state)
        self.state = state
        self._state_since = time.monotonic()
        self._sync_backlight()

    def _saver_active(self) -> bool:
        return active_deck(self.store) is not None

    def _brightness_target(self) -> int:
        store = self.store
        active = store.get("brightness_active")
        if not store.get("backlight_control"):
            # Pinned at the day level, so switching control off from a dimmed
            # state can't strand the panel dark.
            return active
        if self.state == OFF:
            return store.get("brightness_off")
        if self._in_night_window():
            return round(active * store.get("brightness_night_pct") / 100)
        return active

    def _sync_backlight(self) -> None:
        # Non-blocking: wake() has already flipped state before this runs and
        # announces right after, so the scrim never waits on DDC.
        self.backlight.request(self._brightness_target())

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
