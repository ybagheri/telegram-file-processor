"""
The main per-file processing flow: quality pick -> options -> target ->
confirm/upload. Extracted out of bot.py (phase D, step 3 of the module
split — see CLAUDE.md's change log) as an aiogram 3 Router, registered
from bot.py via `dp.include_router(router)`.

`handle_file_awaited_input()` is called from bot.py's top-level
`handle_awaited_input()` dispatcher for any state starting with "file:" —
not a `@router` handler itself, same pattern as the admin/settings
awaited-input dispatch functions.
"""
from aiogram import F, Router
from aiogram.types import CallbackQuery, Message

from config import Paths, Telegram
from core.constants import MessageType
from keyboards.files import options_keyboard, quality_keyboard, target_keyboard
from models.pending_file import PendingFile
from services.target_resolver import resolve_target
from services.telegram import telegram_service
from state import awaiting_state, pending_files
from utils.permissions import get_account_tier

router = Router(name="files")
bot = telegram_service.bot


# ======================================================================
# Per-file quality / options callbacks
# ======================================================================

@router.callback_query(F.data.startswith("q:"))
async def quality_pick(callback: CallbackQuery):
    _, pid, value = callback.data.split(":")
    pending = pending_files.get(pid)

    if not pending:
        await callback.answer("این درخواست منقضی شده است.", show_alert=True)
        return

    pending.options["quality"] = value

    await callback.message.edit_text(
        "تنظیمات این فایل را بررسی و در صورت نیاز تغییر دهید:",
        reply_markup=options_keyboard(pid, pending_files),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("o:"))
async def options_action(callback: CallbackQuery):
    _, pid, action = callback.data.split(":", 2)
    pending = pending_files.get(pid)

    if not pending:
        await callback.answer("این درخواست منقضی شده است.", show_alert=True)
        return

    if action == "upload_as":
        pending.options["upload_as"] = "document" if pending.options.get("upload_as") == "video" else "video"
        await callback.message.edit_reply_markup(reply_markup=options_keyboard(pid, pending_files))
        await callback.answer()
        return

    if action == "watermark":
        pending.options["watermark"] = not pending.options.get("watermark")
        await callback.message.edit_reply_markup(reply_markup=options_keyboard(pid, pending_files))
        await callback.answer()
        return

    if action == "make_collage":
        pending.options["make_collage"] = not pending.options.get("make_collage")
        await callback.message.edit_reply_markup(reply_markup=options_keyboard(pid, pending_files))
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

    if action == "thumb_count":
        awaiting_state[pending.user_id] = f"file:{pid}:thumb_count"
        await callback.message.answer(
            "چند تا عکس می‌خواهید؟ یک عدد بفرستید (مثلاً 6)، یا «خودکار» برای انتخاب خودکار بر اساس طول ویدیو."
        )
        await callback.answer()
        return

    if action == "thumb_columns":
        awaiting_state[pending.user_id] = f"file:{pid}:thumb_columns"
        await callback.message.answer(
            "عکس‌ها در چند ستون چیده شوند؟ یک عدد بفرستید (مثلاً 3)، یا «خودکار» برای چیدمان نزدیک به مربع."
        )
        await callback.answer()
        return

    if action == "title":
        awaiting_state[pending.user_id] = f"file:{pid}:title"
        await callback.message.answer("عنوان و خواننده را به‌صورت «عنوان | خواننده» بفرستید:")
        await callback.answer()
        return

    if action == "multipart":
        awaiting_state[pending.user_id] = f"file:{pid}:parts_count"
        await callback.message.answer(
            "این آرشیو چند بخش/تکه است؟ یک عدد بفرستید (مثلاً 5).\n"
            "بعد از اعلام تعداد، بخش‌هایی که همین الان فرستادید به‌عنوان بخش ۱ ثبت می‌شود "
            "و باید بقیه‌ی بخش‌ها را یکی‌یکی، به‌همین ترتیب که خودتان مرتب کرده‌اید، بفرستید."
        )
        await callback.answer()
        return

    if action == "archive_password":
        awaiting_state[pending.user_id] = f"file:{pid}:archive_password"
        await callback.message.answer(
            "رمز این آرشیو را بفرستید.\n"
            "اگر رمز را نمی‌دانید یا فراموش کردید، همین‌جا رد شوید — اگر لازم باشد، ربات خودش موقع پردازش رمز را از شما می‌خواهد."
        )
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
        if awaiting_state.get(pending.user_id, "").startswith(f"file:{pid}:"):
            awaiting_state.pop(pending.user_id, None)
        await callback.message.edit_text("❌ لغو شد.")
        await callback.answer()
        return


@router.callback_query(F.data == "nothing")
async def noop_callback(callback: CallbackQuery):
    await callback.answer()


