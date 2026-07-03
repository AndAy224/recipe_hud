import asyncio
import logging

log = logging.getLogger(__name__)


class X11Display:
    """Fallback for X11 sessions: DPMS via xset (needs DISPLAY env)."""

    name = "x11"

    def __init__(self):
        self._on = True

    async def on(self) -> None:
        if await self._run("xset", "dpms", "force", "on"):
            await self._run("xset", "s", "reset")
            self._on = True

    async def off(self) -> None:
        if await self._run("xset", "dpms", "force", "off"):
            self._on = False

    def is_on(self) -> bool:
        return self._on

    async def _run(self, *cmd: str) -> bool:
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.communicate()
            return proc.returncode == 0
        except FileNotFoundError:
            log.error("xset not installed")
            return False
