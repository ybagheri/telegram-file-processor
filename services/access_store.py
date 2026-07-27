from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from config import Paths

ACCESS_FILE = Paths.CONFIG / "authorized_users.json"


class AccessStore:

    def __init__(self, path: Path):
        self._path = path
        self._lock = asyncio.Lock()
        self._data: dict[str, dict] = self._load()

    def _load(self) -> dict:
        if self._path.exists():
            try:
                return json.loads(self._path.read_text(encoding="utf-8"))
            except Exception:
                return {}
        return {}

    def _write(self):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @staticmethod
    def is_expired(info: dict) -> bool:
        expires_at = info.get("expires_at")
        # None means "no expiry" (unlimited access).
        if expires_at is None:
            return False
        return time.time() >= expires_at

    def is_authorized(self, user_id: int) -> bool:
        info = self._data.get(str(user_id))
        if info is None:
            return False
        if not info.get("active", True):
            return False
        if self.is_expired(info):
            return False
        return True

    def get(self, user_id: int) -> dict | None:
        return self._data.get(str(user_id))

    def list_all(self) -> dict[str, dict]:
        return dict(self._data)

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
        """Add or fully replace a user's entry.

        `expires_at` is a Unix timestamp; `None` means no expiry (unlimited
        access). `name`/`username` are stored separately from `label` so
        manually-added users (identified only by numeric id) can still have
        a human-friendly name/username attached by the admin, even though
        Telegram never disclosed them.
        """
        async with self._lock:
            existing = self._data.get(str(user_id), {})
            self._data[str(user_id)] = {
                "label": label,
                "name": name,
                "username": username,
                "added_by": added_by,
                "expires_at": expires_at,
                "active": True,
                # Preserve original add timestamp across renewals/re-adds.
                "added_at": existing.get("added_at", time.time()),
            }
            self._write()

    async def update_expiry(self, user_id: int, expires_at: float | None) -> bool:
        """Update only the expiry date of an existing user. Returns False
        if the user isn't in the store at all."""
        async with self._lock:
            info = self._data.get(str(user_id))
            if info is None:
                return False
            info["expires_at"] = expires_at
            self._write()
            return True

    async def set_active(self, user_id: int, active: bool) -> bool:
        """Enable/disable a user without removing their record (so their
        expiry date and history are preserved). Returns False if the user
        isn't in the store at all."""
        async with self._lock:
            info = self._data.get(str(user_id))
            if info is None:
                return False
            info["active"] = active
            self._write()
            return True

    async def remove(self, user_id: int) -> bool:
        async with self._lock:
            existed = self._data.pop(str(user_id), None) is not None
            if existed:
                self._write()
            return existed


access_store = AccessStore(ACCESS_FILE)
