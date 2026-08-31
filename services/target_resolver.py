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

ADMIN_STATUSES = {"administrator", "creator"}


async def resolve_target(message: Message):
    """Returns (chat_id, label, error). Exactly one of (chat_id, error)
    is set:

    - chat_id/label set, error=None: the chat was found AND
      `message.from_user` is verified to be an admin/owner there.
    - chat_id=None, error="not_found": couldn't identify the chat at
      all — unrecognized forward, unknown @username/id, or the bot
      itself isn't a member there.
    - chat_id=None, error="not_admin": the chat was identified, but
      `message.from_user` has no admin rights in it.

    That last check is a security boundary, not a UX nicety: being able
    to name or forward-from a chat only proves the *bot* is a member
    there (typically added by that chat's actual admin, for their own
    use) — it says nothing about the person sending *this* message.
    Without it, any user who merely learns another chat's id or
    @username (chat ids leak easily — a forwarded message, a shared
    invite link, simple guessing of small/sequential ids) could quietly
    redirect their own uploads into a group or channel they have no
    rights in, as long as the bot happened to already be there. See
    CLAUDE.md's change log."""

    chat = None

    if message.forward_from_chat:
        chat = message.forward_from_chat

    elif message.text:
        text = message.text.strip()

        try:
            chat = await bot.get_chat(text)
        except Exception:
            # Fallback: a raw numeric chat id. Useful for private
            # channels/groups with no public @username — especially ones
            # with "protect content" enabled, where forwarding a message
            # doesn't reveal its source chat at all.
            try:
                chat = await bot.get_chat(int(text))
            except Exception:
                chat = None

    if chat is None:
        return None, None, "not_found"

    try:
        member = await bot.get_chat_member(chat.id, message.from_user.id)
    except Exception:
        # Couldn't verify membership at all — fail closed, not open.
        return None, None, "not_admin"

    if member.status not in ADMIN_STATUSES:
        return None, None, "not_admin"

    return chat.id, (chat.title or chat.username or str(chat.id)), None
