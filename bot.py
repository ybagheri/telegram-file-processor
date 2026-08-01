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

logger = get_logger(__name__)

bot = telegram_service.bot
dp = AiogramDispatcher()
dp.include_router(admin_router)

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

    # Track anyone who /start-s without being in access_store yet, so the
    # admin can see who's shown interest (and can be told about it once,
    # not on every repeat /start) — see CLAUDE.md's change log for why.
    if not is_admin(user_id) and access_store.get(user_id) is None:
        tg_user = message.from_user
        is_new_pending = await pending_user_store.record_start(
            user_id,
            first_name=tg_user.first_name or "",
            last_name=tg_user.last_name or "",
            username=tg_user.username or "",
            language_code=tg_user.language_code or "",
            is_bot=tg_user.is_bot or False,
        )
        if is_new_pending:
            await notify_admins_of_new_pending_user(tg_user)
            await pending_user_store.mark_notified(user_id)

    if not is_authorized(user_id):
        await message.answer(not_authorized_text(user_id))
        return

    await message.answer(
        "سلام! فایل خود را ارسال کنید.\n"
        "برای تنظیم مقادیر پیش‌فرض از /settings استفاده کنید."
    )


@dp.message(Command("settings"), F.chat.type == "private")
async def settings_command(message: Message):
    if not is_authorized(message.from_user.id):
        await message.answer(not_authorized_text(message.from_user.id))
        return
    await message.answer(**settings_text_and_keyboard(message.from_user.id))


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

@dp.callback_query(F.data == "s:quality")
async def settings_quality(callback: CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="144p", callback_data="sq:144"),
         InlineKeyboardButton(text="240p", callback_data="sq:240")],
        [InlineKeyboardButton(text="360p", callback_data="sq:360"),
         InlineKeyboardButton(text="480p", callback_data="sq:480")],
        [InlineKeyboardButton(text="720p", callback_data="sq:720")],
    ])
    await callback.message.edit_text("کیفیت پیش‌فرض جدید را انتخاب کنید:", reply_markup=kb)
    await callback.answer()


@dp.callback_query(F.data.startswith("sq:"))
async def settings_quality_pick(callback: CallbackQuery):
    value = callback.data.split(":", 1)[1]
    await settings_store.update(callback.from_user.id, quality=value)
    await callback.message.edit_text(**settings_text_and_keyboard(callback.from_user.id))
    await callback.answer("بروزرسانی شد")


@dp.callback_query(F.data == "s:watermark")
async def settings_watermark(callback: CallbackQuery):
    s = settings_store.get(callback.from_user.id)
    await settings_store.update(callback.from_user.id, watermark=not s["watermark"])
    await callback.message.edit_text(**settings_text_and_keyboard(callback.from_user.id))
    await callback.answer("بروزرسانی شد")


@dp.callback_query(F.data == "s:upload_as")
async def settings_upload_as(callback: CallbackQuery):
    s = settings_store.get(callback.from_user.id)
    new_val = "video" if s["upload_as"] == "document" else "document"
    await settings_store.update(callback.from_user.id, upload_as=new_val)
    await callback.message.edit_text(**settings_text_and_keyboard(callback.from_user.id))
    await callback.answer("بروزرسانی شد")


@dp.callback_query(F.data == "s:sortmode")
async def settings_sort_mode(callback: CallbackQuery):
    s = settings_store.get(callback.from_user.id)
    new_val = "date" if s["sort_mode"] == "name" else "name"
    await settings_store.update(callback.from_user.id, sort_mode=new_val)
    await callback.message.edit_text(**settings_text_and_keyboard(callback.from_user.id))
    await callback.answer("بروزرسانی شد")


@dp.callback_query(F.data == "s:sortorder")
async def settings_sort_order(callback: CallbackQuery):
    s = settings_store.get(callback.from_user.id)
    new_val = "desc" if s["sort_order"] == "asc" else "asc"
    await settings_store.update(callback.from_user.id, sort_order=new_val)
    await callback.message.edit_text(**settings_text_and_keyboard(callback.from_user.id))
    await callback.answer("بروزرسانی شد")


@dp.callback_query(F.data == "s:exclude")
async def settings_exclude(callback: CallbackQuery):
    awaiting_state[callback.from_user.id] = "settings_exclude"
    await callback.message.answer(
        "متنی که می‌خواهید از نام همه‌ی فایل‌ها حذف شود را بفرستید "
        "(مثلاً یک تبلیغ یا واترمارک متنی مثل «[www.site.com]»).\n"
        "برای غیرفعال کردن این قابلیت، کلمه‌ی «حذف» را بفرستید."
    )
    await callback.answer()


