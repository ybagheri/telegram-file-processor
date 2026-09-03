"""
Admin panel: /admin command, every admin:* callback, and the admin_*
awaited-input states (the free-text follow-ups to some of those
callbacks — identifying a target user by forward/id, typing a name/
username by hand). Extracted out of bot.py (phase D, step 1 of the module
split — see CLAUDE.md's change log) as an aiogram 3 Router, registered
from bot.py via `dp.include_router(router)`.

`handle_admin_awaited_input()` is called from bot.py's top-level
`handle_awaited_input()` dispatcher for any state starting with "admin_" —
it is NOT a `@router` handler itself (awaited free-text input isn't routed
by aiogram filters here, it's dispatched manually based on `state.py`'s
`awaiting_state` dict), just a plain function this module exposes.
"""
from aiogram import F, Router

import time

from datetime import datetime

from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from config import Heartbeat
from keyboards.admin import admin_panel_keyboard, duration_keyboard, confirm_keyboard, promo_post_menu_keyboard
from core.logger import get_logger
from services.access_store import access_store
from services.blocked_user_store import blocked_user_store
from services.pending_user_store import pending_user_store
from services.promo_post_store import promo_post_store
from services.telegram import telegram_service
from utils.access_control import (
    is_admin,
    is_blocked,
    not_authorized_text,
    compute_expiry,
    format_expiry,
    _extract_target_from_message,
    _user_display,
)
from state import awaiting_state, admin_flow, worker_last_seen

router = Router(name="admin")
logger = get_logger(__name__)
bot = telegram_service.bot


# ======================================================================
# Command
# ======================================================================

