"""
Pure(ish) helpers around who's allowed to use the bot and how to identify/
describe a user — used by the admin panel, the file-flow authorization
check, and the pending-user notification path. Extracted out of bot.py
(phase A of the module-split — see CLAUDE.md's change log) with behavior
completely unchanged; bot.py now just imports these instead of defining
them inline.

`notify_admins_of_new_pending_user` is the one function here with a real
side effect (sending Telegram messages), kept in this module anyway
because it's tightly coupled to the same "who is this user" concerns as
everything else here, not because it's pure.
"""
from __future__ import annotations

import time
from datetime import datetime

from aiogram.types import Message

from config import Telegram
from core.logger import get_logger
from services.access_store import access_store
from services.telegram import telegram_service

logger = get_logger(__name__)
bot = telegram_service.bot


def is_admin(user_id: int) -> bool:
    return user_id in Telegram.ADMIN_IDS


def is_authorized(user_id: int) -> bool:
    # If no admin has been configured, access control is effectively off
    # — otherwise nobody, including the operator, could ever use the bot.
    if not Telegram.ADMIN_IDS:
        return True
    return is_admin(user_id) or access_store.is_authorized(user_id)


def not_authorized_text(user_id: int | None = None) -> str:
    contact = Telegram.ADMIN_CONTACT_USERNAME or "مدیر ربات"

    if user_id is not None:
        info = access_store.get(user_id)
        if info is not None:
            if not info.get("active", True):
                return (
                    "⛔️ دسترسی شما توسط مدیر ربات غیرفعال شده است.\n"
                    f"برای پیگیری به {contact} پیام بدهید."
                )
            if access_store.is_expired(info):
                return (
                    "⛔️ مدت اعتبار اشتراک شما به پایان رسیده است.\n"
                    f"برای تمدید، به {contact} پیام بدهید."
                )

    return (
        "⛔️ شما هنوز اجازه‌ی استفاده از این ربات را ندارید.\n"
        f"برای تهیه‌ی اشتراک و فعال‌سازی، به {contact} پیام بدهید."
    )


def compute_expiry(days: int) -> float | None:
    """days == 0 means unlimited (no expiry)."""
    if not days:
        return None
    return time.time() + days * 86400


def format_expiry(expires_at: float | None) -> str:
    if expires_at is None:
        return "نامحدود"
    return datetime.fromtimestamp(expires_at).strftime("%Y-%m-%d %H:%M")


def _extract_target_from_message(message: Message) -> tuple[int | None, str, str]:
    """Try to identify a target user from an admin's message: either a
    forwarded message (author revealed) or a raw numeric user id typed by
    hand. Returns (user_id, name, username) — name/username are only ever
    populated when we got them from a real forward; for a manually-typed id
    they come back empty and the caller should ask for them separately."""
    forwarded_user = getattr(message, "forward_from", None)

    if forwarded_user:
        name = getattr(forwarded_user, "full_name", None) or ""
        username = getattr(forwarded_user, "username", None) or ""
        return forwarded_user.id, name, username

    if message.text and message.text.strip().lstrip("-").isdigit():
        return int(message.text.strip()), "", ""

    return None, "", ""


def _user_display(target_id: int, info: dict | None) -> str:
    """Best-effort human-friendly label for a user, for confirmation
    prompts and result messages — falls back to the raw id if we have
    nothing else on record."""
    if info is None:
        return str(target_id)
    return info.get("name") or info.get("username") or info.get("label") or str(target_id)


async def notify_admins_of_new_pending_user(tg_user):
    """DM every configured admin once, the first time someone `/start`s
    the bot without being registered in `access_store` yet — so the admin
    finds out about interested users without having to check anything."""
    full_name = (tg_user.full_name or "").strip() or "—"
    username = f"@{tg_user.username}" if tg_user.username else "ندارد"
    when = datetime.fromtimestamp(time.time()).strftime("%Y-%m-%d %H:%M")

    text = (
        "🆕 کاربر جدید ربات\n\n"
        f"👤 نام:\n{full_name}\n\n"
        f"🔹 یوزرنیم:\n{username}\n\n"
        f"🆔 Telegram ID:\n{tg_user.id}\n\n"
        f"⏰ زمان:\n{when}"
    )

    for admin_id in Telegram.ADMIN_IDS:
        try:
            await bot.send_message(admin_id, text)
        except Exception:
            logger.warning("Could not notify admin %s about new pending user %s", admin_id, tg_user.id)
