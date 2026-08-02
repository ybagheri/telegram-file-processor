"""
Resolves a "target chat" (where processed files, or a user's default
delivery target, should go) from an admin/user's message: a forwarded
message from that chat, an @username, or a raw numeric chat id. Extracted
out of bot.py (used by both handlers/settings.py's "settings_target" state
and bot.py's own per-file target-picking flow) with behavior unchanged.
"""
from aiogram.types import Message

from services.telegram import telegram_service

bot = telegram_service.bot


async def resolve_target(message: Message):
    if message.forward_from_chat:
        chat = message.forward_from_chat
        return chat.id, (chat.title or chat.username or str(chat.id))

    if message.text:
        text = message.text.strip()

        try:
            chat = await bot.get_chat(text)
            return chat.id, (chat.title or chat.username or str(chat.id))
        except Exception:
            pass

        # Fallback: a raw numeric chat id. Useful for private
        # channels/groups with no public @username — especially ones with
        # "protect content" enabled, where forwarding a message doesn't
        # reveal its source chat at all.
        try:
            chat_id = int(text)
            chat = await bot.get_chat(chat_id)
            return chat.id, (chat.title or chat.username or str(chat.id))
        except Exception:
            pass

    return None, None
