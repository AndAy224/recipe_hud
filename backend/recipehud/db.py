from pathlib import Path

import aiosqlite

SCHEMA_VERSION = 1
SCHEMA_PATH = Path(__file__).with_name("schema.sql")


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = await aiosqlite.connect(self.path)
        self.conn.row_factory = aiosqlite.Row
        await self.conn.execute("PRAGMA journal_mode=WAL")
        await self._migrate()

    async def _migrate(self) -> None:
        cur = await self.conn.execute("PRAGMA user_version")
        version = (await cur.fetchone())[0]
        if version < 1:
            await self.conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
            await self.conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            await self.conn.commit()
        # Future migrations: if version < 2: ... etc.

    async def close(self) -> None:
        if self.conn:
            await self.conn.close()
            self.conn = None

    async def fetchall(self, sql: str, params: tuple = ()) -> list[dict]:
        cur = await self.conn.execute(sql, params)
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def fetchone(self, sql: str, params: tuple = ()) -> dict | None:
        cur = await self.conn.execute(sql, params)
        row = await cur.fetchone()
        return dict(row) if row else None

    async def execute(self, sql: str, params: tuple = ()) -> int:
        """Execute and commit; returns lastrowid."""
        cur = await self.conn.execute(sql, params)
        await self.conn.commit()
        return cur.lastrowid

    async def executemany(self, sql: str, seq: list[tuple]) -> None:
        await self.conn.executemany(sql, seq)
        await self.conn.commit()
