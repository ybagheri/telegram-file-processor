import asyncio
from dataclasses import dataclass, field
from html import escape as html_escape
from uuid import uuid4

from aiogram import Dispatcher as AiogramDispatcher, F
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from config import Paths, Telegram
from core.constants import MessageType
from core.logger import get_logger
from core.protocol import Protocol
from services.settings_store import settings_store
from services.telegram import telegram_service
from utils.filetype import FileTypeDetector

logger = get_logger(__name__)

bot = telegram_service.bot
dp = AiogramDispatcher()

QUALITY_LABELS = {
    "144": "144p", "240": "240p", "360": "360p",
    "480": "480p", "720": "720p",
    "mp3": "🎵 فقط صدا (mp3)", "m4a": "🎧 صدا (m4a)", "voice": "🎙 وویس",
}

POSITION_ICONS = {
    "top_left": "↖️", "top_center": "⬆️", "top_right": "↗️",
    "middle_left": "⬅️", "center": "⏺", "middle_right": "➡️",
    "bottom_left": "↙️", "bottom_center": "⬇️", "bottom_right": "↘️",
}

POSITION_LABELS_FA = {
    "top_left": "بالا چپ", "top_center": "بالا وسط", "top_right": "بالا راست",
    "middle_left": "وسط چپ", "center": "مرکز", "middle_right": "وسط راست",
    "bottom_left": "پایین چپ", "bottom_center": "پایین وسط", "bottom_right": "پایین راست",
}

POSITION_GRID = [
    ["top_left", "top_center", "top_right"],
    ["middle_left", "center", "middle_right"],
    ["bottom_left", "bottom_center", "bottom_right"],
]


def logo_position_keyboard(current: str) -> InlineKeyboardMarkup:
    rows = []
    for grid_row in POSITION_GRID:
        row = []
        for pos in grid_row:
            icon = POSITION_ICONS[pos]
            text = f"✅{icon}" if pos == current else icon
            row.append(InlineKeyboardButton(text=text, callback_data=f"slogopos:{pos}"))
        rows.append(row)
    return InlineKeyboardMarkup(inline_keyboard=rows)


@dataclass
class PendingFile:
    user_id: int
    chat_id: int
    file_name: str
    file_type: str
    source_message: Message
    options: dict = field(default_factory=dict)


# In-memory state. Fine for a single-process bot; cleared on restart.
pending_files: dict[str, PendingFile] = {}
awaiting_state: dict[int, str] = {}  # user_id -> state tag

# job_id -> [(folder_name, message_id_in_destination_chat), ...], used to
# build a linked Table Of Contents once an archive job finishes.
job_folder_links: dict[str, list[tuple[str, int]]] = {}


# ======================================================================
# Keyboards
# ======================================================================

def quality_keyboard(pid: str) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="144p", callback_data=f"q:{pid}:144"),
         InlineKeyboardButton(text="240p", callback_data=f"q:{pid}:240")],
        [InlineKeyboardButton(text="360p", callback_data=f"q:{pid}:360"),
         InlineKeyboardButton(text="480p", callback_data=f"q:{pid}:480")],
        [InlineKeyboardButton(text="720p", callback_data=f"q:{pid}:720")],
        [InlineKeyboardButton(text="🎵 فقط صدا (mp3)", callback_data=f"q:{pid}:mp3"),
         InlineKeyboardButton(text="🎧 صدا (m4a)", callback_data=f"q:{pid}:m4a")],
        [InlineKeyboardButton(text="🎙 وویس", callback_data=f"q:{pid}:voice")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def options_keyboard(pid: str) -> InlineKeyboardMarkup:
    pending = pending_files[pid]
    o = pending.options
    rows = []

    is_video_output = pending.file_type == "VIDEO" and o.get("quality") not in ("mp3", "m4a", "voice")

    if is_video_output:
        rows.append([InlineKeyboardButton(
            text=f"📦 آپلود به‌صورت: {'ویدیو' if o.get('upload_as') == 'video' else 'فایل'}",
            callback_data=f"o:{pid}:upload_as",
        )])
        rows.append([InlineKeyboardButton(
            text=f"💧 واترمارک: {'فعال' if o.get('watermark') else 'غیرفعال'}",
            callback_data=f"o:{pid}:watermark",
        )])
        rows.append([InlineKeyboardButton(text="🖼 تغییر تامبنیل", callback_data=f"o:{pid}:thumb")])

    if pending.file_type == "AUDIO" or o.get("quality") in ("mp3", "m4a", "voice"):
        rows.append([InlineKeyboardButton(text="🎵 عنوان و خواننده", callback_data=f"o:{pid}:title")])

    rows.append([InlineKeyboardButton(text="✏️ تغییر نام فایل", callback_data=f"o:{pid}:name")])

    rows.append([InlineKeyboardButton(
        text=f"📤 مقصد: {o.get('target_label', 'خودم')}",
        callback_data=f"o:{pid}:target",
    )])

    rows.append([
        InlineKeyboardButton(text="✅ آپلود کن", callback_data=f"o:{pid}:go"),
        InlineKeyboardButton(text="❌ لغو", callback_data=f"o:{pid}:cancel"),
    ])

    return InlineKeyboardMarkup(inline_keyboard=rows)


def target_keyboard(pid: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👤 خودم", callback_data=f"t:{pid}:me")],
        [InlineKeyboardButton(text="➕ چت جدید", callback_data=f"t:{pid}:new")],
    ])


