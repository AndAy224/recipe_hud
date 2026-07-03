import logging

log = logging.getLogger(__name__)


class MockDisplay:
    """Dev backend (Windows / no compositor): tracks state in memory."""

    name = "mock"

    def __init__(self):
        self._on = True

    async def on(self) -> None:
        if not self._on:
            log.info("[mock display] power ON")
        self._on = True

    async def off(self) -> None:
        if self._on:
            log.info("[mock display] power OFF")
        self._on = False

    def is_on(self) -> bool:
        return self._on