@dp.callback_query(F.data == "s:artist")
async def settings_artist(callback: CallbackQuery):
    awaiting_state[callback.from_user.id] = "settings_artist"
    await callback.message.answer("نام خواننده/هنرمند پیش‌فرض را بفرستید:")
    await callback.answer()


@dp.callback_query(F.data == "s:logo")
async def settings_logo(callback: CallbackQuery):
    awaiting_state[callback.from_user.id] = "settings_logo"
    await callback.message.answer("تصویر لوگو را به‌صورت عکس بفرستید:")
    await callback.answer()


@dp.callback_query(F.data == "s:logopos")
async def settings_logo_position(callback: CallbackQuery):
    current = settings_store.get(callback.from_user.id)["logo_position"]
    await callback.message.edit_text(
        "📍 محل قرارگیری واترمارک روی ویدیو را انتخاب کنید:",
        reply_markup=logo_position_keyboard(current),
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("slogopos:"))
async def settings_logo_position_pick(callback: CallbackQuery):
    position = callback.data.split(":", 1)[1]
    await settings_store.update(callback.from_user.id, logo_position=position)
    await callback.message.edit_text(**settings_text_and_keyboard(callback.from_user.id))
    await callback.answer(f"موقعیت: {POSITION_LABELS_FA.get(position, position)}")


@dp.callback_query(F.data == "s:target")
async def settings_target(callback: CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👤 ارسال به خودم", callback_data="starget:me")],
        [InlineKeyboardButton(text="➕ تنظیم کانال/گروه جدید", callback_data="starget:new")],
    ])
    await callback.message.edit_text(
        "مقصد پیش‌فرض ارسال فایل‌ها را انتخاب کنید:",
        reply_markup=kb,
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("starget:"))
async def settings_target_pick(callback: CallbackQuery):
    choice = callback.data.split(":", 1)[1]

    if choice == "me":
        await settings_store.update(callback.from_user.id, target_chat_id=0, target_label="خودم")
        await callback.message.edit_text(**settings_text_and_keyboard(callback.from_user.id))
        await callback.answer("بروزرسانی شد")
        return

    awaiting_state[callback.from_user.id] = "settings_target"
    await callback.message.answer(
        "یک پیام از چت مقصد برای من فوروارد کنید، @username یا آیدی عددی آن را بفرستید.\n"
        "⚠️ ربات باید عضو آن گروه/کانال باشد.\n"
        "برای انصراف /cancel را بفرستید."
    )
    await callback.answer()


@dp.callback_query(F.data == "s:caption")
async def settings_caption(callback: CallbackQuery):
    awaiting_state[callback.from_user.id] = "settings_caption"
    await callback.message.answer(
        "متن کپشن پیش‌فرض برای مدیاهای تحویلی را بفرستید.\n"
        "برای حذف کپشن (بدون کپشن)، کلمه‌ی «حذف» را بفرستید."
    )
    await callback.answer()


# ======================================================================
# Per-file quality / options callbacks
# ======================================================================

