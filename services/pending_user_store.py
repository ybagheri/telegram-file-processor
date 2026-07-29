from __future__ import annotations

import asyncio
import sqlite3
import time
from pathlib import Path

from config import Paths

DB_FILE = Paths.CONFIG / "pending_users.db"


class PendingUserStore:
    """Tracks people who have `/start`-ed the bot but aren't (yet) in
    `access_store` — i.e. they showed interest but never got registered.

    This is deliberately a separate table/store from `access_store`: it's
    not about who's *allowed* to use the bot, it's a lightweight CRM-ish
    log of "who knocked on the door", so the admin can be notified once
    per person and, later, broadcast to or report on this exact group
    (conversion tracking, etc — see CLAUDE.md's change log for the
    motivating request). A user is removed from here the moment they
    actually get added to `access_store`.
    """

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
            CREATE TABLE IF NOT EXISTS pending_users (
                telegram_id       INTEGER PRIMARY KEY,
                first_name        TEXT NOT NULL DEFAULT '',
                last_name         TEXT NOT NULL DEFAULT '',
                username          TEXT NOT NULL DEFAULT '',
                language_code     TEXT NOT NULL DEFAULT '',
                is_bot            INTEGER NOT NULL DEFAULT 0,
                first_seen_at     REAL NOT NULL,
                last_seen_at      REAL NOT NULL,
                start_count       INTEGER NOT NULL DEFAULT 1,
                admin_notified_at REAL
            )
            """
        )
        self._conn.commit()

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict:
        return {
            "telegram_id": row["telegram_id"],
            "first_name": row["first_name"],
            "last_name": row["last_name"],
            "username": row["username"],
            "language_code": row["language_code"],
            "is_bot": bool(row["is_bot"]),
            "first_seen_at": row["first_seen_at"],
            "last_seen_at": row["last_seen_at"],
            "start_count": row["start_count"],
            "admin_notified_at": row["admin_notified_at"],
        }

    def get(self, telegram_id: int) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM pending_users WHERE telegram_id = ?",
            (telegram_id,),
        ).fetchone()
        return self._row_to_dict(row) if row is not None else None

    def list_all(self) -> dict[str, dict]:
        rows = self._conn.execute(
            "SELECT * FROM pending_users ORDER BY first_seen_at"
        ).fetchall()
        return {str(row["telegram_id"]): self._row_to_dict(row) for row in rows}

    async def record_start(
        self,
        telegram_id: int,
        *,
        first_name: str = "",
        last_name: str = "",
        username: str = "",
        language_code: str = "",
        is_bot: bool = False,
    ) -> bool:
        """Record a `/start`. Returns True if this is a brand-new pending
        user (the caller should notify the admin), False if it's a repeat
        `/start` from someone already tracked (only `last_seen_at`/
        `start_count` are bumped — no repeat admin notification)."""
        async with self._lock:
            now = time.time()
            existing = self._conn.execute(
                "SELECT telegram_id FROM pending_users WHERE telegram_id = ?",
                (telegram_id,),
            ).fetchone()

            if existing is not None:
                self._conn.execute(
                    """
                    UPDATE pending_users
                    SET last_seen_at = ?, start_count = start_count + 1
                    WHERE telegram_id = ?
                    """,
                    (now, telegram_id),
                )
                self._conn.commit()
                return False

            self._conn.execute(
                """
                INSERT INTO pending_users
                    (telegram_id, first_name, last_name, username, language_code,
                     is_bot, first_seen_at, last_seen_at, start_count, admin_notified_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, NULL)
                """,
                (telegram_id, first_name, last_name, username, language_code,
                 1 if is_bot else 0, now, now),
            )
            self._conn.commit()
            return True

    async def mark_notified(self, telegram_id: int) -> None:
        async with self._lock:
            self._conn.execute(
                "UPDATE pending_users SET admin_notified_at = ? WHERE telegram_id = ?",
                (time.time(), telegram_id),
            )
            self._conn.commit()

    async def remove(self, telegram_id: int) -> bool:
        """Called once someone actually gets added to `access_store` — they're
        no longer just "pending", so they shouldn't linger in this table."""
        async with self._lock:
            cur = self._conn.execute(
                "DELETE FROM pending_users WHERE telegram_id = ?",
                (telegram_id,),
            )
            self._conn.commit()
            return cur.rowcount > 0


pending_user_store = PendingUserStore(DB_FILE)
