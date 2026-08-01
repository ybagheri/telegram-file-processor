"""
Admin-panel inline keyboards. Extracted out of bot.py (phase B of the
module split; see CLAUDE.md's change log) with behavior unchanged.
"""
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from keyboards.constants import DURATION_OPTIONS


def admin_panel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ افزودن کاربر", callback_data="admin:add_user")],
        [InlineKeyboardButton(text="📋 لیست کاربران مجاز", callback_data="admin:list_users")],
        [InlineKeyboardButton(text="⏳ تمدید / تغییر انقضا", callback_data="admin:renew_user")],
        [InlineKeyboardButton(text="🚫 فعال/غیرفعال کردن کاربر", callback_data="admin:toggle_user")],
        [InlineKeyboardButton(text="🗑 حذف کامل کاربر", callback_data="admin:delete_user")],
    ])


def duration_keyboard(purpose: str, target_id: int | None = None) -> InlineKeyboardMarkup:
    rows = []
    for label, days in DURATION_OPTIONS:
        callback_data = (
            f"admin:dur:{purpose}:{days}"
            if target_id is None
            else f"admin:dur:{purpose}:{days}:{target_id}"
        )
        rows.append([InlineKeyboardButton(text=label, callback_data=callback_data)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def confirm_keyboard(
    confirm_data: str,
    cancel_data: str,
    confirm_label: str = "✅ بله، انجام بده",
    cancel_label: str = "❌ انصراف",
) -> InlineKeyboardMarkup:
    """Generic yes/no keyboard for anything destructive/hard-to-undo enough
    to deserve a confirmation step before it actually happens (disabling or
    deleting a user, so far)."""
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=confirm_label, callback_data=confirm_data),
        InlineKeyboardButton(text=cancel_label, callback_data=cancel_data),
    ]])
