"""
The bot's core entry points: `/start`, `/cancel`, and the catch-all for
every other private message (new files, plain text, photos, password
replies). Extracted out of bot.py (phase D, step 4 — the last handler
module — of the module split; see CLAUDE.md's change log).

Two separate Routers on purpose: `router` holds the specific-`Command`
handlers (`/start`, `/cancel`); `catchall_router` holds
`handle_private_message`'s bare `F.chat.type == "private"` filter, which
matches ANY private message with no further discrimination. Both must be
registered via `dp.include_router(...)` in `bot.py`, with `router` (and
every other specific-filter router — admin/settings/files/photo) *before*
`catchall_router` — see the phase-7d change-log entry for the real bug
that taught us this the hard way: aiogram checks a router's own handlers
before descending into any sub-router, so a broad catch-all decorated
directly on `dp` (or included before a more specific router) will win
every time, regardless of when `dp.include_router(...)` was called.
"""
from uuid import uuid4

import time

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message

from core.protocol import Protocol
from handlers.admin import handle_admin_awaited_input
from handlers.files import handle_file_awaited_input
from handlers.photo import handle_incoming_photo
from handlers.settings import handle_settings_awaited_input
from keyboards.files import options_keyboard, quality_keyboard, url_mode_keyboard
from models.pending_file import PendingFile
from services.settings_store import settings_store
from services.telegram import telegram_service
from state import admin_flow, awaiting_state, pending_files, pending_passwords, user_submission_times
from utils.access_control import track_pending_user_if_needed
from utils.filetype import FileTypeDetector
from utils.permissions import (
    check_tier_submission,
    max_file_size_for_user,
)
from utils.url_validation import (
    REASON_BAD_SCHEME,
    REASON_NO_HOST,
    REASON_PRIVATE_ADDRESS,
    REASON_UNRESOLVABLE,
    extract_url,
    filename_from_url,
    validate_url,
)

router = Router(name="core")
catchall_router = Router(name="catchall")


def _rate_limit_text(max_files: int, window_minutes: int) -> str:
    """Persian rejection message for a rate-limited submission."""

    return (
        f"⛔ شما در {window_minutes} دقیقه گذشته بیش از حد مجاز "
        f"({max_files} فایل) ارسال کرده‌اید. لطفاً کمی صبر کنید و "
        "دوباره تلاش کنید."
    )


def check_submission_rate_limit(user_id: int) -> tuple[bool, int, int]:
    """Tier-aware submission gate. Returns (allowed, max_files,
    window_minutes) — the limits values are the tier's actually-applied
    ones, so the caller can build an accurate rejection message. Records
    the submission when allowed. Admins bypass rate limiting entirely,
    paid users use the paid limits (disabled by default), trial users
    get the trial limits."""

    history = user_submission_times.setdefault(user_id, [])

    now = time.time()

    allowed, (max_files, window_minutes) = check_tier_submission(
        user_id,
        history,
        now,
    )

    # Keep the dict from growing without bound: a user with no timestamps
    # left after pruning doesn't need an entry.
    if not history:
        user_submission_times.pop(user_id, None)

    return allowed, max_files, window_minutes


def _url_error_text(reason: str) -> str:
    """Persian feedback for a rejected URL submission."""

    if reason == REASON_BAD_SCHEME:
        return "❌ فقط لینک‌های http و https پشتیبانی می‌شوند."

    if reason == REASON_PRIVATE_ADDRESS:
        return (
            "❌ به دلایل امنیتی، دانلود از آدرس‌های داخلی یا شبکه‌ی خصوصی "
            "مجاز نیست."
        )

    if reason == REASON_UNRESOLVABLE:
        return "❌ دامنه‌ی لینک پیدا نشد. لینک را بررسی کنید."

    return "❌ لینک معتبر نیست. یک لینک مستقیم فایل بفرستید."


def _pending_options_from_defaults(defaults: dict) -> dict:
    """The per-file options dict seeded from the user's /settings
    defaults — shared by the upload path and the URL-upload path so both
    create identical PendingFile options."""

    return {
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
    }


async def handle_url_submission(message: Message, url: str) -> None:
    """The URL-upload entry point: a private, authorized user sent a
    message that is just an http(s) link. Validated here (scheme + SSRF
    guard + rate limit + supported type), then registered in
    pending_files exactly like an uploaded file — the rest of the flow
    (quality/options/target/confirm) and the worker pipeline are shared
    unchanged; only "where the bytes come from" differs."""

    user_id = message.from_user.id

    ok, reason = validate_url(url)

    if not ok:
        await message.answer(_url_error_text(reason))
        return

    # Same cap as direct uploads — a URL isn't a way around rate limiting.
    # Tier-aware: admins bypass, paid users get the paid limits, trial
    # users the trial ones.
    allowed, max_files, window_minutes = check_submission_rate_limit(user_id)

    if not allowed:
        await message.answer(_rate_limit_text(max_files, window_minutes))
        return

    filename = filename_from_url(url)

    file_type = FileTypeDetector.detect("", filename)

    if file_type == "UNKNOWN":
        await message.answer(
            "❌ نوع فایل از روی لینک قابل تشخیص نیست یا پشتیبانی نمی‌شود."
        )
        return

    defaults = settings_store.get(user_id)

    pid = uuid4().hex[:10]

    pending_files[pid] = PendingFile(
        user_id=user_id,
        chat_id=message.chat.id,
        file_name=filename,
        file_type=file_type,
        source_message=message,
        url=url,
        options=_pending_options_from_defaults(defaults),
    )

    # The per-file options flow only applies to "normal processing" —
    # first ask which behavior the user wants for this URL. The choice
    # screen is handled by handlers/files.py's `urlmode:` callback:
    # "direct" delivers the downloaded file as-is (no processing), while
    # "process" continues into the exact same flow an uploaded file gets.
    await message.answer(
        _URL_MODE_TEXT,
        reply_markup=url_mode_keyboard(pid),
    )


