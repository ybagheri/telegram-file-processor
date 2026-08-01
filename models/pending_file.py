"""
In-memory record of a file the bot has received but hasn't finished
processing/delivering yet (one per pid, kept in the `pending_files` dict
in state.py). Extracted out of bot.py (phase C of the module split; see
CLAUDE.md's change log) with behavior unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from aiogram.types import Message


@dataclass
class PendingFile:
    user_id: int
    chat_id: int
    file_name: str
    file_type: str
    source_message: Message
    options: dict = field(default_factory=dict)
    is_multipart: bool = False
    parts_total: int = 0
    part_message_ids: list = field(default_factory=list)
