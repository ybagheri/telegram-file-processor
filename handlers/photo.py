"""
Plain photo -> watermark confirmation flow. Extracted out of bot.py
(phase D, step 3 of the module split — see CLAUDE.md's change log) as an
aiogram 3 Router, registered from bot.py via `dp.include_router(router)`.

`handle_incoming_photo` is NOT a `@router` handler — it's a plain function
called from `handle_private_message` (in bot.py) when an incoming private
message is a bare photo (not a document/video/audio/archive), same as
before the split.
"""
from pathlib import Path
from uuid import uuid4

from aiogram import F, Router
from aiogram.types import CallbackQuery, FSInputFile, Message

from config import Paths
from keyboards.photo import photo_confirm_keyboard
from keyboards.settings import logo_position_keyboard
from models.pending_photo import PendingPhoto
from services.media import media_service
from services.settings_store import settings_store
from services.telegram import telegram_service
from state import awaiting_state, pending_photos

router = Router(name="photo")
bot = telegram_service.bot


async def handle_incoming_photo(message: Message):

    pid = uuid4().hex[:10]

    pending_photos[pid] = PendingPhoto(
        user_id=message.from_user.id,
        chat_id=message.chat.id,
        source_message=message,
    )

    await message.answer(
        "این یک عکسه، نه ویدیو/فایل صوتی/آرشیو. اگر می‌خواهید لوگوی واترمارک را روی همین عکس بزنم، تأیید کنید:",
        reply_markup=photo_confirm_keyboard(pid),
    )


@router.callback_query(F.data.startswith("pw:"))
async def photo_watermark_action(callback: CallbackQuery):

    _, pid, action = callback.data.split(":", 2)
    pending = pending_photos.get(pid)

    if not pending:
        await callback.answer("این درخواست منقضی شده است.", show_alert=True)
        return

    if action == "apply":
        await callback.message.edit_text("⏳ در حال اعمال واترمارک...")
        await apply_watermark_to_photo(pid)
        await callback.answer()
        return

    if action == "changelogo":
        awaiting_state[pending.user_id] = "settings_logo"
        await callback.message.answer(
            "تصویر لوگوی جدید را به‌صورت عکس بفرستید. بعد از ذخیره، دوباره روی «💧 روی این عکس واترمارک بزن» بزنید."
        )
        await callback.answer()
        return

    if action == "changepos":
        current = settings_store.get(pending.user_id)["logo_position"]
        await callback.message.answer(
            "📍 محل جدید واترمارک را انتخاب کنید. بعد از انتخاب، دوباره روی «💧 روی این عکس واترمارک بزن» بزنید.",
            reply_markup=logo_position_keyboard(current),
        )
        await callback.answer()
        return

    if action == "cancel":
        pending_photos.pop(pid, None)
        await callback.message.edit_text("❌ باشه، کاری روی این عکس انجام نشد.")
        await callback.answer()
        return


async def apply_watermark_to_photo(pid: str):

    pending = pending_photos.pop(pid, None)

    if not pending:
        return

    settings = settings_store.get(pending.user_id)
    logo_path = Path(settings["logo_path"]) if settings.get("logo_path") else Paths.LOGO_FILE
    position = settings.get("logo_position", "bottom_right")

    if not logo_path.exists():
        await telegram_service.send_text(
            pending.user_id,
            "هنوز هیچ لوگویی برای واترمارک تنظیم نکرده‌اید. اول از /settings یک لوگو تنظیم کنید.",
        )
        return

    photo = pending.source_message.photo[-1]

    input_path = Paths.TEMP / f"{pid}_input.jpg"
    output_path = Paths.TEMP / f"{pid}_watermarked.jpg"
    input_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        await bot.download(photo, destination=input_path)

        ok = await media_service.watermark_image(input_path, output_path, logo_path, position)

        if not ok:
            await telegram_service.send_text(
                pending.user_id,
                "متأسفانه اعمال واترمارک روی این عکس با خطا مواجه شد.",
            )
            return

        await bot.send_photo(pending.user_id, FSInputFile(output_path))

    finally:
        for path in (input_path, output_path):
            if path.exists():
                try:
                    path.unlink()
                except OSError:
                    pass
