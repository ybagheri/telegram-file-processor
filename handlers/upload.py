from __future__ import annotations

from pathlib import Path

from aiogram import Dispatcher
from aiogram.types import (
    CallbackQuery,
    Document,
    Message,
    Video,
    Audio,
)

from config import Config

from core.filetype import FileTypeDetector
from core.protocol import Protocol

from keyboards.quality import quality_keyboard

from services.telegram import TelegramService

from storage.session import session_storage

from aiogram import F
from aiogram.types import CallbackQuery

telegram = TelegramService()


async def upload_handler(
    message: Message,
):

    media = None

    if message.video:

        media = message.video

    elif message.audio:

        media = message.audio

    elif message.document:

        media = message.document

    else:

        await message.answer("❌ این نوع فایل پشتیبانی نمی‌شود.")

        return

    file_name = getattr(
        media,
        "file_name",
        None,
    )

    if not file_name:

        if message.video:

            file_name = "video.mp4"

        elif message.audio:

            file_name = "audio.mp3"

        else:

            file_name = "file"

    mime_type = getattr(
        media,
        "mime_type",
        "",
    )

    file_size = getattr(
        media,
        "file_size",
        0,
    )

    file_type = FileTypeDetector.detect(
        mime_type,
        Path(file_name).suffix,
    )

    if file_type == "UNKNOWN":

        await message.answer("❌ فرمت فایل پشتیبانی نمی‌شود.")

        return

    session = session_storage.get(
        message.from_user.id,
    )

    session.file_id = media.file_id

    session.file_name = file_name

    session.mime_type = mime_type

    session.file_size = file_size

    session.file_type = file_type
    
    session.chat_id = message.chat.id

    session.message_id = message.message_id

    session.options = type(session.options)()

    if file_type == "VIDEO":

        await message.answer(
            "کیفیت خروجی را انتخاب کنید.",
            reply_markup=quality_keyboard(),
        )

        return

    if file_type == "AUDIO":

        await message.answer(
            "فرمت خروجی را انتخاب کنید.",
            reply_markup=quality_keyboard(),
        )

        return

    payload = Protocol.create_job(
        user_id=message.from_user.id,
        message_id=message.message_id,
        file_type=file_type,
        original_name=file_name,
        mime_type=mime_type,
        file_size=file_size,
        options={},
    )

    await telegram.copy_user_message_to_bridge(

        chat_id=message.chat.id,

        message_id=message.message_id,

        caption=payload,

    )

    await message.answer(
        "⏳ فایل برای پردازش ارسال شد.",
    )


async def quality_callback(
    callback: CallbackQuery,
):

    if not callback.data.startswith("quality:"):

        return

    session = session_storage.get(
        callback.from_user.id,
    )

    quality = callback.data.split(
        ":",
        1,
    )[1]

    session.options.quality = quality

    payload = Protocol.create_job(
        user_id=callback.from_user.id,
        message_id=session.message_id,
        file_type=session.file_type,
        original_name=session.file_name,
        mime_type=session.mime_type,
        file_size=session.file_size,
        options={
            "quality": quality,
            "title": session.options.title,
            "artist": session.options.artist,
            "password": session.options.password,
        },
    )

    await telegram.copy_user_message_to_bridge(

        chat_id=session.chat_id,

        message_id=session.message_id,

        caption=payload,

    )

    await callback.message.edit_text(
        "⏳ فایل برای پردازش ارسال شد.\n" f"کیفیت انتخاب شده: {quality}",
    )

    await callback.answer()


# =====================================================
# Register
# =====================================================


def register_upload_handlers(
    dp: Dispatcher,
):

    dp.message.register(
        upload_handler,
        F.video,
    )

    dp.message.register(
        upload_handler,
        F.audio,
    )

    dp.message.register(
        upload_handler,
        F.document,
    )

    dp.callback_query.register(
        quality_callback,
        F.data.startswith("quality:"),
    )