def settings_text_and_keyboard(user_id: int) -> dict:
    s = settings_store.get(user_id)

    text = (
        "⚙️ تنظیمات پیش‌فرض شما:\n\n"
        f"🎚 کیفیت پیش‌فرض ویدیو: {s['quality']}p\n"
        f"💧 واترمارک: {'فعال' if s['watermark'] else 'غیرفعال'}\n"
        f"📦 آپلود به‌صورت: {'ویدیو' if s['upload_as'] == 'video' else 'فایل'}\n"
        f"📤 مقصد ارسال پیش‌فرض: {s['target_label']}\n"
        f"🎤 خواننده پیش‌فرض: {s['artist'] or '—'}\n"
        f"🖼 لوگوی واترمارک: {'تنظیم شده' if s['logo_path'] else 'پیش‌فرض سیستم'}\n"
        f"📍 محل واترمارک: {POSITION_LABELS_FA.get(s['logo_position'], s['logo_position'])}\n"
        f"📝 کپشن پیش‌فرض مدیاها: {s['media_caption'] or '— (بدون کپشن)'}\n"
        f"🔤 ترتیب فایل‌های آرشیو: {'بر اساس تاریخ' if s['sort_mode'] == 'date' else 'بر اساس نام'} "
        f"({'نزولی' if s['sort_order'] == 'desc' else 'صعودی'})\n"
        f"🧹 متن حذفی از نام فایل‌ها: {s['exclude_text'] or '— (خالی)'}\n"
    )

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎚 تغییر کیفیت پیش‌فرض", callback_data="s:quality")],
        [InlineKeyboardButton(
            text=f"💧 واترمارک: {'خاموش کن' if s['watermark'] else 'روشن کن'}",
            callback_data="s:watermark",
        )],
        [InlineKeyboardButton(
            text=f"📦 آپلود پیش‌فرض: {'فایل کن' if s['upload_as'] == 'video' else 'ویدیو کن'}",
            callback_data="s:upload_as",
        )],
        [InlineKeyboardButton(text="📤 تغییر مقصد پیش‌فرض", callback_data="s:target")],
        [InlineKeyboardButton(text="🎤 تغییر خواننده پیش‌فرض", callback_data="s:artist")],
        [InlineKeyboardButton(text="🖼 تغییر لوگوی واترمارک", callback_data="s:logo")],
        [InlineKeyboardButton(text="📍 تغییر محل واترمارک", callback_data="s:logopos")],
        [InlineKeyboardButton(text="📝 تغییر کپشن پیش‌فرض", callback_data="s:caption")],
        [InlineKeyboardButton(
            text=f"🔤 ترتیب: {'تاریخ' if s['sort_mode'] == 'date' else 'نام'} کن",
            callback_data="s:sortmode",
        ),
         InlineKeyboardButton(
            text=f"↕️ جهت: {'نزولی' if s['sort_order'] == 'desc' else 'صعودی'} کن",
            callback_data="s:sortorder",
        )],
        [InlineKeyboardButton(text="🧹 تغییر متن حذفی (Exclude)", callback_data="s:exclude")],
    ])

    return {"text": text, "reply_markup": kb}


