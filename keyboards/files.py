"""
Keyboards for the main file-processing flow (quality pick -> options ->
target). Extracted out of bot.py (phase B of the module split; see
CLAUDE.md's change log).

`options_keyboard` used to read the `pending_files` dict as an implicit
module-level global in bot.py. Now that it lives in its own module, it
takes `pending_files` as an explicit parameter instead — a small, free
improvement while moving it anyway (no more hidden global dependency,
easier to test in isolation), not a behavior change: every call site in
bot.py already has `pending_files` in scope and just passes it along.
"""
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


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
        [InlineKeyboardButton(text="🖼 فقط کولاژ تامبنیل (بدون تبدیل ویدیو)", callback_data=f"q:{pid}:thumbs")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def options_keyboard(pid: str, pending_files: dict) -> InlineKeyboardMarkup:
    pending = pending_files[pid]
    o = pending.options
    rows = []

    is_video_output = pending.file_type == "VIDEO" and o.get("quality") not in ("mp3", "m4a", "voice", "thumbs")
    is_thumbs_only = o.get("quality") == "thumbs"

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

    if pending.file_type == "VIDEO" and not is_thumbs_only:
        rows.append([InlineKeyboardButton(
            text=f"🖼 همراه با کولاژ تامبنیل: {'بله ✅' if o.get('make_collage') else 'خیر'}",
            callback_data=f"o:{pid}:make_collage",
        )])

    if is_thumbs_only or o.get("make_collage"):
        count_label = o.get("thumb_count") or "خودکار"
        columns_label = o.get("thumb_columns") or "خودکار"
        rows.append([InlineKeyboardButton(
            text=f"🔢 تعداد عکس‌ها: {count_label}",
            callback_data=f"o:{pid}:thumb_count",
        )])
        rows.append([InlineKeyboardButton(
            text=f"📐 تعداد ستون: {columns_label}",
            callback_data=f"o:{pid}:thumb_columns",
        )])

    if pending.file_type == "AUDIO" or o.get("quality") in ("mp3", "m4a", "voice"):
        rows.append([InlineKeyboardButton(text="🎵 عنوان و خواننده", callback_data=f"o:{pid}:title")])

    if pending.file_type == "ARCHIVE":
        if pending.is_multipart:
            rows.append([InlineKeyboardButton(
                text=f"📦 چندبخشی: {len(pending.part_message_ids)}/{pending.parts_total} بخش دریافت شد",
                callback_data="nothing",
            )])
        else:
            rows.append([InlineKeyboardButton(text="📦 آرشیو چندبخشی است", callback_data=f"o:{pid}:multipart")])

        rows.append([InlineKeyboardButton(
            text=f"🔑 رمز آرشیو: {'تنظیم شده ✅' if o.get('password') else 'اگر می‌دانید تنظیم کنید'}",
            callback_data=f"o:{pid}:archive_password",
        )])

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


def url_mode_keyboard(pid: str) -> InlineKeyboardMarkup:
    """The choice a user gets when they send a file URL: deliver the
    downloaded file as-is (no conversion/processing), or feed it through
    the normal per-file processing flow."""

    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="⬆️ ارسال مستقیم (بدون پردازش)",
            callback_data=f"urlmode:{pid}:direct",
        )],
        [InlineKeyboardButton(
            text="⚙️ پردازش کامل فایل",
            callback_data=f"urlmode:{pid}:process",
        )],
        [InlineKeyboardButton(text="❌ لغو", callback_data=f"urlmode:{pid}:cancel")],
    ])


def target_keyboard(pid: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👤 خودم", callback_data=f"t:{pid}:me")],
        [InlineKeyboardButton(text="➕ چت جدید", callback_data=f"t:{pid}:new")],
    ])
