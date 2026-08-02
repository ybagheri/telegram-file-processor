import asyncio
from html import escape as html_escape
from pathlib import Path
from uuid import uuid4

from aiogram import Dispatcher as AiogramDispatcher, F, Router
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from config import Paths, Telegram
from core.constants import MessageType
from core.logger import get_logger
from core.protocol import Protocol
from services.access_store import access_store
from services.media import media_service
from services.pending_user_store import pending_user_store
from services.settings_store import settings_store
from services.telegram import telegram_service
from utils.filetype import FileTypeDetector
from utils.access_control import (
    is_admin,
    is_authorized,
    not_authorized_text,
    compute_expiry,
    format_expiry,
    _extract_target_from_message,
    _user_display,
    notify_admins_of_new_pending_user,
    track_pending_user_if_needed,
)
from keyboards.constants import (
    QUALITY_LABELS,
    POSITION_ICONS,
    POSITION_LABELS_FA,
    POSITION_GRID,
    DURATION_OPTIONS,
)
from keyboards.admin import admin_panel_keyboard, duration_keyboard, confirm_keyboard
from keyboards.settings import logo_position_keyboard, settings_text_and_keyboard
from keyboards.files import quality_keyboard, options_keyboard, target_keyboard
from keyboards.photo import photo_confirm_keyboard
from handlers.admin import (
    router as admin_router,
    admin_command,
    admin_add_user,
    admin_renew_user,
    admin_toggle_user,
    admin_delete_user,
    admin_toggle_confirmed,
    admin_toggle_cancelled,
    admin_delete_confirmed,
    admin_delete_cancelled,
    admin_duration_selected,
    admin_list_users,
    admin_manage_user,
    admin_manage_renew,
    admin_manage_toggle,
    admin_manage_delete,
    handle_admin_awaited_input,
)

from handlers.settings import (
    router as settings_router,
    settings_command,
    settings_quality,
    settings_quality_pick,
    settings_watermark,
    settings_upload_as,
    settings_sort_mode,
    settings_sort_order,
    settings_exclude,
    settings_artist,
    settings_logo,
    settings_logo_position,
    settings_logo_position_pick,
    settings_target,
    settings_target_pick,
    settings_caption,
    handle_settings_awaited_input,
)
from services.target_resolver import resolve_target as _resolve_target
from handlers.files import (
    router as files_router,
    quality_pick,
    options_action,
    noop_callback,
    target_pick,
    finalize_job,
    handle_file_awaited_input,
)
from handlers.photo import (
    router as photo_router,
    handle_incoming_photo,
    photo_watermark_action,
    apply_watermark_to_photo,
)

logger = get_logger(__name__)

bot = telegram_service.bot
dp = AiogramDispatcher()
dp.include_router(admin_router)
dp.include_router(settings_router)
dp.include_router(files_router)
dp.include_router(photo_router)

# IMPORTANT: aiogram checks a router's OWN directly-decorated handlers
# before descending into any included sub-router, REGARDLESS of when
# dp.include_router(...) was called relative to when those own handlers
# were defined. handle_private_message's filter below is a bare
# "F.chat.type == 'private'" catch-all with no further discrimination —
# if it were registered directly on `dp` (as it originally was, before
# the admin router existed), it would swallow "/admin" and every other
# private message BEFORE admin_router's Command("admin")/callback filters
# ever got a chance, since dp's own handlers always win over sub-routers.
# Putting it on its own router instead, included *after* admin_router,
# makes ordinary sub-router-vs-sub-router inclusion order apply (verified
# empirically — see the phase-D entry in CLAUDE.md's change log for the
# regression this was actually caught fixing). Any future router with a
# specific filter must be included before this one; this one should
# always be included last.
catchall_router = Router(name="catchall")
dp.include_router(catchall_router)


# ======================================================================
# Access control
# ======================================================================
# (moved to utils/access_control.py — phase A of the module split; see
# CLAUDE.md's change log. Imported above so every existing call site in
# this file — is_admin(...), is_authorized(...), etc. — keeps working
# unchanged.)
#
# The admin/settings-panel keyboards that used to live here (admin_panel_keyboard,
# duration_keyboard, confirm_keyboard, logo_position_keyboard) moved to
# keyboards/admin.py and keyboards/settings.py — phase B of the module
# split (see CLAUDE.md's change log). Imported above.


from models.pending_file import PendingFile
from models.pending_photo import PendingPhoto
from state import (
    pending_files,
    pending_photos,
    awaiting_state,
    admin_flow,
    pending_passwords,
    job_folder_links,
)

