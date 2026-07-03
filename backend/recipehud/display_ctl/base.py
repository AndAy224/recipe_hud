from typing import Protocol


class DisplayBackend(Protocol):
    name: str

    async def on(self) -> None: ...

    async def off(self) -> None: ...

    def is_on(self) -> bool: ...
