"""SQLite-backed minimal store for sessions/steps/audit."""
from __future__ import annotations

from pathlib import Path


class SqliteStore:
    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path

    async def init_schema(self) -> None:
        raise NotImplementedError("M1: create tables sessions/steps/audit")
