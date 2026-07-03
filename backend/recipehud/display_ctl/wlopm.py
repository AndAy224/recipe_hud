import asyncio
import logging

from ..settings_store import SettingsStore

log = logging.getLogger(__name__)


class WlopmDisplay:
    """Wayland (labwc/wlroots) output power via wlopm.

    Needs WAYLAND_DISPLAY and XDG_RUNTIME_DIR in the service environment.
    Retries because the compositor may not be up yet right after boot.
    """

    name = "wlopm"

    def __init__(self, store: SettingsStore):
        self.store = store
        self._on = True

    async def on(self) -> None:
        if await self._run("--on"):
            self._on = True

    async def off(self) -> None:
        if await self._run("--off"):
            self._on = False

    def is_on(self) -> bool:
        return self._on

    async def _run(self, flag: str) -> bool:
        output = self.store.get("display_output")
        for attempt in range(3):
            try:
                proc = await asyncio.create_subprocess_exec(
                    "wlopm", flag, output,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.PIPE,
                )
                _, stderr = await proc.communicate()
                if proc.returncode == 0:
                    return True
                log.warning("wlopm %s %s failed (rc=%s): %s",
                            flag, output, proc.returncode, stderr.decode().strip())
            except FileNotFoundError:
                log.error("wlopm not installed")
                return False
            except Exception as exc:
                log.warning("wlopm error: %s", exc)
            await asyncio.sleep(2 * (attempt + 1))
        return False