@dp.callback_query(F.data.startswith("q:"))
async def quality_pick(callback: CallbackQuery):
    _, pid, value = callback.data.split(":")
    pending = pending_files.get(pid)

    if not pending:
        await callback.answer("این درخواست منقضی شده است.", show_alert=True)
        return

    pending.options["quality"] = value

    await callback.message.edit_text(
        "تنظیمات این فایل را بررسی و در صورت نیاز تغییر دهید:",
        reply_markup=options_keyboard(pid, pending_files),
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("o:"))
async def options_action(callback: CallbackQuery):
    _, pid, action = callback.data.split(":", 2)
    pending = pending_files.get(pid)

    if not pending:
        await callback.answer("این درخواست منقضی شده است.", show_alert=True)
        return

    if action == "upload_as":
        pending.options["upload_as"] = "document" if pending.options.get("upload_as") == "video" else "video"
        await callback.message.edit_reply_markup(reply_markup=options_keyboard(pid, pending_files))
        await callback.answer()
        return

    if action == "watermark":
        pending.options["watermark"] = not pending.options.get("watermark")
        await callback.message.edit_reply_markup(reply_markup=options_keyboard(pid, pending_files))
        await callback.answer()
        return

    if action == "make_collage":
        pending.options["make_collage"] = not pending.options.get("make_collage")
        await callback.message.edit_reply_markup(reply_markup=options_keyboard(pid, pending_files))
        await callback.answer()
        return

    if action == "thumb":
        awaiting_state[pending.user_id] = f"file:{pid}:thumb"
        await callback.message.answer("تصویر تامبنیل جدید را بفرستید:")
        await callback.answer()
        return

    if action == "name":
        awaiting_state[pending.user_id] = f"file:{pid}:name"
        await callback.message.answer("نام جدید فایل را بفرستید (بدون پسوند):")
        await callback.answer()
        return

    if action == "thumb_count":
        awaiting_state[pending.user_id] = f"file:{pid}:thumb_count"
        await callback.message.answer(
            "چند تا عکس می‌خواهید؟ یک عدد بفرستید (مثلاً 6)، یا «خودکار» برای انتخاب خودکار بر اساس طول ویدیو."
        )
        await callback.answer()
        return

    if action == "thumb_columns":
        awaiting_state[pending.user_id] = f"file:{pid}:thumb_columns"
        await callback.message.answer(
            "عکس‌ها در چند ستون چیده شوند؟ یک عدد بفرستید (مثلاً 3)، یا «خودکار» برای چیدمان نزدیک به مربع."
        )
        await callback.answer()
        return

    if action == "title":
        awaiting_state[pending.user_id] = f"file:{pid}:title"
        await callback.message.answer("عنوان و خواننده را به‌صورت «عنوان | خواننده» بفرستید:")
        await callback.answer()
        return

    if action == "multipart":
        awaiting_state[pending.user_id] = f"file:{pid}:parts_count"
        await callback.message.answer(
            "این آرشیو چند بخش/تکه است؟ یک عدد بفرستید (مثلاً 5).\n"
            "بعد از اعلام تعداد، بخش‌هایی که همین الان فرستادید به‌عنوان بخش ۱ ثبت می‌شود "
            "و باید بقیه‌ی بخش‌ها را یکی‌یکی، به‌همین ترتیب که خودتان مرتب کرده‌اید، بفرستید."
        )
        await callback.answer()
        return

    if action == "archive_password":
        awaiting_state[pending.user_id] = f"file:{pid}:archive_password"
        await callback.message.answer(
            "رمز این آرشیو را بفرستید.\n"
            "اگر رمز را نمی‌دانید یا فراموش کردید، همین‌جا رد شوید — اگر لازم باشد، ربات خودش موقع پردازش رمز را از شما می‌خواهد."
        )
        await callback.answer()
        return

    if action == "target":
        await callback.message.edit_text(
            "مقصد ارسال فایل نهایی را انتخاب کنید:",
            reply_markup=target_keyboard(pid),
        )
        await callback.answer()
        return

    if action == "go":
        await finalize_job(callback, pending, pid)
        return

    if action == "cancel":
        pending_files.pop(pid, None)
        if awaiting_state.get(pending.user_id, "").startswith(f"file:{pid}:"):
            awaiting_state.pop(pending.user_id, None)
        await callback.message.edit_text("❌ لغو شد.")
        await callback.answer()
        return


@dp.callback_query(F.data == "nothing")
async def noop_callback(callback: CallbackQuery):
    await callback.answer()


@dp.callback_query(F.data.startswith("t:"))
async def target_pick(callback: CallbackQuery):
    _, pid, choice = callback.data.split(":")
    pending = pending_files.get(pid)

    if not pending:
        await callback.answer("این درخواست منقضی شده است.", show_alert=True)
        return

    if choice == "me":
        pending.options["target_chat_id"] = 0
        pending.options["target_label"] = "خودم"
        await callback.message.edit_text(
            "تنظیمات این فایل را بررسی و در صورت نیاز تغییر دهید:",
            reply_markup=options_keyboard(pid, pending_files),
        )
        await callback.answer()
        return

    if choice == "new":
        awaiting_state[pending.user_id] = f"file:{pid}:target"
        await callback.message.answer(
            "یک پیام از گروه/کانال مقصد برای من فوروارد کنید، @username یا آیدی عددی آن را بفرستید.\n"
            "⚠️ ربات باید عضو آن گروه/کانال (و دسترسی ارسال) داشته باشد.\n"
            "برای انصراف /cancel را بفرستید."
        )
        await callback.answer()
        return


