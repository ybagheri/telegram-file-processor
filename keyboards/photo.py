"""
Keyboard for the plain-photo -> watermark confirmation flow. Extracted out
of bot.py (phase B of the module split; see CLAUDE.md's change log) with
behavior unchanged.
"""
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def photo_confirm_keyboard(pid: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💧 روی این عکس واترمارک بزن", callback_data=f"pw:{pid}:apply")],
        [InlineKeyboardButton(text="🖼 تغییر لوگوی واترمارک", callback_data=f"pw:{pid}:changelogo")],
        [InlineKeyboardButton(text="📍 تغییر محل واترمارک", callback_data=f"pw:{pid}:changepos")],
        [InlineKeyboardButton(text="❌ نه، کاری نکن", callback_data=f"pw:{pid}:cancel")],
    ])