@router.message(Command("admin"), F.chat.type == "private")
async def admin_command(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer(not_authorized_text())
        return

    await message.answer(
        "⚙️ پنل مدیریت کاربران:",
        reply_markup=admin_panel_keyboard(),
    )


@router.callback_query(F.data == "admin:panel")
async def admin_panel_back(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("اجازه‌ی این کار را ندارید.", show_alert=True)
        return

    await callback.message.answer(
        "⚙️ پنل مدیریت کاربران:",
        reply_markup=admin_panel_keyboard(),
    )
    await callback.answer()


def format_worker_status(
    last_seen: float | None,
    now: float,
    interval_seconds: int,
) -> str:
    """Persian one-liner for the admin /status command, built from the
    "worker last seen" tracker. Kept pure (timestamps in, string out) so
    the staleness thresholds are testable without any Telegram objects."""

    if last_seen is None:
        return (
            "🖥 وضعیت Worker:\n"
            "❌ Worker تاکنون هیچ سیگنالی نفرستاده است — احتمالاً از کار افتاده."
        )

    delta = now - last_seen

    if delta <= interval_seconds + 60:
        return (
            "🖥 وضعیت Worker:\n"
            f"✅ Worker فعال است (آخرین سیگنال: {int(delta)} ثانیه پیش)."
        )

    minutes = int(delta // 60)

    return (
        "🖥 وضعیت Worker:\n"
        f"⚠️ آخرین سیگنال Worker {minutes} دقیقه پیش بود "
        f"(هر {interval_seconds} ثانیه یک سیگنال ارسال می‌شود). "
        "ممکن است Worker از کار افتاده باشد."
    )


@router.message(Command("status"), F.chat.type == "private")
async def status_command(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer(not_authorized_text())
        return

    await message.answer(
        format_worker_status(
            worker_last_seen.get("worker"),
            time.time(),
            Heartbeat.INTERVAL_SECONDS,
        )
    )


# ======================================================================
# Admin panel callbacks
# ======================================================================

@router.callback_query(F.data == "admin:add_user")
async def admin_add_user(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("اجازه‌ی این کار را ندارید.", show_alert=True)
        return

    await callback.message.answer(
        "مدت اعتبار دسترسی کاربر جدید را انتخاب کنید:",
        reply_markup=duration_keyboard("add"),
    )
    await callback.answer()


@router.callback_query(F.data == "admin:renew_user")
async def admin_renew_user(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("اجازه‌ی این کار را ندارید.", show_alert=True)
        return

    awaiting_state[callback.from_user.id] = "admin_renew_target"
    await callback.message.answer(
        "یک پیام از کاربر مورد نظر فوروارد کنید یا آیدی عددی‌اش را بفرستید "
        "تا تاریخ انقضای او را تغییر دهید.\n"
        "برای انصراف /cancel را بفرستید."
    )
    await callback.answer()


@router.callback_query(F.data == "admin:toggle_user")
async def admin_toggle_user(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("اجازه‌ی این کار را ندارید.", show_alert=True)
        return

    awaiting_state[callback.from_user.id] = "admin_toggle_target"
    await callback.message.answer(
        "یک پیام از کاربر مورد نظر فوروارد کنید یا آیدی عددی‌اش را بفرستید "
        "تا وضعیت فعال/غیرفعال او تغییر کند.\n"
        "برای انصراف /cancel را بفرستید."
    )
    await callback.answer()


@router.callback_query(F.data == "admin:delete_user")
async def admin_delete_user(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("اجازه‌ی این کار را ندارید.", show_alert=True)
        return

    awaiting_state[callback.from_user.id] = "admin_delete_target"
    await callback.message.answer(
        "یک پیام از کاربر مورد نظر فوروارد کنید یا آیدی عددی‌اش را بفرستید "
        "تا رکوردش به‌طور کامل حذف شود.\n"
        "⚠️ این کار غیرقابل بازگشت است (برخلاف غیرفعال‌سازی، چیزی برای بازگردانی باقی نمی‌ماند).\n"
        "برای انصراف /cancel را بفرستید."
    )
    await callback.answer()


# ======================================================================
# Pending (started-but-not-registered) users
# ======================================================================

@router.callback_query(F.data == "admin:list_pending")
async def admin_list_pending(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("اجازه‌ی این کار را ندارید.", show_alert=True)
        return

    pending = pending_user_store.list_all()

    if not pending:
        await callback.message.answer("در حال حاضر هیچ کاربر استارت‌کرده‌ی ثبت‌نام‌نشده‌ای وجود ندارد.")
        await callback.answer()
        return

    lines = ["👥 کاربران استارت‌کرده و ثبت‌نام‌نشده:\n"]
    rows = []

    for uid_str, info in pending.items():
        uid = int(uid_str)
        name = info.get("first_name") or ""
        username = f"@{info['username']}" if info.get("username") else "بدون یوزرنیم"
        seen_at = datetime.fromtimestamp(info["first_seen_at"]).strftime("%Y-%m-%d %H:%M")
        lines.append(
            f"• {uid} ({name or '—'}, {username}) — اولین بار: {seen_at} — "
            f"تعداد استارت: {info.get('start_count', 1)}"
        )

        label = (name or username)[:24]
        rows.append([InlineKeyboardButton(
            text=f"⛔️ بلاک {label} ({uid})",
            callback_data=f"admin:block_confirm:{uid}",
        )])

    lines.append("\nبرای بلاک سریع هرکدام، روی دکمه‌ی زیر آن بزنید:")

    await callback.message.answer(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


# ======================================================================
# Blocking
# ======================================================================

@router.callback_query(F.data == "admin:block_user")
async def admin_block_user(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("اجازه‌ی این کار را ندارید.", show_alert=True)
        return

    awaiting_state[callback.from_user.id] = "admin_block_target"
    await callback.message.answer(
        "یک پیام از کاربر مورد نظر فوروارد کنید یا آیدی عددی‌اش را بفرستید تا بلاک شود.\n"
        "توجه: بلاک، مستقل از ثبت‌نام است — کاربر مسدودشده حتی اگر بعداً به لیست "
        "کاربران مجاز اضافه شود، همچنان تا زمانی که آنبلاک نشده نمی‌تواند از ربات استفاده کند.\n"
        "برای انصراف /cancel را بفرستید."
    )
    await callback.answer()


@router.callback_query(F.data == "admin:unblock_user")
async def admin_unblock_user(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("اجازه‌ی این کار را ندارید.", show_alert=True)
        return

    awaiting_state[callback.from_user.id] = "admin_unblock_target"
    await callback.message.answer(
        "یک پیام از کاربر مورد نظر فوروارد کنید یا آیدی عددی‌اش را بفرستید تا آنبلاک شود.\n"
        "برای انصراف /cancel را بفرستید."
    )
    await callback.answer()


@router.callback_query(F.data == "admin:list_blocked")
async def admin_list_blocked(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("اجازه‌ی این کار را ندارید.", show_alert=True)
        return

    blocked = blocked_user_store.list_all()

    if not blocked:
        await callback.message.answer("در حال حاضر هیچ کاربر بلاک‌شده‌ای وجود ندارد.")
        await callback.answer()
        return

    lines = ["📋 کاربران بلاک‌شده:\n"]
    rows = []

    for uid_str, info in blocked.items():
        uid = int(uid_str)
        blocked_at = datetime.fromtimestamp(info["blocked_at"]).strftime("%Y-%m-%d %H:%M")
        note = f" — {info['note']}" if info.get("note") else ""
        lines.append(f"• {uid} — بلاک‌شده در: {blocked_at}{note}")

        rows.append([InlineKeyboardButton(
            text=f"✅ آنبلاک {uid}",
            callback_data=f"admin:unblock_confirm:{uid}",
        )])

    await callback.message.answer(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:block_confirm:"))
async def admin_block_confirmed(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("اجازه‌ی این کار را ندارید.", show_alert=True)
        return

    target_id = int(callback.data.split(":")[2])

    if is_blocked(target_id):
        await callback.message.answer(f"کاربر {target_id} از قبل بلاک بود.")
        await callback.answer()
        return

    await blocked_user_store.block(target_id, blocked_by=callback.from_user.id)
    await pending_user_store.remove(target_id)

    await callback.message.answer(f"⛔️ کاربر {target_id} بلاک شد.")
    await callback.answer()


@router.callback_query(F.data.startswith("admin:unblock_confirm:"))
async def admin_unblock_confirmed(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("اجازه‌ی این کار را ندارید.", show_alert=True)
        return

    target_id = int(callback.data.split(":")[2])
    removed = await blocked_user_store.unblock(target_id)

    if removed:
        await callback.message.answer(f"✅ کاربر {target_id} آنبلاک شد.")
    else:
        await callback.message.answer(f"کاربر {target_id} بلاک نبود.")
    await callback.answer()


@router.callback_query(F.data.startswith("admin:toggle_confirm:"))
async def admin_toggle_confirmed(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("اجازه‌ی این کار را ندارید.", show_alert=True)
        return

    _, _, target_id_str, new_active_str = callback.data.split(":")
    target_id = int(target_id_str)
    new_active = bool(int(new_active_str))

    ok = await access_store.set_active(target_id, new_active)
    if not ok:
        await callback.message.answer("این کاربر دیگر در لیست کاربران مجاز نیست.")
    else:
        status_text = "فعال ✅" if new_active else "غیرفعال ⛔️"
        await callback.message.answer(f"وضعیت کاربر {target_id} به «{status_text}» تغییر یافت.")
    await callback.answer()


@router.callback_query(F.data == "admin:toggle_cancel")
async def admin_toggle_cancelled(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("اجازه‌ی این کار را ندارید.", show_alert=True)
        return
    await callback.message.answer("لغو شد؛ وضعیت کاربر تغییری نکرد.")
    await callback.answer()


@router.callback_query(F.data.startswith("admin:delete_confirm:"))
async def admin_delete_confirmed(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("اجازه‌ی این کار را ندارید.", show_alert=True)
        return

    target_id = int(callback.data.split(":")[2])
    display = _user_display(target_id, access_store.get(target_id))

    removed = await access_store.remove(target_id)
    if removed:
        await callback.message.answer(f"🗑 رکورد کاربر {display} به‌طور کامل حذف شد.")
    else:
        await callback.message.answer("این کاربر دیگر در لیست کاربران مجاز نیست.")
    await callback.answer()


@router.callback_query(F.data == "admin:delete_cancel")
async def admin_delete_cancelled(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("اجازه‌ی این کار را ندارید.", show_alert=True)
        return
    await callback.message.answer("لغو شد؛ کاربر حذف نشد.")
    await callback.answer()


@router.callback_query(F.data.startswith("admin:dur:"))
async def admin_duration_selected(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("اجازه‌ی این کار را ندارید.", show_alert=True)
        return

    admin_id = callback.from_user.id
    # "admin:dur:<purpose>:<days>" or "admin:dur:<purpose>:<days>:<target_id>"
    parts = callback.data.split(":")
    purpose = parts[2]
    days = int(parts[3])

    if purpose == "add":
        admin_flow[admin_id] = {"days": days}
        awaiting_state[admin_id] = "admin_add_user"
        await callback.message.answer(
            "یک پیام از کاربر مورد نظر برای من فوروارد کنید (فوروارد باید نویسنده را نشان بدهد)، "
            "یا مستقیماً آیدی عددی کاربر را بفرستید.\n"
            "برای انصراف /cancel را بفرستید."
        )
        await callback.answer()
        return

    if purpose == "renew":
        target_id = int(parts[4])
        expires_at = compute_expiry(days)
        existed = await access_store.update_expiry(target_id, expires_at)
        if not existed:
            await callback.message.answer("این کاربر دیگر در لیست کاربران مجاز نیست.")
        else:
            await callback.message.answer(
                f"✅ تاریخ انقضای کاربر {target_id} به‌روزرسانی شد.\n"
                f"⏳ انقضا: {format_expiry(expires_at)}"
            )
        await callback.answer()
        return


@router.callback_query(F.data == "admin:list_users")
async def admin_list_users(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("اجازه‌ی این کار را ندارید.", show_alert=True)
        return

    users = access_store.list_all()

    if not users:
        await callback.message.answer("هنوز هیچ کاربری اضافه نشده است.")
        await callback.answer()
        return

    lines = ["📋 کاربران مجاز:\n"]
    rows = []
    for uid, info in users.items():
        label = info.get("label") or info.get("name") or info.get("username") or "—"
        active = info.get("active", True)
        status = "✅ فعال" if active and not access_store.is_expired(info) else (
            "⛔️ منقضی" if active else "⛔️ غیرفعال"
        )
        expiry_text = format_expiry(info.get("expires_at"))
        lines.append(f"• {uid} ({label}) — {status} — انقضا: {expiry_text}")

        button_label = label if len(label) <= 24 else label[:23] + "…"
        rows.append([InlineKeyboardButton(
            text=f"⚙️ {button_label} ({uid})",
            callback_data=f"admin:manage:{uid}",
        )])

    lines.append("\nبرای تمدید/غیرفعال‌سازی/حذف سریع، روی هرکدوم از دکمه‌های زیر بزنید:")

    await callback.message.answer(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:manage:"))
async def admin_manage_user(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("اجازه‌ی این کار را ندارید.", show_alert=True)
        return

    target_id = int(callback.data.split(":")[2])
    info = access_store.get(target_id)

    if info is None:
        await callback.message.answer("این کاربر دیگر در لیست کاربران مجاز نیست.")
        await callback.answer()
        return

    display = _user_display(target_id, info)
    status = "✅ فعال" if info["active"] and not access_store.is_expired(info) else (
        "⛔️ منقضی" if info["active"] else "⛔️ غیرفعال"
    )
    expiry_text = format_expiry(info["expires_at"])
    toggle_label = "🚫 غیرفعال کردن" if info["active"] else "✅ فعال کردن"

    text = (
        f"⚙️ مدیریت کاربر {display} ({target_id})\n"
        f"وضعیت: {status}\n"
        f"انقضا: {expiry_text}"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏳ تمدید / تغییر انقضا", callback_data=f"admin:manage_renew:{target_id}")],
        [InlineKeyboardButton(text=toggle_label, callback_data=f"admin:manage_toggle:{target_id}")],
        [InlineKeyboardButton(text="🗑 حذف کامل", callback_data=f"admin:manage_delete:{target_id}")],
        [InlineKeyboardButton(text="🔙 بازگشت به لیست", callback_data="admin:list_users")],
    ])
    await callback.message.answer(text, reply_markup=keyboard)
    await callback.answer()


@router.callback_query(F.data.startswith("admin:manage_renew:"))
async def admin_manage_renew(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("اجازه‌ی این کار را ندارید.", show_alert=True)
        return

    target_id = int(callback.data.split(":")[2])
    if access_store.get(target_id) is None:
        await callback.message.answer("این کاربر دیگر در لیست کاربران مجاز نیست.")
        await callback.answer()
        return

    await callback.message.answer(
        "مدت اعتبار جدید را انتخاب کنید:",
        reply_markup=duration_keyboard("renew", target_id),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:manage_toggle:"))
async def admin_manage_toggle(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("اجازه‌ی این کار را ندارید.", show_alert=True)
        return

    target_id = int(callback.data.split(":")[2])
    existing = access_store.get(target_id)

    if existing is None:
        await callback.message.answer("این کاربر دیگر در لیست کاربران مجاز نیست.")
        await callback.answer()
        return

    new_active = not existing.get("active", True)
    display = _user_display(target_id, existing)
    action_text = "غیرفعال" if not new_active else "فعال"

    await callback.message.answer(
        f"آیا مطمئنید می‌خواهید کاربر {display} را {action_text} کنید؟",
        reply_markup=confirm_keyboard(
            confirm_data=f"admin:toggle_confirm:{target_id}:{1 if new_active else 0}",
            cancel_data="admin:toggle_cancel",
        ),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:manage_delete:"))
async def admin_manage_delete(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("اجازه‌ی این کار را ندارید.", show_alert=True)
        return

    target_id = int(callback.data.split(":")[2])
    existing = access_store.get(target_id)

    if existing is None:
        await callback.message.answer("این کاربر دیگر در لیست کاربران مجاز نیست.")
        await callback.answer()
        return

    display = _user_display(target_id, existing)
    await callback.message.answer(
        f"⚠️ آیا مطمئنید می‌خواهید رکورد کاربر {display} به‌طور کامل حذف شود؟\n"
        "این کار غیرقابل بازگشت است.",
        reply_markup=confirm_keyboard(
            confirm_data=f"admin:delete_confirm:{target_id}",
            cancel_data="admin:delete_cancel",
            confirm_label="🗑 بله، حذف کن",
        ),
    )
    await callback.answer()


def _broadcast_target_label(target: str) -> str:
    return {
        "pending": "ثبت‌نام‌نشده",
        "registered": "ثبت‌نام‌شده",
        "all": "همه‌ی کاربران",
    }.get(target, target)


def _broadcast_recipients(target: str) -> dict[str, dict]:
    """uid (str) -> info dict, for whichever group `target` names.
    Blocked users are always excluded, regardless of target — a block
    means "never contact this person again", broadcasts included."""

    if target == "pending":
        recipients = dict(pending_user_store.list_all())
    elif target == "registered":
        recipients = dict(access_store.list_all())
    elif target == "all":
        recipients = dict(access_store.list_all())
        recipients.update(pending_user_store.list_all())
    else:
        recipients = {}

    return {
        uid: info
        for uid, info in recipients.items()
        if not blocked_user_store.is_blocked(int(uid))
    }


@router.callback_query(F.data.startswith("admin:broadcast_start:"))
async def admin_broadcast_start(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("اجازه‌ی این کار را ندارید.", show_alert=True)
        return

    target = callback.data.split(":")[2]
    label = _broadcast_target_label(target)
    count = len(_broadcast_recipients(target))

    if count == 0:
        await callback.message.answer(f"در حال حاضر هیچ کاربر «{label}»ای برای ارسال پیام وجود ندارد.")
        await callback.answer()
        return

    admin_flow[callback.from_user.id] = {"broadcast_target": target}
    awaiting_state[callback.from_user.id] = "admin_broadcast_text"
    await callback.message.answer(
        f"متن پیامی که می‌خواهید به {count} کاربر «{label}» ارسال شود را بفرستید.\n"
        "برای انصراف /cancel را بفرستید."
    )
    await callback.answer()


@router.callback_query(F.data == "admin:broadcast_confirm")
async def admin_broadcast_confirm(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("اجازه‌ی این کار را ندارید.", show_alert=True)
        return

    flow = admin_flow.pop(callback.from_user.id, {})
    text = flow.get("broadcast_text")
    target = flow.get("broadcast_target", "pending")

    if not text:
        await callback.message.answer("متنی برای ارسال پیدا نشد؛ دوباره تلاش کنید.")
        await callback.answer()
        return

    recipients = _broadcast_recipients(target)
    sent_count = 0
    failed_count = 0

    for uid_str in recipients:
        try:
            await bot.send_message(int(uid_str), text)
            sent_count += 1
        except Exception:
            failed_count += 1
            logger.warning("Could not deliver broadcast message to user %s", uid_str)

    await callback.message.answer(
        f"✅ پیام همگانی به «{_broadcast_target_label(target)}» ارسال شد.\n"
        f"موفق: {sent_count}\n"
        f"ناموفق: {failed_count} (احتمالاً ربات را بلاک کرده‌اند)"
    )
    await callback.answer()


@router.callback_query(F.data == "admin:broadcast_cancel")
async def admin_broadcast_cancelled(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("اجازه‌ی این کار را ندارید.", show_alert=True)
        return

    admin_flow.pop(callback.from_user.id, None)
    await callback.message.answer("لغو شد؛ پیامی ارسال نشد.")
    await callback.answer()


# ======================================================================
# Promotional post — sent automatically after a job completes (see
# handlers/bridge.py's DONE handling and CLAUDE.md's change log)
# ======================================================================

@router.callback_query(F.data == "admin:promo_menu")
async def admin_promo_menu(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("اجازه‌ی این کار را ندارید.", show_alert=True)
        return

    post = promo_post_store.get()
    has_post = post is not None
    enabled = bool(post and post["enabled"])

    if not has_post:
        status = "هنوز پستی تنظیم نشده است."
    elif enabled:
        status = "✅ فعال — این پست بعد از اتمام هر کار برای همان کاربر ارسال می‌شود."
    else:
        status = "⏸ غیرفعال — پست تنظیم شده اما در حال حاضر ارسال نمی‌شود."

    await callback.message.answer(
        f"📝 پست تبلیغاتی پس از اتمام کار\n\n{status}",
        reply_markup=promo_post_menu_keyboard(has_post, enabled),
    )
    await callback.answer()


@router.callback_query(F.data == "admin:promo_set")
async def admin_promo_set(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("اجازه‌ی این کار را ندارید.", show_alert=True)
        return

    awaiting_state[callback.from_user.id] = "admin_promo_post"
    await callback.message.answer(
        "هر پیامی (متن، عکس، ویدیو یا فایل همراه با کپشن) که می‌خواهید بعد از "
        "اتمام کار برای کاربران ارسال شود را همینجا بفرستید.\n"
        "برای انصراف /cancel را بفرستید."
    )
    await callback.answer()


@router.callback_query(F.data == "admin:promo_preview")
async def admin_promo_preview(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("اجازه‌ی این کار را ندارید.", show_alert=True)
        return

    post = promo_post_store.get()

    if post is None:
        await callback.message.answer("هنوز پستی تنظیم نشده است.")
        await callback.answer()
        return

    try:
        await bot.copy_message(
            callback.from_user.id,
            from_chat_id=post["source_chat_id"],
            message_id=post["source_message_id"],
        )
    except Exception:
        logger.exception("Failed to preview the promo post for admin %s", callback.from_user.id)
        await callback.message.answer("پیش‌نمایش ناموفق بود — پیام اصلی ممکن است حذف شده باشد.")

    await callback.answer()


@router.callback_query(F.data == "admin:promo_toggle")
async def admin_promo_toggle(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("اجازه‌ی این کار را ندارید.", show_alert=True)
        return

    post = promo_post_store.get()

    if post is None:
        await callback.message.answer("هنوز پستی تنظیم نشده است.")
        await callback.answer()
        return

    new_enabled = not post["enabled"]
    await promo_post_store.set_enabled(new_enabled)

    status = "✅ فعال شد" if new_enabled else "⏸ غیرفعال شد"
    await callback.message.answer(f"ارسال خودکار پست تبلیغاتی {status}.")
    await callback.answer()


@router.callback_query(F.data == "admin:promo_delete")
async def admin_promo_delete(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("اجازه‌ی این کار را ندارید.", show_alert=True)
        return

    removed = await promo_post_store.clear()

    if removed:
        await callback.message.answer("🗑 پست تبلیغاتی حذف شد.")
    else:
        await callback.message.answer("پستی برای حذف وجود نداشت.")
    await callback.answer()


@router.callback_query(F.data == "admin:stats")
async def admin_stats(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("اجازه‌ی این کار را ندارید.", show_alert=True)
        return

    registered = access_store.list_all()
    pending = pending_user_store.list_all()

    active_count = 0
    expired_count = 0
    disabled_count = 0

    for info in registered.values():
        if not info.get("active", True):
            disabled_count += 1
        elif access_store.is_expired(info):
            expired_count += 1
        else:
            active_count += 1

    total_registered = len(registered)
    total_pending = len(pending)
    total_seen = total_registered + total_pending

    if total_seen > 0:
        conversion_rate = f"{(total_registered / total_seen) * 100:.1f}٪"
    else:
        conversion_rate = "—"

    text = (
        "📊 آمار کاربران\n\n"
        f"✅ کاربران مجاز (ثبت‌شده): {total_registered}\n"
        f"　　فعال: {active_count}\n"
        f"　　منقضی: {expired_count}\n"
        f"　　غیرفعال: {disabled_count}\n\n"
        f"🕓 کاربران ثبت‌نام‌نشده (pending): {total_pending}\n\n"
        f"📈 نرخ تبدیل (ثبت‌شده از کل کسانی که ربات را دیده‌اند): {conversion_rate}"
    )

    await callback.message.answer(text)
    await callback.answer()


# ======================================================================
# Awaited-input states (admin_*) — called from bot.py's handle_awaited_input
# ======================================================================

async def handle_admin_awaited_input(message: Message, state: str) -> bool:
    """Handles every state prefixed "admin_" — the free-text follow-ups to
    the callbacks above (identifying a target user, typing a name/
    username by hand). Returns True if the state was recognized and
    handled (whether or not it advanced — an error message still counts as
    "handled", the caller shouldn't fall through to treating the message
    as a new file upload), False if this state doesn't belong to the admin
    domain at all."""

    user_id = message.from_user.id

    if state == "admin_add_user":

        if not is_admin(user_id):
            awaiting_state.pop(user_id, None)
            admin_flow.pop(user_id, None)
            return False

        target_id, name, username = _extract_target_from_message(message)

        if target_id is None:
            await message.answer(
                "نتونستم کاربر را شناسایی کنم. یک پیام از او فوروارد کنید "
                "(با نمایش نویسنده) یا آیدی عددی‌اش را بفرستید."
            )
            return True

        flow = admin_flow.setdefault(user_id, {})
        days = flow.get("days", 0)

        if name or username:
            # Real forward — Telegram already gave us name/username, no need
            # to ask the admin to type them in by hand.
            expires_at = compute_expiry(days)
            await access_store.add(
                target_id,
                label=(name or username),
                name=name,
                username=username,
                added_by=user_id,
                expires_at=expires_at,
            )
            await pending_user_store.remove(target_id)
            awaiting_state.pop(user_id, None)
            admin_flow.pop(user_id, None)
            await message.answer(
                f"✅ کاربر {name or username or target_id} به لیست مجاز اضافه شد.\n"
                f"⏳ انقضا: {format_expiry(expires_at)}"
            )
            return True

        # Manually-typed numeric id — Telegram gave us nothing about this
        # user, so let the admin attach a name/username by hand (either can
        # be left blank).
        flow["target_id"] = target_id
        awaiting_state[user_id] = "admin_add_name"
        await message.answer(
            "نام کاربر را وارد کنید (اختیاری؛ برای رد شدن «-» بفرستید):"
        )
        return True

    if state == "admin_add_name":

        if not is_admin(user_id):
            awaiting_state.pop(user_id, None)
            admin_flow.pop(user_id, None)
            return False

        if not message.text:
            await message.answer("لطفاً نام را به‌صورت متن بفرستید، یا «-» برای رد شدن.")
            return True

        text = message.text.strip()
        admin_flow.setdefault(user_id, {})["name"] = "" if text in ("-", "خالی") else text
        awaiting_state[user_id] = "admin_add_username"
        await message.answer(
            "یوزرنیم کاربر را وارد کنید (اختیاری؛ برای رد شدن «-» بفرستید):"
        )
        return True

    if state == "admin_add_username":

        if not is_admin(user_id):
            awaiting_state.pop(user_id, None)
            admin_flow.pop(user_id, None)
            return False

        if not message.text:
            await message.answer("لطفاً یوزرنیم را به‌صورت متن بفرستید، یا «-» برای رد شدن.")
            return True

        text = message.text.strip()
        username = "" if text in ("-", "خالی") else text.lstrip("@")

        flow = admin_flow.pop(user_id, {})
        target_id = flow.get("target_id")
        name = flow.get("name", "")
        days = flow.get("days", 0)
        expires_at = compute_expiry(days)

        await access_store.add(
            target_id,
            label=(name or username or str(target_id)),
            name=name,
            username=username,
            added_by=user_id,
            expires_at=expires_at,
        )
        await pending_user_store.remove(target_id)
        awaiting_state.pop(user_id, None)
        await message.answer(
            f"✅ کاربر {name or username or target_id} به لیست مجاز اضافه شد.\n"
            f"⏳ انقضا: {format_expiry(expires_at)}"
        )
        return True

    if state == "admin_renew_target":

        if not is_admin(user_id):
            awaiting_state.pop(user_id, None)
            return False

        target_id, _, _ = _extract_target_from_message(message)

        if target_id is None:
            await message.answer(
                "نتونستم کاربر را شناسایی کنم. یک پیام از او فوروارد کنید "
                "یا آیدی عددی‌اش را بفرستید."
            )
            return True

        if access_store.get(target_id) is None:
            awaiting_state.pop(user_id, None)
            await message.answer("این کاربر در لیست کاربران مجاز نیست.")
            return True

        awaiting_state.pop(user_id, None)
        await message.answer(
            "مدت اعتبار جدید را انتخاب کنید:",
            reply_markup=duration_keyboard("renew", target_id),
        )
        return True

    if state == "admin_toggle_target":

        if not is_admin(user_id):
            awaiting_state.pop(user_id, None)
            return False

        target_id, _, _ = _extract_target_from_message(message)

        if target_id is None:
            await message.answer(
                "نتونستم کاربر را شناسایی کنم. یک پیام از او فوروارد کنید "
                "یا آیدی عددی‌اش را بفرستید."
            )
            return True

        existing = access_store.get(target_id)
        if existing is None:
            awaiting_state.pop(user_id, None)
            await message.answer("این کاربر در لیست کاربران مجاز نیست.")
            return True

        awaiting_state.pop(user_id, None)
        new_active = not existing.get("active", True)
        display = _user_display(target_id, existing)
        action_text = "غیرفعال" if not new_active else "فعال"
        await message.answer(
            f"آیا مطمئنید می‌خواهید کاربر {display} را {action_text} کنید؟",
            reply_markup=confirm_keyboard(
                confirm_data=f"admin:toggle_confirm:{target_id}:{1 if new_active else 0}",
                cancel_data="admin:toggle_cancel",
            ),
        )
        return True

    if state == "admin_delete_target":

        if not is_admin(user_id):
            awaiting_state.pop(user_id, None)
            return False

        target_id, _, _ = _extract_target_from_message(message)

        if target_id is None:
            await message.answer(
                "نتونستم کاربر را شناسایی کنم. یک پیام از او فوروارد کنید "
                "یا آیدی عددی‌اش را بفرستید."
            )
            return True

        existing = access_store.get(target_id)
        if existing is None:
            awaiting_state.pop(user_id, None)
            await message.answer("این کاربر در لیست کاربران مجاز نیست.")
            return True

        awaiting_state.pop(user_id, None)
        display = _user_display(target_id, existing)
        await message.answer(
            f"⚠️ آیا مطمئنید می‌خواهید رکورد کاربر {display} به‌طور کامل حذف شود؟\n"
            "این کار غیرقابل بازگشت است.",
            reply_markup=confirm_keyboard(
                confirm_data=f"admin:delete_confirm:{target_id}",
                cancel_data="admin:delete_cancel",
                confirm_label="🗑 بله، حذف کن",
            ),
        )
        return True

    if state == "admin_block_target":

        if not is_admin(user_id):
            awaiting_state.pop(user_id, None)
            return False

        target_id, _, _ = _extract_target_from_message(message)

        if target_id is None:
            await message.answer(
                "نتونستم کاربر را شناسایی کنم. یک پیام از او فوروارد کنید "
                "یا آیدی عددی‌اش را بفرستید."
            )
            return True

        awaiting_state.pop(user_id, None)

        if is_blocked(target_id):
            await message.answer(f"کاربر {target_id} از قبل بلاک بود.")
            return True

        display = _user_display(target_id, access_store.get(target_id))
        await message.answer(
            f"آیا مطمئنید می‌خواهید کاربر {display} ({target_id}) را بلاک کنید؟",
            reply_markup=confirm_keyboard(
                confirm_data=f"admin:block_confirm:{target_id}",
                cancel_data="admin:toggle_cancel",
                confirm_label="⛔️ بله، بلاک کن",
            ),
        )
        return True

    if state == "admin_unblock_target":

        if not is_admin(user_id):
            awaiting_state.pop(user_id, None)
            return False

        target_id, _, _ = _extract_target_from_message(message)

        if target_id is None:
            await message.answer(
                "نتونستم کاربر را شناسایی کنم. یک پیام از او فوروارد کنید "
                "یا آیدی عددی‌اش را بفرستید."
            )
            return True

        awaiting_state.pop(user_id, None)

        if not is_blocked(target_id):
            await message.answer(f"کاربر {target_id} بلاک نبود.")
            return True

        await message.answer(
            f"آیا مطمئنید می‌خواهید کاربر {target_id} را آنبلاک کنید؟",
            reply_markup=confirm_keyboard(
                confirm_data=f"admin:unblock_confirm:{target_id}",
                cancel_data="admin:toggle_cancel",
                confirm_label="✅ بله، آنبلاک کن",
            ),
        )
        return True

    if state == "admin_broadcast_text":

        if not is_admin(user_id):
            awaiting_state.pop(user_id, None)
            admin_flow.pop(user_id, None)
            return False

        if not message.text:
            await message.answer("لطفاً متن پیام را به‌صورت متن بفرستید.")
            return True

        flow = admin_flow.get(user_id, {})
        target = flow.get("broadcast_target", "pending")
        count = len(_broadcast_recipients(target))

        admin_flow[user_id] = {"broadcast_text": message.text, "broadcast_target": target}
        awaiting_state.pop(user_id, None)

        await message.answer(
            f"این پیام به {count} کاربر «{_broadcast_target_label(target)}» ارسال خواهد شد:\n\n"
            f"{message.text}\n\n"
            "آیا مطمئنید؟",
            reply_markup=confirm_keyboard(
                confirm_data="admin:broadcast_confirm",
                cancel_data="admin:broadcast_cancel",
                confirm_label="📢 بله، ارسال کن",
            ),
        )
        return True

    if state == "admin_promo_post":

        if not is_admin(user_id):
            awaiting_state.pop(user_id, None)
            return False

        awaiting_state.pop(user_id, None)

        await promo_post_store.set_post(
            message.chat.id,
            message.message_id,
            set_by=user_id,
        )

        await message.answer(
            "✅ پست تبلیغاتی ذخیره و فعال شد؛ از این پس بعد از اتمام هر کار، "
            "این پست برای همان کاربر ارسال می‌شود."
        )
        return True

    return False