@router.callback_query(F.data.startswith("t:"))
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
            reply_markup=options_keyboard(pid, pending_files),
        )
        await callback.answer()
        return

    if choice == "new":
        awaiting_state[pending.user_id] = f"file:{pid}:target"
        await callback.message.answer(
            "یک پیام از گروه/کانال مقصد برای من فوروارد کنید، @username یا آیدی عددی آن را بفرستید.\n"
            "⚠️ ربات باید عضو آن گروه/کانال (و دسترسی ارسال) داشته باشد.\n"
            "برای انصراف /cancel را بفرستید."
        )
        await callback.answer()
        return


@router.callback_query(F.data.startswith("urlmode:"))
async def url_mode_pick(callback: CallbackQuery):
    """The direct-vs-process choice shown right after a URL submission.
    "direct" skips the options flow entirely (worker delivers the file
    untouched); "process" continues with the same quality/options/target
    flow an uploaded file gets."""

    _, pid, choice = callback.data.split(":")
    pending = pending_files.get(pid)

    if not pending:
        await callback.answer("این درخواست منقضی شده است.", show_alert=True)
        return

    if choice == "direct":

        pending.direct_upload = True

        await finalize_job(callback, pending, pid)
        return

    if choice == "process":

        if pending.file_type == "VIDEO":
            await callback.message.edit_text(
                "🔻 کیفیت / فرمت خروجی را انتخاب کنید 🔻",
                reply_markup=quality_keyboard(pid),
            )
        else:
            await callback.message.edit_text(
                "تنظیمات این فایل را بررسی و در صورت نیاز تغییر دهید:",
                reply_markup=options_keyboard(pid, pending_files),
            )

        await callback.answer()
        return

    if choice == "cancel":

        pending_files.pop(pid, None)
        if awaiting_state.get(pending.user_id, "").startswith(f"file:{pid}:"):
            awaiting_state.pop(pending.user_id, None)

        await callback.message.edit_text("❌ لغو شد.")
        await callback.answer()


async def finalize_job(callback: CallbackQuery, pending: PendingFile, pid: str):

    if pending.url:

        # URL-upload mode: there is no Telegram media to forward into the
        # bridge — the worker streams the bytes from the URL itself.
        # direct_upload marks the "send it as-is" choice (no processing
        # pipeline on the worker side).
        job_data = {
            "type": MessageType.JOB.value,
            "user_id": pending.user_id,
            "url": pending.url,
            "file_type": pending.file_type,
            "file_name": pending.file_name,
            "original_chat_id": pending.chat_id,
            "options": pending.options,
            "account_tier": get_account_tier(pending.user_id).value,
        }

        if pending.direct_upload:
            job_data["direct_upload"] = True

    elif pending.is_multipart:

        job_data = {
            "type": MessageType.JOB.value,
            "user_id": pending.user_id,
            "message_id": pending.part_message_ids[0],
            "part_message_ids": pending.part_message_ids,
            "file_type": pending.file_type,
            "file_name": pending.file_name,
            "original_chat_id": pending.chat_id,
            "options": pending.options,
            "account_tier": get_account_tier(pending.user_id).value,
        }

    else:

        forwarded = await pending.source_message.forward(Telegram.GROUP_ID)

        job_data = {
            "type": MessageType.JOB.value,
            "user_id": pending.user_id,
            "message_id": forwarded.message_id,
            "file_type": pending.file_type,
            "file_name": pending.file_name,
            "original_chat_id": pending.chat_id,
            "options": pending.options,
            "account_tier": get_account_tier(pending.user_id).value,
        }

    await telegram_service.send_job(job_data)

    pending_files.pop(pid, None)

    await callback.message.edit_text("✅ فایل برای پردازش ارسال شد. به‌زودی نتیجه برات میاد.")
    await callback.answer()


# ======================================================================
# Awaited-input states (file:*) — called from bot.py's handle_awaited_input
# ======================================================================

