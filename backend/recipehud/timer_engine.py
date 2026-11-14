import asyncio
import logging
import time
import uuid
from dataclasses import dataclass

from .db import Database
from .settings_store import SettingsStore

log = logging.getLogger(__name__)

RUNNING = "running"
PAUSED = "paused"
RINGING = "ringing"

# A restored timer that turns out to have expired this long ago is dropped
# rather than rung (nobody wants yesterday's pasta alarm at boot).
STALE_AFTER_S = 600
# The wall clock moving this much against the monotonic clock between two
# ticks is a step (NTP sync after boot, a manual change), not drift.
CLOCK_STEP_S = 2.0


@dataclass
class Timer:
    id: str
    label: str
    duration_s: float
    state: str
    ends_at_mono: float | None = None   # monotonic deadline while running
    remaining_s: float = 0.0            # authoritative while paused
    ring_started_mono: float | None = None
    ends_at_wall: float | None = None   # the same deadline as wall-clock time (snapshot)
    # True while the deadline was derived from the snapshot's wall-clock time
    # (see TimerEngine._on_clock_step); False once it is anchored to this
    # process's monotonic clock.
    from_snapshot: bool = False

    def remaining(self) -> float:
        if self.state == RUNNING:
            return max(0.0, self.ends_at_mono - time.monotonic())
        if self.state == PAUSED:
            return self.remaining_s
        return 0.0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "duration_s": self.duration_s,
            "state": self.state,
            "remaining_s": round(self.remaining(), 1),
        }


