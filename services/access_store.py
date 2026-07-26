from __future__ import annotations

import asyncio
import json
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

    def is_authorized(self, user_id: int) -> bool:
        return str(user_id) in self._data

    def get(self, user_id: int) -> dict | None:
        return self._data.get(str(user_id))

    def list_all(self) -> dict[str, dict]:
        return dict(self._data)

    async def add(self, user_id: int, *, label: str = "", added_by: int | None = None):
        async with self._lock:
            self._data[str(user_id)] = {
                "label": label,
                "added_by": added_by,
            }
            self._write()

    async def remove(self, user_id: int) -> bool:
        async with self._lock:
            existed = self._data.pop(str(user_id), None) is not None
            if existed:
                self._write()
            return existed


access_store = AccessStore(ACCESS_FILE)
