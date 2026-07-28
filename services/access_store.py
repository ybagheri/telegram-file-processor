from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from pathlib import Path

from config import Paths

DB_FILE = Paths.CONFIG / "access.db"

# The very first version of this store was a hand-written JSON file. It's
# replaced by SQLite (see CLAUDE.md), but on first run against an old
# deployment we migrate whatever is in that file into the new database
# instead of silently losing every authorized user.
LEGACY_JSON_FILE = Paths.CONFIG / "authorized_users.json"


class AccessStore:
    """Who's allowed to use the bot, for how long, and whether they're
    currently enabled.

    Backed by SQLite instead of a hand-written JSON file: SQLite commits are
    atomic, so a crash or full disk mid-write can't leave the whole user
    list corrupted the way a partial `path.write_text(json.dumps(...))`
    could. A single `asyncio.Lock` still serializes writes so two admin
    actions in the same process never race each other.
    """

    def __init__(self, db_path: Path, legacy_json_path: Path | None = None):
        self._path = db_path
        self._lock = asyncio.Lock()

        self._path.parent.mkdir(parents=True, exist_ok=True)

        self._conn = sqlite3.connect(str(self._path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")

        self._create_schema()

        if legacy_json_path is not None:
            self._migrate_legacy_json(legacy_json_path)

    # ------------------------------------------------------------
    # Setup / migration
    # ------------------------------------------------------------

    def _create_schema(self):
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS authorized_users (
                user_id     INTEGER PRIMARY KEY,
                label       TEXT NOT NULL DEFAULT '',
                name        TEXT NOT NULL DEFAULT '',
                username    TEXT NOT NULL DEFAULT '',
                added_by    INTEGER,
                added_at    REAL NOT NULL,
                expires_at  REAL,
                active      INTEGER NOT NULL DEFAULT 1
            )
            """
        )
        self._conn.commit()
        self._migrate_schema()

    def _migrate_schema(self):
        """Add columns introduced after the table already existed on some
        deployments. SQLite has no `ADD COLUMN IF NOT EXISTS`, so check
        `PRAGMA table_info` by hand before altering."""
        existing_columns = {
            row["name"]
            for row in self._conn.execute("PRAGMA table_info(authorized_users)")
        }
        if "last_reminder_expiry" not in existing_columns:
            self._conn.execute(
                "ALTER TABLE authorized_users ADD COLUMN last_reminder_expiry REAL"
            )
            self._conn.commit()

    def _migrate_legacy_json(self, legacy_path: Path):
        """One-time import from the old JSON-file store. Only runs if the
        table is currently empty, so it can never clobber real SQLite data
        with a stale JSON snapshot (e.g. if the old file is left behind by
        mistake after a previous migration)."""

        if not legacy_path.exists():
            return

        existing_count = self._conn.execute(
            "SELECT COUNT(*) FROM authorized_users"
        ).fetchone()[0]

        if existing_count > 0:
            return

        try:
            legacy_data = json.loads(legacy_path.read_text(encoding="utf-8"))
        except Exception:
            return

        imported = 0

        for uid_str, info in legacy_data.items():
            try:
                user_id = int(uid_str)
            except (TypeError, ValueError):
                continue

            self._conn.execute(
                """
                INSERT OR REPLACE INTO authorized_users
                    (user_id, label, name, username, added_by, added_at, expires_at, active)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    info.get("label", "") or "",
                    info.get("name", "") or "",
                    info.get("username", "") or "",
                    info.get("added_by"),
                    info.get("added_at", time.time()),
                    info.get("expires_at"),
                    1 if info.get("active", True) else 0,
                ),
            )
            imported += 1

        self._conn.commit()

        if imported:
            # Keep the original file as a backup, but renamed so nobody
            # mistakes it for the live source of truth going forward.
            try:
                legacy_path.rename(legacy_path.with_suffix(".json.migrated"))
            except OSError:
                pass

    # ------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict:
        return {
            "label": row["label"],
            "name": row["name"],
            "username": row["username"],
            "added_by": row["added_by"],
            "added_at": row["added_at"],
            "expires_at": row["expires_at"],
            "active": bool(row["active"]),
            "last_reminder_expiry": row["last_reminder_expiry"],
        }

    @staticmethod
    def is_expired(info: dict) -> bool:
        expires_at = info.get("expires_at")
        # None means "no expiry" (unlimited access).
        if expires_at is None:
            return False
        return time.time() >= expires_at

    def is_authorized(self, user_id: int) -> bool:
        info = self.get(user_id)
        if info is None:
            return False
        if not info.get("active", True):
            return False
        if self.is_expired(info):
            return False
        return True

    def get(self, user_id: int) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM authorized_users WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_dict(row)

    def list_all(self) -> dict[str, dict]:
        rows = self._conn.execute(
            "SELECT * FROM authorized_users ORDER BY added_at"
        ).fetchall()
        return {str(row["user_id"]): self._row_to_dict(row) for row in rows}

    # ------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------

    async def add(
        self,
        user_id: int,
        *,
        label: str = "",
        added_by: int | None = None,
        expires_at: float | None = None,
        name: str = "",
        username: str = "",
    ):
        """Add a user, or fully replace an existing entry (re-activating
        them in the process — adding someone back is an explicit choice
        that should un-disable them too).

        `expires_at` is a Unix timestamp; `None` means no expiry (unlimited
        access). `name`/`username` are stored separately from `label` so a
        manually-added user (identified only by numeric id) can still have
        a human-friendly name/username attached by the admin by hand, even
        though Telegram never disclosed them.
        """
        async with self._lock:
            existing = self._conn.execute(
                "SELECT added_at FROM authorized_users WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            added_at = existing["added_at"] if existing is not None else time.time()

            self._conn.execute(
                """
                INSERT INTO authorized_users
                    (user_id, label, name, username, added_by, added_at, expires_at, active, last_reminder_expiry)
                VALUES (?, ?, ?, ?, ?, ?, ?, 1, NULL)
                ON CONFLICT(user_id) DO UPDATE SET
                    label                 = excluded.label,
                    name                  = excluded.name,
                    username              = excluded.username,
                    added_by              = excluded.added_by,
                    expires_at            = excluded.expires_at,
                    active                = 1,
                    last_reminder_expiry  = NULL
                """,
                (user_id, label, name, username, added_by, added_at, expires_at),
            )
            self._conn.commit()

    async def update_expiry(self, user_id: int, expires_at: float | None) -> bool:
        """Update only the expiry date of an existing user. Returns False
        if the user isn't in the store at all. Also clears the "already
        reminded" marker — a fresh expiry deserves its own reminder cycle,
        not silence because we warned about the *previous* one."""
        async with self._lock:
            cur = self._conn.execute(
                "UPDATE authorized_users SET expires_at = ?, last_reminder_expiry = NULL WHERE user_id = ?",
                (expires_at, user_id),
            )
            self._conn.commit()
            return cur.rowcount > 0

    async def set_active(self, user_id: int, active: bool) -> bool:
        """Enable/disable a user without deleting their record (so their
        expiry date and history survive). Returns False if the user isn't
        in the store at all."""
        async with self._lock:
            cur = self._conn.execute(
                "UPDATE authorized_users SET active = ? WHERE user_id = ?",
                (1 if active else 0, user_id),
            )
            self._conn.commit()
            return cur.rowcount > 0

    async def remove(self, user_id: int) -> bool:
        """Permanently delete a user's record (as opposed to `set_active`,
        which just disables them). Returns False if the user wasn't in the
        store at all."""
        async with self._lock:
            cur = self._conn.execute(
                "DELETE FROM authorized_users WHERE user_id = ?",
                (user_id,),
            )
            self._conn.commit()
            return cur.rowcount > 0

    # ------------------------------------------------------------
    # Expiry reminders
    # ------------------------------------------------------------

    def list_expiring_soon(self, within_seconds: float) -> list[dict]:
        """Active users whose access expires within `within_seconds` from
        now (and hasn't already expired), who haven't already been
        reminded about *this specific* expiry date. Each dict includes
        `user_id` alongside the usual fields."""
        now = time.time()
        rows = self._conn.execute(
            """
            SELECT * FROM authorized_users
            WHERE active = 1
              AND expires_at IS NOT NULL
              AND expires_at > ?
              AND expires_at <= ?
              AND (last_reminder_expiry IS NULL OR last_reminder_expiry != expires_at)
            """,
            (now, now + within_seconds),
        ).fetchall()

        results = []
        for row in rows:
            info = self._row_to_dict(row)
            info["user_id"] = row["user_id"]
            results.append(info)
        return results

    async def mark_reminded(self, user_id: int, expires_at: float) -> None:
        """Record that we've already sent a reminder for this user's
        *current* expiry, so `list_expiring_soon` won't nag them again
        until it changes (renewal resets this via `update_expiry`/`add`)."""
        async with self._lock:
            self._conn.execute(
                "UPDATE authorized_users SET last_reminder_expiry = ? WHERE user_id = ?",
                (expires_at, user_id),
            )
            self._conn.commit()


access_store = AccessStore(DB_FILE, LEGACY_JSON_FILE)