class TimerEngine:
    """Server-authoritative timers. Deadlines are monotonic; the snapshot
    table stores wall-clock ends_at only so timers survive a restart."""

    def __init__(self, db: Database, broadcast, store: SettingsStore):
        self.db = db
        self.broadcast = broadcast
        self.store = store
        self._timers: dict[str, Timer] = {}
        self._tick_task: asyncio.Task | None = None
        self._clock_offset: float | None = None  # wall minus monotonic, last tick

    # -- queries -------------------------------------------------------

    def list(self) -> list[dict]:
        return [t.to_dict() for t in self._timers.values()]

    def has_active(self) -> bool:
        return any(t.state in (RUNNING, RINGING) for t in self._timers.values())

    def has_ringing(self) -> bool:
        return any(t.state == RINGING for t in self._timers.values())

    # -- commands ------------------------------------------------------

    async def create(self, label: str, seconds: int) -> dict:
        timer = Timer(
            id=uuid.uuid4().hex[:12],
            label=label,
            duration_s=float(seconds),
            state=RUNNING,
        )
        self._arm(timer, seconds)
        self._timers[timer.id] = timer
        await self._persist(timer)
        await self.broadcast("timer.created", timer.to_dict())
        self._ensure_tick_task()
        return timer.to_dict()

    async def pause(self, timer_id: str) -> dict:
        timer = self._get(timer_id)
        if timer.state == RUNNING:
            timer.remaining_s = timer.remaining()
            timer.state = PAUSED
            timer.ends_at_mono = None
            timer.ends_at_wall = None
            await self._persist(timer)
            await self.broadcast("timer.updated", timer.to_dict())
        return timer.to_dict()

    async def resume(self, timer_id: str) -> dict:
        timer = self._get(timer_id)
        if timer.state == PAUSED:
            timer.state = RUNNING
            self._arm(timer, timer.remaining_s)
            await self._persist(timer)
            await self.broadcast("timer.updated", timer.to_dict())
            self._ensure_tick_task()
        return timer.to_dict()

    async def extend(self, timer_id: str, seconds: int) -> dict:
        timer = self._get(timer_id)
        if timer.state == RUNNING:
            self._arm(timer, timer.remaining() + seconds)
        elif timer.state == PAUSED:
            timer.remaining_s += seconds
        elif timer.state == RINGING:
            # +1 min on a ringing timer restarts it for that long.
            timer.state = RUNNING
            self._arm(timer, seconds)
            timer.ring_started_mono = None
            await self.broadcast("alarm.stop", {"id": timer.id})
        timer.duration_s += seconds
        await self._persist(timer)
        await self.broadcast("timer.updated", timer.to_dict())
        self._ensure_tick_task()
        return timer.to_dict()

    async def cancel(self, timer_id: str) -> None:
        timer = self._get(timer_id)
        was_ringing = timer.state == RINGING
        del self._timers[timer_id]
        await self.db.execute("DELETE FROM timers_snapshot WHERE id = ?", (timer_id,))
        if was_ringing:
            await self.broadcast("alarm.stop", {"id": timer_id})
        await self.broadcast("timer.cancelled", {"id": timer_id})

    async def dismiss(self, timer_id: str) -> None:
        timer = self._get(timer_id)
        if timer.state == RINGING:
            await self.cancel(timer_id)

    # -- lifecycle -----------------------------------------------------

    async def restore(self) -> None:
        """Rebuild timers from the snapshot after a backend restart."""
        now = time.time()
        rows = await self.db.fetchall("SELECT * FROM timers_snapshot")
        for row in rows:
            state = row["state"]
            if state == PAUSED:
                timer = Timer(row["id"], row["label"], row["duration_s"], PAUSED,
                              remaining_s=row["remaining_s"])
            else:
                remaining = (row["ends_at"] or now) - now
                if remaining <= -STALE_AFTER_S:
                    # Expired long ago while we were down; drop silently.
                    await self.db.execute(
                        "DELETE FROM timers_snapshot WHERE id = ?", (row["id"],))
                    continue
                if remaining <= 0:
                    timer = Timer(row["id"], row["label"], row["duration_s"], RINGING,
                                  ring_started_mono=time.monotonic())
                else:
                    timer = Timer(row["id"], row["label"], row["duration_s"], RUNNING,
                                  ends_at_mono=time.monotonic() + remaining,
                                  ends_at_wall=row["ends_at"], from_snapshot=True)
            self._timers[timer.id] = timer
        if self._timers:
            log.info("restored %d timer(s) from snapshot", len(self._timers))
            self._ensure_tick_task()

    async def shutdown(self) -> None:
        if self._tick_task:
            self._tick_task.cancel()

    # -- internals -----------------------------------------------------

    def _get(self, timer_id: str) -> Timer:
        if timer_id not in self._timers:
            raise KeyError(timer_id)
        return self._timers[timer_id]

    def _arm(self, timer: Timer, seconds: float) -> None:
        """Start (or restart) the countdown from now."""
        timer.ends_at_mono = time.monotonic() + seconds
        timer.ends_at_wall = time.time() + seconds
        timer.from_snapshot = False

    async def _on_clock_step(self) -> None:
        """The wall clock jumped. Which clock is right depends on the timer.

        A Pi has no battery clock: after a power cut it boots on a stale time
        and NTP corrects it a little later. A timer *restored from the
        snapshot* got its remaining time by subtracting that stale "now" from
        the saved wall-clock deadline, so it is too long by however far the
        clock was off — the saved deadline is the truth, and it is recomputed
        against the corrected clock. A timer started in this process counts
        real elapsed seconds on the monotonic clock, which never jumps; only
        its saved wall-clock deadline needs moving, so a later restart
        restores it correctly."""
        now_wall, now_mono = time.time(), time.monotonic()
        for timer in list(self._timers.values()):
            if timer.state != RUNNING or self._timers.get(timer.id) is not timer:
                continue
            if timer.from_snapshot and timer.ends_at_wall is not None:
                remaining = timer.ends_at_wall - now_wall
                if remaining <= -STALE_AFTER_S:
                    log.info("timer %s expired long ago (clock corrected); dropping", timer.id)
                    await self.cancel(timer.id)
                    continue
                timer.ends_at_mono = now_mono + remaining
                log.info("timer %s re-based on the corrected clock: %.0fs left",
                         timer.id, max(0.0, remaining))
            else:
                timer.ends_at_wall = now_wall + timer.remaining()
                await self._persist(timer)

    def _ensure_tick_task(self) -> None:
        if self._tick_task is None or self._tick_task.done():
            self._tick_task = asyncio.create_task(self._tick_loop())

    async def _tick_loop(self) -> None:
        while self._timers:
            # This loop is the only thing that ever makes a timer ring. An
            # exception escaping it would end the task without a trace and
            # leave every running timer counting down to nothing, so a failed
            # tick is logged and the next one simply tries again.
            try:
                await self._tick()
            except Exception:
                log.exception("timer tick failed")
            await asyncio.sleep(1)

    async def _tick(self) -> None:
        offset = time.time() - time.monotonic()
        if self._clock_offset is not None and abs(offset - self._clock_offset) > CLOCK_STEP_S:
            log.warning("wall clock stepped by %+.0fs", offset - self._clock_offset)
            self._clock_offset = offset
            await self._on_clock_step()
        self._clock_offset = offset
        now_mono = time.monotonic()
        for timer in list(self._timers.values()):
            if self._timers.get(timer.id) is not timer:
                # Cancelled over REST while an earlier timer in this pass was
                # awaiting. Acting on the stale object would ring (and
                # re-persist) a timer that no longer exists.
                continue
            if timer.state == RUNNING and timer.ends_at_mono <= now_mono:
                timer.state = RINGING
                timer.ring_started_mono = now_mono
                try:
                    await self._persist(timer)
                except Exception:
                    # A DB hiccup must not swallow the alarm itself.
                    log.exception("could not persist ringing timer %s", timer.id)
                await self.broadcast("alarm.start", {
                    "id": timer.id,
                    "label": timer.label,
                    "volume": self.store.get("alarm_volume"),
                })
            elif timer.state == RINGING:
                auto_dismiss = self.store.get("alarm_auto_dismiss_s")
                if now_mono - timer.ring_started_mono > auto_dismiss:
                    await self.dismiss(timer.id)
        if self._timers:
            await self.broadcast("timer.tick", self.list())

    async def _persist(self, timer: Timer) -> None:
        ends_at = timer.ends_at_wall if timer.state == RUNNING else None
        await self.db.execute(
            "INSERT INTO timers_snapshot (id, label, duration_s, ends_at, remaining_s, state) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET label=excluded.label, duration_s=excluded.duration_s, "
            "ends_at=excluded.ends_at, remaining_s=excluded.remaining_s, state=excluded.state",
            (timer.id, timer.label, timer.duration_s, ends_at, timer.remaining(), timer.state),
        )
