"""
The bot's core entry points: `/start`, `/cancel`, and the catch-all for
every other private message (new files, plain text, photos, password
replies). Extracted out of bot.py (phase D, step 4 — the last handler
module — of the module split; see CLAUDE.md's change log).

Two separate Routers on purpose: `router` holds the specific-`Command`
handlers (`/start`, `/cancel`); `catchall_router` holds
`handle_private_message`'s bare `F.chat.type == "private"` filter, which
matches ANY private message with no further discrimination. Both must be
registered via `dp.include_router(...)` in `bot.py`, with `router` (and
every other specific-filter router — admin/settings/files/photo) *before*
`catchall_router` — see the phase-7d change-log entry for the real bug
that taught us this the hard way: aiogram checks a router's own handlers
before descending into any sub-router, so a broad catch-all decorated
directly on `dp` (or included before a more specific router) will win
every time, regardless of when `dp.include_router(...)` was called.
"""
from uuid import uuid4

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message

from core.protocol import Protocol
from handlers.admin import handle_admin_awaited_input
from handlers.files import handle_file_awaited_input
from handlers.photo import handle_incoming_photo
from handlers.settings import handle_settings_awaited_input
from keyboards.files import options_keyboard, quality_keyboard
from models.pending_file import PendingFile
from services.settings_store import settings_store
from services.telegram import telegram_service
from state import admin_flow, awaiting_state, pending_files, pending_passwords
from utils.access_control import is_authorized, not_authorized_text, track_pending_user_if_needed
from utils.filetype import FileTypeDetector

router = Router(name="core")
catchall_router = Router(name="catchall")


async def handle_awaited_input(message: Message, state: str) -> bool:
    """Composes the per-domain awaited-input dispatchers by state prefix.
    Each domain (admin/settings/files) owns its own states entirely; this
    is just the routing table between them, kept here (rather than in
    bot.py) so this module doesn't need to import bot.py back — bot.py
    imports THIS function instead, for backward compatibility with
    existing direct-call tests."""

    if state.startswith("admin_"):
        return await handle_admin_awaited_input(message, state)

    if state.startswith("settings_"):
        return await handle_settings_awaited_input(message, state)

    if state.startswith("file:"):
        return await handle_file_awaited_input(message, state)

    return False


# ======================================================================
# Commands
# ======================================================================

@router.message(Command("start"), F.chat.type == "private")
async def start(message: Message):
    user_id = message.from_user.id

    # Track anyone interacting with the bot without being in access_store
    # yet, and notify the admin (once per person) — see
    # track_pending_user_if_needed's docstring for why this same call also
    # has to happen in handle_private_message and settings_command, not
    # just here.
    await track_pending_user_if_needed(message)

    if not is_authorized(user_id):
        await message.answer(not_authorized_text(user_id))
        return

    await message.answer(
        "سلام! فایل خود را ارسال کنید.\n"
        "برای تنظیم مقادیر پیش‌فرض از /settings استفاده کنید."
    )


@router.message(Command("cancel"), F.chat.type == "private")
async def cancel_command(message: Message):
    user_id = message.from_user.id

    had_state = awaiting_state.pop(user_id, None) is not None

    stale_pids = [pid for pid, p in pending_files.items() if p.user_id == user_id]
    for pid in stale_pids:
        pending_files.pop(pid, None)

    pending_passwords.pop(user_id, None)
    admin_flow.pop(user_id, None)

    if had_state or stale_pids:
        await message.answer("✅ لغو شد. می‌توانید یک فایل جدید بفرستید.")
    else:
        await message.answer("چیزی برای لغو کردن نبود.")


# ======================================================================
# Everything else in a private chat
# ======================================================================

@catchall_router.message(F.chat.type == "private")
async def handle_private_message(message: Message):
    user_id = message.from_user.id

    # ------------------------------------------------------------
    # Are we waiting for some input from this user right now?
    # ------------------------------------------------------------

    state = awaiting_state.get(user_id)

    if state:
        handled = await handle_awaited_input(message, state)
        if handled:
            return

    # Same tracking/notification as /start and /settings — a user can just
    # as easily send a file directly as their very first interaction
    # (this is exactly what happens when testing with qa-userbot, which
    # sends files straight away without ever sending /start first).
    await track_pending_user_if_needed(message)

    if not is_authorized(user_id):
        await message.answer(not_authorized_text(user_id))
        return

    # ------------------------------------------------------------
    # Password reply for a pending encrypted archive
    # ------------------------------------------------------------

    if user_id in pending_passwords and message.text:

        job_id = pending_passwords.pop(user_id)

        await telegram_service.send_password_response(
            Protocol.create_password_response(
                user_id=user_id,
                job_id=job_id,
                password=message.text,
            )
        )

        await message.answer("رمز ارسال شد، پردازش ادامه پیدا می‌کند.")
        return

    # ------------------------------------------------------------
    # New file
    # ------------------------------------------------------------

    file = message.document or message.video or message.audio

    if not file:

        if message.photo:
            await handle_incoming_photo(message)
            return

        return

    file_name = getattr(file, "file_name", None) or f"file_{message.message_id}"
    mime_type = getattr(file, "mime_type", "") or ""

    file_type = FileTypeDetector.detect(mime_type, file_name)

    if file_type == "UNKNOWN":
        await message.answer("نوع فایل پشتیبانی نمی‌شود.")
        return

    defaults = settings_store.get(user_id)

    pid = uuid4().hex[:10]

    pending_files[pid] = PendingFile(
        user_id=user_id,
        chat_id=message.chat.id,
        file_name=file_name,
        file_type=file_type,
        source_message=message,
        options={
            "quality": defaults["quality"],
            "watermark": defaults["watermark"],
            "upload_as": defaults["upload_as"],
            "target_chat_id": defaults["target_chat_id"],
            "target_label": defaults["target_label"],
            "artist": defaults["artist"],
            "logo_path": defaults["logo_path"],
            "logo_position": defaults["logo_position"],
            "title": "",
            "rename_to": "",
            "custom_thumbnail": "",
            "sort_mode": defaults["sort_mode"],
            "sort_order": defaults["sort_order"],
            "exclude_text": defaults["exclude_text"],
            "thumb_count": 0,
            "thumb_columns": 0,
            "make_collage": False,
        },
    )

    if file_type == "VIDEO":
        await message.answer(
            "🔻 کیفیت / فرمت خروجی را انتخاب کنید 🔻",
            reply_markup=quality_keyboard(pid),
        )
    else:
        await message.answer(
            "تنظیمات این فایل را بررسی و در صورت نیاز تغییر دهید:",
            reply_markup=options_keyboard(pid, pending_files),
        )