_URL_MODE_TEXT = (
    "🔗 فایل از این لینک دانلود می‌شود. بعد از دانلود چه اتفاقی برایش بیفتد؟\n\n"
    "⬆️ ارسال مستقیم: فایل همین‌طور که هست برای شما ارسال می‌شود — بدون تبدیل، "
    "واترمارک یا هیچ تغییری (سریع‌ترین حالت).\n\n"
    "⚙️ پردازش کامل: فایل مثل یک فایل معمولی وارد روند پردازش می‌شود — انتخاب کیفیت، "
    "واترمارک، تامبنیل، استخراج آرشیو و بقیهٔ تنظیمات."
)


async def handle_awaited_input(message: Message, state: str) -> bool:
    """Composes the per-domain awaited-input dispatchers by state prefix.
    Each domain (admin/settings/files) owns its own states entirely; this
    is just the routing table between them, kept here (rather than in
    bot.py) so this module doesn't need to import bot.py back — bot.py
    imports THIS function instead, for backward compatibility with
    existing direct-call tests."""

    if state.startswith("admin_"):
        return await handle_admin_awaited_input(message, state)

    if state.startswith("settings_"):
        return await handle_settings_awaited_input(message, state)

    if state.startswith("file:"):
        return await handle_file_awaited_input(message, state)

    return False


# ======================================================================
# Commands
# ======================================================================

@router.message(Command("start"), F.chat.type == "private")
async def start(message: Message):
    user_id = message.from_user.id

    # Track anyone interacting with the bot without being in access_store
    # yet, and notify the admin (once per person) — see
    # track_pending_user_if_needed's docstring for why this same call also
    # has to happen in handle_private_message and settings_command, not
    # just here.
    await track_pending_user_if_needed(message)

    # Access tiers: unauthorized users are no longer hard-blocked — they
    # use the bot as trial users (see utils/permissions.py). The pending
    # tracking above still notifies the admin so they can upgrade the
    # account to paid.
    await message.answer(
        "سلام! فایل خود را ارسال کنید.\n"
        "برای تنظیم مقادیر پیش‌فرض از /settings استفاده کنید."
    )


@router.message(Command("cancel"), F.chat.type == "private")
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
# Everything else in a private chat
# ======================================================================

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

    # Same tracking/notification as /start and /settings — a user can just
    # as easily send a file directly as their very first interaction
    # (this is exactly what happens when testing with qa-userbot, which
    # sends files straight away without ever sending /start first).
    await track_pending_user_if_needed(message)

    # NOTE: no hard access gate here anymore — unauthorized users proceed
    # as trial users, subject to the trial limits (rate limit below and
    # the tier file-size cap in the size check).

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

        # URL-upload mode: a text message that is just an http(s) link.
        # Checked AFTER awaited-input/password handling above, so it can
        # never hijack those free-text flows.
        url = extract_url(message.text or "")

        if url:
            await handle_url_submission(message, url)

        return

    # Per-user rate limiting: reject (with a friendly Persian message)
    # when this user has already submitted their tier's cap of files
    # within the configured window. Tier-aware — admins bypass, paid
    # users get the paid limits, trial users the trial ones. Applied only
    # to actual new submissions — password replies and awaited-input
    # flows above are never blocked.
    allowed, max_files, window_minutes = check_submission_rate_limit(user_id)

    if not allowed:
        await message.answer(_rate_limit_text(max_files, window_minutes))
        return

    file_name = getattr(file, "file_name", None) or f"file_{message.message_id}"
    mime_type = getattr(file, "mime_type", "") or ""

    file_type = FileTypeDetector.detect(mime_type, file_name)

    if file_type == "UNKNOWN":
        await message.answer("نوع فایل پشتیبانی نمی‌شود.")
        return

    # Tier-aware file-size cap: Telegram knows the declared size before
    # anything is downloaded, so trial users over their cap get rejected
    # right here instead of after the worker picked the job up. The
    # worker re-checks against the same tier limit as belt-and-braces.
    file_size = getattr(file, "size", 0) or 0

    size_limit = max_file_size_for_user(user_id)

    if file_size > size_limit:
        await message.answer(
            f"حجم فایل ({file_size / (1024 * 1024 * 1024):.1f} گیگابایت) بیشتر از "
            f"حد مجاز حساب شما ({size_limit / (1024 * 1024 * 1024):.1f} گیگابایت) است."
        )
        return

    defaults = settings_store.get(user_id)

    pid = uuid4().hex[:10]

    pending_files[pid] = PendingFile(
        user_id=user_id,
        chat_id=message.chat.id,
        file_name=file_name,
        file_type=file_type,
        source_message=message,
        options=_pending_options_from_defaults(defaults),
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
