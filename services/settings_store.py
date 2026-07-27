from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path

from config import Paths

DB_FILE = Paths.CONFIG / "settings.db"

# The original version of this store was a hand-written JSON file. It's
# replaced by SQLite (see CLAUDE.md), but on first run against an old
# deployment we migrate whatever is in that file into the new database
# instead of silently losing every user's preferences.
LEGACY_JSON_FILE = Paths.CONFIG / "user_settings.json"

DEFAULTS = {
    "quality": "360",
    "watermark": True,
    "upload_as": "video",      # "document" | "video" (video = proper player + thumbnail)
    "target_chat_id": 0,       # 0 => deliver back to the user
    "target_label": "خودم",
    "artist": "",
    "logo_path": "",
    "logo_position": "bottom_right",
    "media_caption": "",       # empty => no caption on delivered media
    "sort_mode": "name",       # "name" | "date" — order of files inside an archive
    "sort_order": "asc",       # "asc" | "desc"
    "exclude_text": "",        # substring stripped from every filename/title
}

# Columns that hold a boolean in Python but are stored as 0/1 in SQLite.
_BOOL_COLUMNS = {"watermark"}


class SettingsStore:
    """Per-user preferences (quality, watermark, upload mode, delivery
    target, etc). Backed by SQLite instead of a hand-written JSON file for
    the same reason as `access_store`: atomic commits instead of a
    from-scratch rewrite of one big file on every single change.
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
            CREATE TABLE IF NOT EXISTS user_settings (
                user_id        INTEGER PRIMARY KEY,
                quality        TEXT NOT NULL DEFAULT '360',
                watermark      INTEGER NOT NULL DEFAULT 1,
                upload_as      TEXT NOT NULL DEFAULT 'video',
                target_chat_id INTEGER NOT NULL DEFAULT 0,
                target_label   TEXT NOT NULL DEFAULT 'خودم',
                artist         TEXT NOT NULL DEFAULT '',
                logo_path      TEXT NOT NULL DEFAULT '',
                logo_position  TEXT NOT NULL DEFAULT 'bottom_right',
                media_caption  TEXT NOT NULL DEFAULT '',
                sort_mode      TEXT NOT NULL DEFAULT 'name',
                sort_order     TEXT NOT NULL DEFAULT 'asc',
                exclude_text   TEXT NOT NULL DEFAULT ''
            )
            """
        )
        self._conn.commit()

    def _migrate_legacy_json(self, legacy_path: Path):
        """One-time import from the old JSON-file store. Only runs if the
        table is currently empty, so it can never clobber real SQLite data
        with a stale JSON snapshot."""

        if not legacy_path.exists():
            return

        existing_count = self._conn.execute(
            "SELECT COUNT(*) FROM user_settings"
        ).fetchone()[0]

        if existing_count > 0:
            return

        try:
            legacy_data = json.loads(legacy_path.read_text(encoding="utf-8"))
        except Exception:
            return

        imported = 0
        columns = list(DEFAULTS.keys())

        for uid_str, stored in legacy_data.items():
            try:
                user_id = int(uid_str)
            except (TypeError, ValueError):
                continue

            merged = DEFAULTS.copy()
            merged.update(stored or {})

            values = [
                (1 if merged[col] else 0) if col in _BOOL_COLUMNS else merged[col]
                for col in columns
            ]

            placeholders = ", ".join("?" for _ in columns)
            self._conn.execute(
                f"""
                INSERT OR REPLACE INTO user_settings (user_id, {", ".join(columns)})
                VALUES (?, {placeholders})
                """,
                (user_id, *values),
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

    def get(self, user_id: int) -> dict:
        row = self._conn.execute(
            "SELECT * FROM user_settings WHERE user_id = ?",
            (user_id,),
        ).fetchone()

        if row is None:
            return DEFAULTS.copy()

        result = {key: row[key] for key in DEFAULTS.keys()}
        for col in _BOOL_COLUMNS:
            result[col] = bool(result[col])
        return result

    # ------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------

    async def update(self, user_id: int, **changes):
        if not changes:
            return

        unknown = set(changes) - set(DEFAULTS.keys())
        if unknown:
            raise ValueError(f"Unknown setting(s): {sorted(unknown)}")

        async with self._lock:
            # Make sure a row exists (with defaults) before updating just
            # the given fields.
            self._conn.execute(
                "INSERT OR IGNORE INTO user_settings (user_id) VALUES (?)",
                (user_id,),
            )

            set_clause = ", ".join(f"{key} = ?" for key in changes)
            values = [
                (1 if value else 0) if key in _BOOL_COLUMNS else value
                for key, value in changes.items()
            ]

            self._conn.execute(
                f"UPDATE user_settings SET {set_clause} WHERE user_id = ?",
                (*values, user_id),
            )
            self._conn.commit()


settings_store = SettingsStore(DB_FILE, LEGACY_JSON_FILE)
