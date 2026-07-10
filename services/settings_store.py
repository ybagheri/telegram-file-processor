from __future__ import annotations

import asyncio
import json
from pathlib import Path

from config import Paths

SETTINGS_FILE = Paths.CONFIG / "user_settings.json"

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
}


class SettingsStore:

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

    def get(self, user_id: int) -> dict:
        merged = DEFAULTS.copy()
        merged.update(self._data.get(str(user_id), {}))
        return merged

    async def update(self, user_id: int, **changes):
        async with self._lock:
            key = str(user_id)
            current = self._data.setdefault(key, {})
            current.update(changes)
            self._write()


settings_store = SettingsStore(SETTINGS_FILE)