# PendingFile, PendingPhoto, and every shared in-process dict
# (pending_files, pending_photos, awaiting_state, admin_flow,
# pending_passwords, job_folder_links) moved to models/ and state.py —
# phase C of the module split (see CLAUDE.md's change log). Imported
# above; every existing call site in this file keeps working unchanged
# since these are the exact same dict objects, just defined elsewhere.

# How often the background task checks for users whose access is about to
# expire, and how far ahead it warns them.
REMINDER_CHECK_INTERVAL_SECONDS = 6 * 3600   # every 6 hours
REMINDER_THRESHOLD_SECONDS = 3 * 86400        # warn 3 days before expiry


# ======================================================================
# Keyboards
# ======================================================================
# (quality_keyboard, options_keyboard, target_keyboard moved to
# keyboards/files.py; settings_text_and_keyboard moved to
# keyboards/settings.py — phase B of the module split, see CLAUDE.md's
# change log. Imported above.


# ======================================================================
# Commands
# ======================================================================

@dp.message(Command("start"), F.chat.type == "private")
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


# /settings command moved to handlers/settings.py (registered on its
# Router, included above via dp.include_router(settings_router)) — phase
# D, step 2 of the module split, see CLAUDE.md's change log.

# /admin command moved to handlers/admin.py (registered on its Router,
# included above via dp.include_router(admin_router)) — phase D, step 1
# of the module split, see CLAUDE.md's change log. `admin_command` is
# still imported into this namespace for backward compatibility.


@dp.message(Command("cancel"), F.chat.type == "private")
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
# Admin panel callbacks
# ======================================================================
# (moved to handlers/admin.py, registered on its Router — phase D, step 1
# of the module split, see CLAUDE.md's change log. Every admin:*
# callback is imported above for backward compatibility.)

# ======================================================================
# Global settings callbacks
# ======================================================================
# (moved to handlers/settings.py, registered on its Router — phase D,
# step 2 of the module split, see CLAUDE.md's change log. Every s:*/
# sq:*/slogopos:*/starget:* callback is imported above for backward
# compatibility.)

# ======================================================================
# Per-file quality / options callbacks
# ======================================================================
# (moved to handlers/files.py, registered on its Router — phase D, step 3
# of the module split, see CLAUDE.md's change log. quality_pick,
# options_action, noop_callback, target_pick, finalize_job, and
# handle_file_awaited_input are imported above for backward compatibility.)

# ======================================================================
# Plain photo -> watermark flow
# ======================================================================
# (moved to handlers/photo.py, registered on its Router — phase D, step 3
# of the module split, see CLAUDE.md's change log. handle_incoming_photo,
# photo_watermark_action, apply_watermark_to_photo are imported above for
# backward compatibility.)


# ======================================================================
# Awaited free-text / photo / forward input
# ======================================================================

async def _resolve_message_link(chat_id: int, message_id: int) -> str:

    try:
        chat = await bot.get_chat(chat_id)
        if getattr(chat, "username", None):
            return f"https://t.me/{chat.username}/{message_id}"
    except Exception:
        pass

    internal = str(chat_id)
    internal = internal[4:] if internal.startswith("-100") else internal.lstrip("-")

    return f"https://t.me/c/{internal}/{message_id}"


# _resolve_target moved to services/target_resolver.py (phase D, step 2
# of the module split, see CLAUDE.md's change log) — imported above as
# _resolve_target for backward compatibility.


async def handle_awaited_input(message: Message, state: str) -> bool:

    user_id = message.from_user.id

    # admin_* states are handled entirely by handlers/admin.py — phase D,
    # step 1 of the module split (see CLAUDE.md's change log).
    if state.startswith("admin_"):
        return await handle_admin_awaited_input(message, state)

    # settings_* states are handled entirely by handlers/settings.py —
    # phase D, step 2 of the module split.
    if state.startswith("settings_"):
        return await handle_settings_awaited_input(message, state)

    # file:* states are handled entirely by handlers/files.py — phase D,
    # step 3 of the module split.
    if state.startswith("file:"):
        return await handle_file_awaited_input(message, state)

    return False


# ======================================================================
# User side: new files + password replies + awaited input
# ======================================================================
# (pending_passwords moved to state.py — phase C of the module split, see
# CLAUDE.md's change log. Imported above.)

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


# ======================================================================
# Bridge side: messages coming back from worker.py through the group
# ======================================================================

