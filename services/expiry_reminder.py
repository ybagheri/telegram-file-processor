"""
Background task: periodically DMs users whose access is about to expire.
Extracted out of bot.py (phase D, step 4 — the final step — of the
module split; see CLAUDE.md's change log) into a standalone service,
started from `bot.py`'s `main()`.
"""
import asyncio

from config import Telegram
from core.logger import get_logger
from services.access_store import access_store
from services.telegram import telegram_service
from utils.access_control import _user_display, format_expiry

logger = get_logger(__name__)
bot = telegram_service.bot

# How often the background task checks for users whose access is about to
# expire, and how far ahead it warns them.
REMINDER_CHECK_INTERVAL_SECONDS = 6 * 3600   # every 6 hours
REMINDER_THRESHOLD_SECONDS = 3 * 86400        # warn 3 days before expiry


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
