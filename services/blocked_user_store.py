from __future__ import annotations

import asyncio
import sqlite3
import time
from pathlib import Path

from config import Paths

DB_FILE = Paths.CONFIG / "blocked_users.db"


class BlockedUserStore:
    """Users the admin has explicitly blocked from using the bot at all —
    orthogonal to access_store (who's *authorized*) and pending_user_store
    (who's *shown interest but isn't registered*): a block always wins,
    regardless of whether the person is registered, paid, or just pending.
    See utils/access_control.py::is_authorized and CLAUDE.md's change log."""

    def __init__(self, db_path: Path):
        self._path = db_path
        self._lock = asyncio.Lock()

        self._path.parent.mkdir(parents=True, exist_ok=True)

        self._conn = sqlite3.connect(str(self._path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")

        self._create_schema()

    def _create_schema(self):
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS blocked_users (
                telegram_id INTEGER PRIMARY KEY,
                blocked_at  REAL NOT NULL,
                blocked_by  INTEGER,
                note        TEXT NOT NULL DEFAULT ''
            )
            """
        )
        self._conn.commit()

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict:
        return {
            "telegram_id": row["telegram_id"],
            "blocked_at": row["blocked_at"],
            "blocked_by": row["blocked_by"],
            "note": row["note"],
        }

    def is_blocked(self, telegram_id: int) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM blocked_users WHERE telegram_id = ?",
            (telegram_id,),
        ).fetchone()
        return row is not None

    def get(self, telegram_id: int) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM blocked_users WHERE telegram_id = ?",
            (telegram_id,),
        ).fetchone()
        return self._row_to_dict(row) if row is not None else None

    def list_all(self) -> dict[str, dict]:
        rows = self._conn.execute(
            "SELECT * FROM blocked_users ORDER BY blocked_at DESC"
        ).fetchall()
        return {str(row["telegram_id"]): self._row_to_dict(row) for row in rows}

    async def block(self, telegram_id: int, *, blocked_by: int | None = None, note: str = "") -> None:
        async with self._lock:
            self._conn.execute(
                """
                INSERT INTO blocked_users (telegram_id, blocked_at, blocked_by, note)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(telegram_id) DO UPDATE SET
                    blocked_at = excluded.blocked_at,
                    blocked_by = excluded.blocked_by,
                    note = excluded.note
                """,
                (telegram_id, time.time(), blocked_by, note),
            )
            self._conn.commit()

    async def unblock(self, telegram_id: int) -> bool:
        async with self._lock:
            cur = self._conn.execute(
                "DELETE FROM blocked_users WHERE telegram_id = ?",
                (telegram_id,),
            )
            self._conn.commit()
            return cur.rowcount > 0


blocked_user_store = BlockedUserStore(DB_FILE)
