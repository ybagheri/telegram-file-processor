"""
/settings inline keyboards + the settings summary text. Extracted out of
bot.py (phase B of the module split; see CLAUDE.md's change log) with
behavior unchanged.
"""
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from keyboards.constants import POSITION_GRID, POSITION_ICONS, POSITION_LABELS_FA
from services.settings_store import settings_store


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
