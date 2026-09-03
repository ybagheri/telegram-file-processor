from __future__ import annotations

import asyncio
import sqlite3
import time
from pathlib import Path

from config import Paths

DB_FILE = Paths.CONFIG / "promo_post.db"


class PromoPostStore:
    """A single admin-configured "promotional post" — any message (text,
    photo, video, document, whatever) the admin sends the bot, referenced
    by (source_chat_id, source_message_id) so it can be relayed later via
    Telegram's own copy_message without re-uploading anything. When
    enabled, handlers/bridge.py's DONE handling copies it to a user right
    after their job completes — see CLAUDE.md's change log for the
    motivating request. There is deliberately only ever one row: this is
    a single "current" post, not a queue/history of past ones."""

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
            CREATE TABLE IF NOT EXISTS promo_post (
                id                INTEGER PRIMARY KEY CHECK (id = 1),
                enabled           INTEGER NOT NULL DEFAULT 0,
                source_chat_id    INTEGER,
                source_message_id INTEGER,
                set_by            INTEGER,
                set_at            REAL
            )
            """
        )
        self._conn.commit()

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict:
        return {
            "enabled": bool(row["enabled"]),
            "source_chat_id": row["source_chat_id"],
            "source_message_id": row["source_message_id"],
            "set_by": row["set_by"],
            "set_at": row["set_at"],
        }

    def get(self) -> dict | None:
        """None means no post has ever been configured. A configured post
        with enabled=False still returns its dict — callers that only
        care about "should this actually be sent right now" should check
        both `is not None` and `["enabled"]`."""
        row = self._conn.execute(
            "SELECT * FROM promo_post WHERE id = 1"
        ).fetchone()
        return self._row_to_dict(row) if row is not None else None

    async def set_post(self, chat_id: int, message_id: int, *, set_by: int | None = None) -> None:
        """Replacing the post also (re-)enables it — if an admin bothers
        setting one, the reasonable default is that they want it live;
        they can still turn it off explicitly afterward."""
        async with self._lock:
            self._conn.execute(
                """
                INSERT INTO promo_post (id, enabled, source_chat_id, source_message_id, set_by, set_at)
                VALUES (1, 1, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    enabled = 1,
                    source_chat_id = excluded.source_chat_id,
                    source_message_id = excluded.source_message_id,
                    set_by = excluded.set_by,
                    set_at = excluded.set_at
                """,
                (chat_id, message_id, set_by, time.time()),
            )
            self._conn.commit()

    async def set_enabled(self, enabled: bool) -> bool:
        """Returns False (no-op) if no post has ever been configured —
        there's nothing to enable/disable yet."""
        async with self._lock:
            cur = self._conn.execute(
                "UPDATE promo_post SET enabled = ? WHERE id = 1",
                (1 if enabled else 0,),
            )
            self._conn.commit()
            return cur.rowcount > 0

    async def clear(self) -> bool:
        async with self._lock:
            cur = self._conn.execute("DELETE FROM promo_post WHERE id = 1")
            self._conn.commit()
            return cur.rowcount > 0


promo_post_store = PromoPostStore(DB_FILE)