# ======================================================================
# Commands
# ======================================================================

@dp.message(Command("start"), F.chat.type == "private")
async def start(message: Message):
    await message.answer(
        "سلام! فایل خود را ارسال کنید.\n"
        "برای تنظیم مقادیر پیش‌فرض از /settings استفاده کنید."
    )


@dp.message(Command("settings"), F.chat.type == "private")
async def settings_command(message: Message):
    await message.answer(**settings_text_and_keyboard(message.from_user.id))


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
    awaiting_state[callback.from_user.id] = "settings_target"
    await callback.message.answer(
        "یک پیام از چت مقصد برای من فوروارد کنید یا @username آن را بفرستید.\n"
        "⚠️ ربات باید عضو آن گروه/کانال باشد."
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
        reply_markup=options_keyboard(pid),
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
        await callback.message.edit_reply_markup(reply_markup=options_keyboard(pid))
        await callback.answer()
        return

    if action == "watermark":
        pending.options["watermark"] = not pending.options.get("watermark")
        await callback.message.edit_reply_markup(reply_markup=options_keyboard(pid))
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

    if action == "title":
        awaiting_state[pending.user_id] = f"file:{pid}:title"
        await callback.message.answer("عنوان و خواننده را به‌صورت «عنوان | خواننده» بفرستید:")
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
        await callback.message.edit_text("❌ لغو شد.")
        await callback.answer()
        return


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
            reply_markup=options_keyboard(pid),
        )
        await callback.answer()
        return

    if choice == "new":
        awaiting_state[pending.user_id] = f"file:{pid}:target"
        await callback.message.answer(
            "یک پیام از گروه/کانال مقصد برای من فوروارد کنید، یا @username آن را بفرستید.\n"
            "⚠️ ربات باید عضو آن گروه/کانال (و دسترسی ارسال) داشته باشد."
        )
        await callback.answer()
        return


async def finalize_job(callback: CallbackQuery, pending: PendingFile, pid: str):

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
        try:
            chat = await bot.get_chat(message.text.strip())
            return chat.id, (chat.title or chat.username or str(chat.id))
        except Exception:
            return None, None

    return None, None


async def handle_awaited_input(message: Message, state: str) -> bool:

    user_id = message.from_user.id

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
            await message.answer("چت را نشناختم. یک پیام از آن فوروارد کنید یا @username بفرستید.")
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
            await message.answer("✅ تامبنیل ذخیره شد.", reply_markup=options_keyboard(pid))
            return True

        if field_name == "name":
            if not message.text:
                return False
            pending.options["rename_to"] = message.text.strip()
            awaiting_state.pop(user_id, None)
            await message.answer("✅ نام فایل بروزرسانی شد.", reply_markup=options_keyboard(pid))
            return True

        if field_name == "title":
            if not message.text:
                return False
            parts = message.text.split("|")
            pending.options["title"] = parts[0].strip()
            if len(parts) > 1:
                pending.options["artist"] = parts[1].strip()
            awaiting_state.pop(user_id, None)
            await message.answer("✅ عنوان/خواننده بروزرسانی شد.", reply_markup=options_keyboard(pid))
            return True

        if field_name == "target":
            chat_id, label = await _resolve_target(message)
            if chat_id is None:
                await message.answer("چت را نشناختم. یک پیام از آن فوروارد کنید یا @username بفرستید.")
                return True
            pending.options["target_chat_id"] = chat_id
            pending.options["target_label"] = label
            awaiting_state.pop(user_id, None)
            await message.answer("✅ مقصد بروزرسانی شد.", reply_markup=options_keyboard(pid))
            return True

    return False


# ======================================================================
# User side: new files + password replies + awaited input
# ======================================================================

# chat_id (== user_id for private chats) -> job_id waiting for an archive password
pending_passwords: dict[int, str] = {}


@dp.message(F.chat.type == "private")
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
            reply_markup=options_keyboard(pid),
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

        if message.document or message.video or message.audio or message.voice:

            # copyMessage keeps the ORIGINAL caption when caption is not
            # given at all, so we must always pass an explicit string here
            # (empty by default) — otherwise the raw protocol JSON caption
            # used for the bridge would leak straight through to the user.
            user_caption = settings_store.get(user_id).get("media_caption") or ""

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

        return


async def main():
    logger.info("Bot started")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
