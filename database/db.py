"""database/db.py — Async SQLite helpers using aiosqlite."""
from __future__ import annotations

import os
import aiosqlite

_DB_PATH: str = os.getenv("DB_PATH", "/root/projects/HLCarryBot/database/carry.db")
_SCHEMA_PATH: str = os.path.join(os.path.dirname(__file__), "schema.sql")


def get_db() -> aiosqlite.Connection:
    """Return an async context manager for a SQLite connection.

    Usage:
        async with get_db() as conn:
            await conn.execute(...)
    """
    return aiosqlite.connect(_DB_PATH)


async def _configure(conn: aiosqlite.Connection) -> None:
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA journal_mode=WAL")
    await conn.execute("PRAGMA foreign_keys=ON")


async def init_db() -> None:
    """Apply schema.sql on first run (idempotent — all CREATE IF NOT EXISTS)."""
    schema = open(_SCHEMA_PATH).read()
    async with get_db() as conn:
        await _configure(conn)
        await conn.executescript(schema)
        await conn.commit()