async def finalize_job(callback: CallbackQuery, pending: PendingFile, pid: str):

    if pending.is_multipart:

        job_data = {
            "type": MessageType.JOB.value,
            "user_id": pending.user_id,
            "message_id": pending.part_message_ids[0],
            "part_message_ids": pending.part_message_ids,
            "file_type": pending.file_type,
            "file_name": pending.file_name,
            "original_chat_id": pending.chat_id,
            "options": pending.options,
        }

    else:

        forwarded = await pending.source_message.forward(Telegram.GROUP_ID)

        job_data = {
            "type": MessageType.JOB.value,
            "user_id": pending.user_id,
            "message_id": forwarded.message_id,
            "file_type": pending.file_type,
            "file_name": pending.file_name,
            "original_chat_id": pending.chat_id,
            "options": pending.options,
        }

    await telegram_service.send_job(job_data)

    pending_files.pop(pid, None)

    await callback.message.edit_text("✅ فایل برای پردازش ارسال شد. به‌زودی نتیجه برات میاد.")
    await callback.answer()


# ======================================================================
# Plain photo -> watermark flow
# ======================================================================
# (photo_confirm_keyboard moved to keyboards/photo.py — phase B of the
# module split, see CLAUDE.md's change log. Imported above.

async def handle_incoming_photo(message: Message):

    pid = uuid4().hex[:10]

    pending_photos[pid] = PendingPhoto(
        user_id=message.from_user.id,
        chat_id=message.chat.id,
        source_message=message,
    )

    await message.answer(
        "این یک عکسه، نه ویدیو/فایل صوتی/آرشیو. اگر می‌خواهید لوگوی واترمارک را روی همین عکس بزنم، تأیید کنید:",
        reply_markup=photo_confirm_keyboard(pid),
    )


@dp.callback_query(F.data.startswith("pw:"))
async def photo_watermark_action(callback: CallbackQuery):

    _, pid, action = callback.data.split(":", 2)
    pending = pending_photos.get(pid)

    if not pending:
        await callback.answer("این درخواست منقضی شده است.", show_alert=True)
        return

    if action == "apply":
        await callback.message.edit_text("⏳ در حال اعمال واترمارک...")
        await apply_watermark_to_photo(pid)
        await callback.answer()
        return

    if action == "changelogo":
        awaiting_state[pending.user_id] = "settings_logo"
        await callback.message.answer(
            "تصویر لوگوی جدید را به‌صورت عکس بفرستید. بعد از ذخیره، دوباره روی «💧 روی این عکس واترمارک بزن» بزنید."
        )
        await callback.answer()
        return

    if action == "changepos":
        current = settings_store.get(pending.user_id)["logo_position"]
        await callback.message.answer(
            "📍 محل جدید واترمارک را انتخاب کنید. بعد از انتخاب، دوباره روی «💧 روی این عکس واترمارک بزن» بزنید.",
            reply_markup=logo_position_keyboard(current),
        )
        await callback.answer()
        return

    if action == "cancel":
        pending_photos.pop(pid, None)
        await callback.message.edit_text("❌ باشه، کاری روی این عکس انجام نشد.")
        await callback.answer()
        return


async def apply_watermark_to_photo(pid: str):

    pending = pending_photos.pop(pid, None)

    if not pending:
        return

    settings = settings_store.get(pending.user_id)
    logo_path = Path(settings["logo_path"]) if settings.get("logo_path") else Paths.LOGO_FILE
    position = settings.get("logo_position", "bottom_right")

    if not logo_path.exists():
        await telegram_service.send_text(
            pending.user_id,
            "هنوز هیچ لوگویی برای واترمارک تنظیم نکرده‌اید. اول از /settings یک لوگو تنظیم کنید.",
        )
        return

    photo = pending.source_message.photo[-1]

    input_path = Paths.TEMP / f"{pid}_input.jpg"
    output_path = Paths.TEMP / f"{pid}_watermarked.jpg"
    input_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        await bot.download(photo, destination=input_path)

        ok = await media_service.watermark_image(input_path, output_path, logo_path, position)

        if not ok:
            await telegram_service.send_text(
                pending.user_id,
                "متأسفانه اعمال واترمارک روی این عکس با خطا مواجه شد.",
            )
            return

        await bot.send_photo(pending.user_id, FSInputFile(output_path))

    finally:
        for path in (input_path, output_path):
            if path.exists():
                try:
                    path.unlink()
                except OSError:
                    pass


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


async def _resolve_target(message: Message):
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


