"""
In-memory record of a plain photo the bot has received and is waiting for
a watermark decision on (one per pid, kept in the `pending_photos` dict in
state.py). Extracted out of bot.py (phase C of the module split; see
CLAUDE.md's change log) with behavior unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass

from aiogram.types import Message


@dataclass
class PendingPhoto:
    user_id: int
    chat_id: int
    source_message: Message
