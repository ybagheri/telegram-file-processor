from __future__ import annotations

from aiogram import Dispatcher
from aiogram.types import Message
from aiogram import F

from core.constants import MessageType
from core.protocol import Protocol

from services.telegram import TelegramService

from storage.session import session_storage

telegram = TelegramService()


async def password_message_handler(
    message: Message,
):

    session = session_storage.get(
        message.from_user.id,
    )

    if not session.waiting_password:

        return

    password = message.text.strip()

    session.options.password = password

    payload = Protocol.create_password_response(
        user_id=message.from_user.id,
        job_id=session.password_job_id,
        password=password,
    )

    await telegram.send_password_response(
        payload,
    )

    session.waiting_password = False

    session.password_job_id = None

    await message.answer(
        "✅ رمز دریافت شد و برای پردازش ارسال گردید.",
    )


async def bridge_password_request_handler(
    message: Message,
):

    if message.chat.id != telegram.bridge_group_id:

        return

    if not message.text:

        return

    try:

        payload = Protocol.decode(
            message.text,
        )

    except Exception:

        return

    if payload.get("type") != MessageType.PASSWORD_REQUEST:

        return

    user_id = payload["user_id"]

    session = session_storage.get(
        user_id,
    )

    session.waiting_password = True

    session.password_job_id = payload["job_id"]

    filename = payload.get(
        "filename",
        "",
    )

    await telegram.send_text(

        user_id,

        (
            "🔐 فایل نیاز به رمز دارد.\n\n"
            f"📦 {filename}\n\n"
            "لطفاً رمز فایل را ارسال کنید."
        ),

    )


def register_password_handlers(
    dp: Dispatcher,
):

    dp.message.register(
        password_message_handler,
        F.text,
    )

    dp.message.register(
        bridge_password_request_handler,
        F.chat.id == telegram.bridge_group_id,
    )
