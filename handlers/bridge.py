from __future__ import annotations

from aiogram import Bot
from aiogram import Dispatcher
from aiogram import F
from aiogram.types import Message

from config import Config

from core.constants import MessageType
from core.logger import get_logger
from core.protocol import Protocol

logger = get_logger(__name__)


async def bridge_handler(
    message: Message,
    bot: Bot,
):

    if message.chat.id != Config.GROUP_ID:
        return

    if not message.caption:
        return

    try:
        payload = Protocol.decode(
            message.caption,
        )

    except Exception:
        return

    if payload.get("type") != MessageType.RESULT:
        return

    user_id = payload["user_id"]

    caption = payload.get(
        "caption",
        "✅ فایل آماده است.",
    )

    try:

        await bot.copy_message(

            chat_id=user_id,

            from_chat_id=Config.GROUP_ID,

            message_id=message.message_id,

            caption=caption,

            disable_notification=payload.get(
                "silent",
                False,
            ),

        )

        logger.info(

            "Result delivered (%s)",

            user_id,

        )

        if payload.get(
            "delete_after_send",
            True,
        ):

            await bot.delete_message(

                Config.GROUP_ID,

                message.message_id,

            )

    except Exception:

        logger.exception(

            "Bridge delivery failed.",

        )


def register_bridge_handlers(
    dp: Dispatcher,
):

    dp.message.register(

        bridge_handler,

        F.chat.id == Config.GROUP_ID,

    )