async def handle_file_awaited_input(message: Message, state: str) -> bool:
    """Handles every state prefixed "file:". Returns True if handled,
    False if this state isn't a file one (or the pending file it refers
    to has expired/vanished, in which case the caller should fall through
    to treating the message as something else)."""

    user_id = message.from_user.id

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
        await message.answer("✅ تامبنیل ذخیره شد.", reply_markup=options_keyboard(pid, pending_files))
        return True

    if field_name == "name":
        if not message.text:
            return False
        pending.options["rename_to"] = message.text.strip()
        awaiting_state.pop(user_id, None)
        await message.answer("✅ نام فایل بروزرسانی شد.", reply_markup=options_keyboard(pid, pending_files))
        return True

    if field_name == "thumb_count":
        if not message.text:
            return False
        text = message.text.strip()
        if text in ("خودکار", "auto", "0", "-"):
            pending.options["thumb_count"] = 0
        elif text.isdigit() and 1 <= int(text) <= 60:
            pending.options["thumb_count"] = int(text)
        else:
            await message.answer("لطفاً یک عدد بین 1 تا 60 بفرستید، یا «خودکار».")
            return True
        awaiting_state.pop(user_id, None)
        await message.answer("✅ تعداد عکس‌ها بروزرسانی شد.", reply_markup=options_keyboard(pid, pending_files))
        return True

    if field_name == "thumb_columns":
        if not message.text:
            return False
        text = message.text.strip()
        if text in ("خودکار", "auto", "0", "-"):
            pending.options["thumb_columns"] = 0
        elif text.isdigit() and 1 <= int(text) <= 20:
            pending.options["thumb_columns"] = int(text)
        else:
            await message.answer("لطفاً یک عدد بین 1 تا 20 بفرستید، یا «خودکار».")
            return True
        awaiting_state.pop(user_id, None)
        await message.answer("✅ تعداد ستون بروزرسانی شد.", reply_markup=options_keyboard(pid, pending_files))
        return True

    if field_name == "title":
        if not message.text:
            return False
        parts = message.text.split("|")
        pending.options["title"] = parts[0].strip()
        if len(parts) > 1:
            pending.options["artist"] = parts[1].strip()
        awaiting_state.pop(user_id, None)
        await message.answer("✅ عنوان/خواننده بروزرسانی شد.", reply_markup=options_keyboard(pid, pending_files))
        return True

    if field_name == "target":
        chat_id, label = await resolve_target(message)
        if chat_id is None:
            if message.document or message.video or message.audio:
                awaiting_state.pop(user_id, None)
                await message.answer("⏹ تنظیم مقصد لغو شد؛ این فایل را به‌عنوان کار جدید در نظر می‌گیرم.")
                return False
            await message.answer(
                "چت را نشناختم. یک پیام از آن فوروارد کنید، @username یا آیدی عددی چت را بفرستید.\n"
                "برای انصراف /cancel را بفرستید."
            )
            return True
        pending.options["target_chat_id"] = chat_id
        pending.options["target_label"] = label
        awaiting_state.pop(user_id, None)
        await message.answer("✅ مقصد بروزرسانی شد.", reply_markup=options_keyboard(pid, pending_files))
        return True

    if field_name == "parts_count":
        if not message.text or not message.text.strip().isdigit():
            await message.answer("لطفاً فقط یک عدد بفرستید (مثلاً 5).")
            return True

        total = int(message.text.strip())

        if total < 2 or total > 100:
            await message.answer("تعداد بخش باید بین 2 تا 100 باشد.")
            return True

        forwarded = await pending.source_message.forward(Telegram.GROUP_ID)

        pending.is_multipart = True
        pending.parts_total = total
        pending.part_message_ids = [forwarded.message_id]

        awaiting_state[user_id] = f"file:{pid}:next_part"

        await message.answer(
            f"✅ بخش ۱ از {total} ثبت شد. لطفاً بخش ۲ را بفرستید."
        )
        return True

    if field_name == "archive_password":
        if not message.text:
            await message.answer("لطفاً رمز را به‌صورت متن بفرستید.")
            return True

        skip_words = ("رد", "ندارم", "نمی‌دانم", "نمیدانم", "-")
        text = message.text.strip()

        if text not in skip_words:
            pending.options["password"] = text
            await message.answer("✅ رمز ذخیره شد.")
        else:
            pending.options["password"] = ""
            await message.answer("باشه، رمز تنظیم نشد.")

        awaiting_state.pop(user_id, None)

        await message.answer(
            "تنظیمات این فایل را بررسی و در صورت نیاز تغییر دهید:",
            reply_markup=options_keyboard(pid, pending_files),
        )
        return True

    if field_name == "next_part":

        # A convenience shortcut: if the user knows the password, they
        # can just type it here instead of going back to the dedicated
        # button — it doesn't advance part collection.
        if message.text and not (message.document or message.video or message.audio):
            pending.options["password"] = message.text.strip()
            await message.answer(
                f"✅ رمز ذخیره شد. حالا بخش {len(pending.part_message_ids) + 1} را بفرستید."
            )
            return True

        file = message.document or message.video or message.audio

        if not file:
            await message.answer("لطفاً فایل بخش بعدی را بفرستید (یا رمز آرشیو را به‌صورت متن).")
            return True

        forwarded = await message.forward(Telegram.GROUP_ID)
        pending.part_message_ids.append(forwarded.message_id)

        received = len(pending.part_message_ids)

        if received < pending.parts_total:
            await message.answer(
                f"✅ بخش {received} از {pending.parts_total} ثبت شد. بخش بعدی را بفرستید."
            )
            return True

        awaiting_state.pop(user_id, None)

        await message.answer(
            f"✅ هر {pending.parts_total} بخش دریافت شد. "
            "تنظیمات نهایی را بررسی و در صورت نیاز تغییر دهید:",
            reply_markup=options_keyboard(pid, pending_files),
        )
        return True

    return False
