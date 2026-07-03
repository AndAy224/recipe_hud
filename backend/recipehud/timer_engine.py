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


@dataclass
class Timer:
    id: str
    label: str
    duration_s: float
    state: str
    ends_at_mono: float | None = None   # monotonic deadline while running
    remaining_s: float = 0.0            # authoritative while paused
    ring_started_mono: float | None = None

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
            ends_at_mono=time.monotonic() + seconds,
        )
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
            await self._persist(timer)
            await self.broadcast("timer.updated", timer.to_dict())
        return timer.to_dict()

    async def resume(self, timer_id: str) -> dict:
        timer = self._get(timer_id)
        if timer.state == PAUSED:
            timer.ends_at_mono = time.monotonic() + timer.remaining_s
            timer.state = RUNNING
            await self._persist(timer)
            await self.broadcast("timer.updated", timer.to_dict())
            self._ensure_tick_task()
        return timer.to_dict()

    async def extend(self, timer_id: str, seconds: int) -> dict:
        timer = self._get(timer_id)
        if timer.state == RUNNING:
            timer.ends_at_mono += seconds
        elif timer.state == PAUSED:
            timer.remaining_s += seconds
        elif timer.state == RINGING:
            # +1 min on a ringing timer restarts it for that long.
            timer.state = RUNNING
            timer.ends_at_mono = time.monotonic() + seconds
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
                if remaining <= -600:
                    # Expired long ago while we were down; drop silently.
                    await self.db.execute(
                        "DELETE FROM timers_snapshot WHERE id = ?", (row["id"],))
                    continue
                if remaining <= 0:
                    timer = Timer(row["id"], row["label"], row["duration_s"], RINGING,
                                  ring_started_mono=time.monotonic())
                else:
                    timer = Timer(row["id"], row["label"], row["duration_s"], RUNNING,
                                  ends_at_mono=time.monotonic() + remaining)
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

    def _ensure_tick_task(self) -> None:
        if self._tick_task is None or self._tick_task.done():
            self._tick_task = asyncio.create_task(self._tick_loop())

    async def _tick_loop(self) -> None:
        while self._timers:
            now_mono = time.monotonic()
            for timer in list(self._timers.values()):
                if timer.state == RUNNING and timer.ends_at_mono <= now_mono:
                    timer.state = RINGING
                    timer.ring_started_mono = now_mono
                    await self._persist(timer)
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
            await asyncio.sleep(1)

    async def _persist(self, timer: Timer) -> None:
        ends_at = time.time() + timer.remaining() if timer.state == RUNNING else None
        await self.db.execute(
            "INSERT INTO timers_snapshot (id, label, duration_s, ends_at, remaining_s, state) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET label=excluded.label, duration_s=excluded.duration_s, "
            "ends_at=excluded.ends_at, remaining_s=excluded.remaining_s, state=excluded.state",
            (timer.id, timer.label, timer.duration_s, ends_at, timer.remaining(), timer.state),
        )
