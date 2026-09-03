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
        [InlineKeyboardButton(text="👥 کاربران استارت‌کرده (ثبت‌نام‌نشده)", callback_data="admin:list_pending")],
        [InlineKeyboardButton(text="⛔️ بلاک کردن کاربر", callback_data="admin:block_user")],
        [InlineKeyboardButton(text="✅ آنبلاک کردن کاربر", callback_data="admin:unblock_user")],
        [InlineKeyboardButton(text="📋 لیست کاربران بلاک‌شده", callback_data="admin:list_blocked")],
        [InlineKeyboardButton(text="📢 پیام همگانی به ثبت‌نام‌نشده‌ها", callback_data="admin:broadcast_start:pending")],
        [InlineKeyboardButton(text="📢 پیام همگانی به ثبت‌نام‌شده‌ها", callback_data="admin:broadcast_start:registered")],
        [InlineKeyboardButton(text="📢 پیام همگانی به همه‌ی کاربران", callback_data="admin:broadcast_start:all")],
        [InlineKeyboardButton(text="📝 پست تبلیغاتی پس از اتمام کار", callback_data="admin:promo_menu")],
        [InlineKeyboardButton(text="📊 آمار کاربران", callback_data="admin:stats")],
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


def promo_post_menu_keyboard(has_post: bool, enabled: bool) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(
            text="📝 تنظیم/تغییر پست" if has_post else "📝 تنظیم پست",
            callback_data="admin:promo_set",
        )],
    ]

    if has_post:
        rows.append([InlineKeyboardButton(text="👁 پیش‌نمایش پست فعلی", callback_data="admin:promo_preview")])
        toggle_label = "⏸ غیرفعال کردن ارسال خودکار" if enabled else "▶️ فعال کردن ارسال خودکار"
        rows.append([InlineKeyboardButton(text=toggle_label, callback_data="admin:promo_toggle")])
        rows.append([InlineKeyboardButton(text="🗑 حذف پست تبلیغاتی", callback_data="admin:promo_delete")])

    rows.append([InlineKeyboardButton(text="🔙 بازگشت", callback_data="admin:panel")])

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