@dp.message(F.chat.id == Telegram.GROUP_ID)
async def handle_bridge_message(message: Message):

    raw = message.text or message.caption

    if not raw:
        return

    try:
        payload = Protocol.decode(raw)
    except Exception:
        return

    message_type = payload.get("type")
    user_id = payload.get("user_id")

    if not user_id:
        return

    destination = payload.get("target_chat_id") or user_id

    if message_type == MessageType.RESULT.value:

        if message.document or message.video or message.audio or message.voice or message.photo:

            # copyMessage keeps the ORIGINAL caption when caption is not
            # given at all, so we must always pass an explicit string here
            # — otherwise the raw protocol JSON caption used for the
            # bridge would leak straight through to the user.
            files = payload.get("files") or []
            file_name = files[0] if files else ""
            name_without_ext = Path(file_name).stem if file_name else ""

            custom_caption = settings_store.get(user_id).get("media_caption") or ""

            caption_parts = [p for p in (name_without_ext, custom_caption) if p]
            user_caption = "\n\n".join(caption_parts)

            await telegram_service.copy_message_to(
                chat_id=destination,
                from_chat_id=message.chat.id,
                message_id=message.message_id,
                caption=user_caption,
            )

        return

    if message_type == MessageType.ERROR.value:

        await telegram_service.send_text(
            user_id,
            f"❌ خطا: {payload.get('message', 'پردازش ناموفق بود.')}",
        )

        return

    if message_type == MessageType.PASSWORD_REQUEST.value:

        job_id = payload.get("job_id")
        filename = payload.get("filename", "")

        pending_passwords[user_id] = job_id

        await telegram_service.send_text(
            user_id,
            f"🔒 فایل «{filename}» رمزدار است. لطفاً رمز آن را ارسال کنید.",
        )

        return

    if message_type == MessageType.INFO.value:

        await telegram_service.send_text(
            destination,
            payload.get("message", ""),
        )

        return

    if message_type == MessageType.FOLDER.value:

        job_id = payload.get("job_id")
        folder = payload.get("folder", "")

        sent = await telegram_service.send_text(destination, f"📂 {folder}")

        if sent is not None:
            job_folder_links.setdefault(job_id, []).append((folder, sent.message_id))

        return

    if message_type == MessageType.DONE.value:

        job_id = payload.get("job_id")
        folders = job_folder_links.pop(job_id, None)

        # A TOC with clickable links only makes sense when the files were
        # delivered to a channel/group the user can link back into —
        # there's no public link for a private 1:1 chat with the bot.
        if folders and destination != user_id:

            lines = ["📑 <b>فهرست مطالب</b>\n"]

            for name, message_id in folders:
                url = await _resolve_message_link(destination, message_id)
                lines.append(f'📂 <a href="{url}">{html_escape(name)}</a>')

            try:
                await telegram_service.send_text(
                    destination,
                    "\n".join(lines),
                    parse_mode="HTML",
                )
            except Exception:
                logger.exception("Failed to send TOC for job %s", job_id)

        try:
            await telegram_service.send_text(
                user_id,
                "✅ همه‌ی فایل‌ها با موفقیت ارسال شدند.",
            )
        except Exception:
            logger.exception("Failed to send completion notice for job %s", job_id)

        return


async def check_and_send_expiry_reminders():
    """One pass: find users nearing expiry and DM each of them once. Split
    out from `expiry_reminder_loop` so it can be driven directly (e.g. from
    tests) without waiting on the sleep loop."""
    due = access_store.list_expiring_soon(REMINDER_THRESHOLD_SECONDS)
    for info in due:
        target_id = info["user_id"]
        display = _user_display(target_id, info)
        expiry_text = format_expiry(info["expires_at"])

        try:
            await bot.send_message(
                target_id,
                "⏳ اشتراک شما به‌زودی به پایان می‌رسد.\n"
                f"تاریخ انقضا: {expiry_text}\n"
                f"برای تمدید، به {Telegram.ADMIN_CONTACT_USERNAME or 'مدیر ربات'} پیام بدهید.",
            )
        except Exception:
            # User may have blocked the bot, deleted their account, etc.
            # Not fatal — we still mark them reminded below so a
            # permanently-unreachable user doesn't get retried every single
            # cycle forever.
            logger.warning(
                "Could not deliver expiry reminder to %s (%s)",
                target_id, display,
            )

        await access_store.mark_reminded(target_id, info["expires_at"])

    return due


async def expiry_reminder_loop():
    """Background task: periodically DMs users whose access is about to
    expire, so they find out ahead of time instead of getting cut off
    mid-use. Runs forever alongside polling; one failed cycle doesn't kill
    it, it just logs and tries again next interval."""
    while True:
        try:
            await check_and_send_expiry_reminders()
        except Exception:
            logger.exception("Error while checking for users nearing expiry")

        await asyncio.sleep(REMINDER_CHECK_INTERVAL_SECONDS)


async def main():
    logger.info("Bot started")
    asyncio.create_task(expiry_reminder_loop())
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