async def handle_awaited_input(message: Message, state: str) -> bool:

    user_id = message.from_user.id

    # admin_* states are handled entirely by handlers/admin.py — phase D,
    # step 1 of the module split (see CLAUDE.md's change log). This is the
    # dispatch point the other domains (settings/file/photo) will move
    # behind too, once they're split out in later steps.
    if state.startswith("admin_"):
        return await handle_admin_awaited_input(message, state)

    if state == "settings_artist":
        if not message.text:
            return False
        await settings_store.update(user_id, artist=message.text.strip())
        awaiting_state.pop(user_id, None)
        await message.answer(**settings_text_and_keyboard(user_id))
        return True

    if state == "settings_logo":
        if not message.photo:
            return False
        path = Paths.CONFIG / "logos" / f"{user_id}.jpg"
        path.parent.mkdir(parents=True, exist_ok=True)
        await bot.download(message.photo[-1], destination=path)
        await settings_store.update(user_id, logo_path=str(path))
        awaiting_state.pop(user_id, None)
        current = settings_store.get(user_id)["logo_position"]
        await message.answer(
            "✅ لوگو ذخیره شد.\n"
            "حالا محل قرارگیری واترمارک روی ویدیو را انتخاب کنید:",
            reply_markup=logo_position_keyboard(current),
        )
        return True

    if state == "settings_target":
        chat_id, label = await _resolve_target(message)
        if chat_id is None:
            if message.document or message.video or message.audio:
                # They clearly want to work on a new file now, not finish
                # setting a target — don't leave them stuck waiting.
                awaiting_state.pop(user_id, None)
                await message.answer("⏹ تنظیم مقصد لغو شد؛ این فایل را به‌عنوان کار جدید در نظر می‌گیرم.")
                return False
            await message.answer(
                "چت را نشناختم. یک پیام از آن فوروارد کنید، @username یا آیدی عددی چت را بفرستید.\n"
                "برای انصراف /cancel را بفرستید."
            )
            return True
        await settings_store.update(user_id, target_chat_id=chat_id, target_label=label)
        awaiting_state.pop(user_id, None)
        await message.answer(**settings_text_and_keyboard(user_id))
        return True

    if state == "settings_caption":
        if not message.text:
            return False
        new_caption = "" if message.text.strip() in ("حذف", "-", "none", "None") else message.text
        await settings_store.update(user_id, media_caption=new_caption)
        awaiting_state.pop(user_id, None)
        await message.answer(**settings_text_and_keyboard(user_id))
        return True

    if state == "settings_exclude":
        if not message.text:
            return False
        new_value = "" if message.text.strip() in ("حذف", "-", "none", "None") else message.text.strip()
        await settings_store.update(user_id, exclude_text=new_value)
        awaiting_state.pop(user_id, None)
        await message.answer(**settings_text_and_keyboard(user_id))
        return True

    if state.startswith("file:"):
        _, pid, field_name = state.split(":")
        pending = pending_files.get(pid)

        if not pending:
            awaiting_state.pop(user_id, None)
            return False

        if field_name == "thumb":
            if not message.photo:
                return False
            path = Paths.TEMP / f"{pid}_thumb.jpg"
            path.parent.mkdir(parents=True, exist_ok=True)
            await bot.download(message.photo[-1], destination=path)
            pending.options["custom_thumbnail"] = str(path)
            awaiting_state.pop(user_id, None)
            await message.answer("✅ تامبنیل ذخیره شد.", reply_markup=options_keyboard(pid, pending_files))
            return True

        if field_name == "name":
            if not message.text:
                return False
            pending.options["rename_to"] = message.text.strip()
            awaiting_state.pop(user_id, None)
            await message.answer("✅ نام فایل بروزرسانی شد.", reply_markup=options_keyboard(pid, pending_files))
            return True

        if field_name == "thumb_count":
            if not message.text:
                return False
            text = message.text.strip()
            if text in ("خودکار", "auto", "0", "-"):
                pending.options["thumb_count"] = 0
            elif text.isdigit() and 1 <= int(text) <= 60:
                pending.options["thumb_count"] = int(text)
            else:
                await message.answer("لطفاً یک عدد بین 1 تا 60 بفرستید، یا «خودکار».")
                return True
            awaiting_state.pop(user_id, None)
            await message.answer("✅ تعداد عکس‌ها بروزرسانی شد.", reply_markup=options_keyboard(pid, pending_files))
            return True

        if field_name == "thumb_columns":
            if not message.text:
                return False
            text = message.text.strip()
            if text in ("خودکار", "auto", "0", "-"):
                pending.options["thumb_columns"] = 0
            elif text.isdigit() and 1 <= int(text) <= 20:
                pending.options["thumb_columns"] = int(text)
            else:
                await message.answer("لطفاً یک عدد بین 1 تا 20 بفرستید، یا «خودکار».")
                return True
            awaiting_state.pop(user_id, None)
            await message.answer("✅ تعداد ستون بروزرسانی شد.", reply_markup=options_keyboard(pid, pending_files))
            return True

        if field_name == "title":
            if not message.text:
                return False
            parts = message.text.split("|")
            pending.options["title"] = parts[0].strip()
            if len(parts) > 1:
                pending.options["artist"] = parts[1].strip()
            awaiting_state.pop(user_id, None)
            await message.answer("✅ عنوان/خواننده بروزرسانی شد.", reply_markup=options_keyboard(pid, pending_files))
            return True

        if field_name == "target":
            chat_id, label = await _resolve_target(message)
            if chat_id is None:
                if message.document or message.video or message.audio:
                    awaiting_state.pop(user_id, None)
                    await message.answer("⏹ تنظیم مقصد لغو شد؛ این فایل را به‌عنوان کار جدید در نظر می‌گیرم.")
                    return False
                await message.answer(
                    "چت را نشناختم. یک پیام از آن فوروارد کنید، @username یا آیدی عددی چت را بفرستید.\n"
                    "برای انصراف /cancel را بفرستید."
                )
                return True
            pending.options["target_chat_id"] = chat_id
            pending.options["target_label"] = label
            awaiting_state.pop(user_id, None)
            await message.answer("✅ مقصد بروزرسانی شد.", reply_markup=options_keyboard(pid, pending_files))
            return True

        if field_name == "parts_count":
            if not message.text or not message.text.strip().isdigit():
                await message.answer("لطفاً فقط یک عدد بفرستید (مثلاً 5).")
                return True

            total = int(message.text.strip())

            if total < 2 or total > 100:
                await message.answer("تعداد بخش باید بین 2 تا 100 باشد.")
                return True

            forwarded = await pending.source_message.forward(Telegram.GROUP_ID)

            pending.is_multipart = True
            pending.parts_total = total
            pending.part_message_ids = [forwarded.message_id]

            awaiting_state[user_id] = f"file:{pid}:next_part"

            await message.answer(
                f"✅ بخش ۱ از {total} ثبت شد. لطفاً بخش ۲ را بفرستید."
            )
            return True

        if field_name == "archive_password":
            if not message.text:
                await message.answer("لطفاً رمز را به‌صورت متن بفرستید.")
                return True

            skip_words = ("رد", "ندارم", "نمی‌دانم", "نمیدانم", "-")
            text = message.text.strip()

            if text not in skip_words:
                pending.options["password"] = text
                await message.answer("✅ رمز ذخیره شد.")
            else:
                pending.options["password"] = ""
                await message.answer("باشه، رمز تنظیم نشد.")

            awaiting_state.pop(user_id, None)

            await message.answer(
                "تنظیمات این فایل را بررسی و در صورت نیاز تغییر دهید:",
                reply_markup=options_keyboard(pid, pending_files),
            )
            return True

        if field_name == "next_part":

            # A convenience shortcut: if the user knows the password, they
            # can just type it here instead of going back to the dedicated
            # button — it doesn't advance part collection.
            if message.text and not (message.document or message.video or message.audio):
                pending.options["password"] = message.text.strip()
                await message.answer(
                    f"✅ رمز ذخیره شد. حالا بخش {len(pending.part_message_ids) + 1} را بفرستید."
                )
                return True

            file = message.document or message.video or message.audio

            if not file:
                await message.answer("لطفاً فایل بخش بعدی را بفرستید (یا رمز آرشیو را به‌صورت متن).")
                return True

            forwarded = await message.forward(Telegram.GROUP_ID)
            pending.part_message_ids.append(forwarded.message_id)

            received = len(pending.part_message_ids)

            if received < pending.parts_total:
                await message.answer(
                    f"✅ بخش {received} از {pending.parts_total} ثبت شد. بخش بعدی را بفرستید."
                )
                return True

            awaiting_state.pop(user_id, None)

            await message.answer(
                f"✅ هر {pending.parts_total} بخش دریافت شد. "
                "تنظیمات نهایی را بررسی و در صورت نیاز تغییر دهید:",
                reply_markup=options_keyboard(pid, pending_files),
            )
            return True

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
