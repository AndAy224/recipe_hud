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

    async def _list_outputs(self) -> list[str]:
        """Live output names. `wlopm` with no args prints "NAME on|off"."""
        rc, out, _ = await self._exec()
        if rc != 0:
            return []
        return [line.split()[0] for line in out.splitlines() if line.split()]

    async def _resolve_output(self) -> str | None:
        """Pick the output to drive.

        `wlopm` exits 0 even when the named output does not exist (it just
        prints an error), so a stale/wrong `display_output` would silently
        no-op forever while is_on() reported success. Check the name against
        the live list instead of trusting it, and fall back to the only
        connected output rather than leaving the panel uncontrollable.
        """
        names = await self._list_outputs()
        configured = str(self.store.get("display_output") or "").strip()
        if not names:
            return configured or None  # compositor not up yet; let the retry loop handle it
        if configured in names:
            return configured
        if len(names) == 1:
            if configured:
                log.warning("display_output %r is not connected; driving %s instead",
                            configured, names[0])
            return names[0]
        log.error("display_output %r is not among the connected outputs %s",
                  configured, names)
        return None

    async def _exec(self, *args: str) -> tuple[int, str, str]:
        proc = await asyncio.create_subprocess_exec(
            "wlopm", *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        return proc.returncode, stdout.decode(errors="replace"), stderr.decode(errors="replace")

    async def _run(self, flag: str) -> bool:
        for attempt in range(3):
            try:
                output = await self._resolve_output()
                if output:
                    rc, _, stderr = await self._exec(flag, output)
                    # rc is 0 even on "Output 'X' does not exist", so the
                    # stderr text is the only real signal of failure.
                    if rc == 0 and not stderr.strip():
                        return True
                    log.warning("wlopm %s %s failed (rc=%s): %s",
                                flag, output, rc, stderr.strip())
            except FileNotFoundError:
                log.error("wlopm not installed")
                return False
            except Exception as exc:
                log.warning("wlopm error: %s", exc)
            await asyncio.sleep(2 * (attempt + 1))
        return False
