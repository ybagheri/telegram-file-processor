from __future__ import annotations

from pathlib import Path

from aiogram import Bot
from telethon import TelegramClient

from config import Telegram
from core.logger import get_logger
from core.protocol import Protocol

logger = get_logger(__name__)


class TelegramService:

    def __init__(self):
        self.bot = Bot(token=Telegram.BOT_TOKEN)

        self.client = TelegramClient(
            Telegram.SESSION_NAME,
            Telegram.API_ID,
            Telegram.API_HASH,
        )

        self.bridge_group_id = Telegram.GROUP_ID

    async def start(self):
        if not self.client.is_connected():
            await self.client.start()

    async def stop(self):
        if self.client.is_connected():
            await self.client.disconnect()

    # ------------------------------------------------------------------
    # Bridge: bot -> worker (sent through the Bot API)
    # ------------------------------------------------------------------

    async def send_job(self, payload: dict):
        await self.bot.send_message(
            self.bridge_group_id,
            Protocol.encode(payload),
        )

    async def send_password_response(self, payload: dict):
        await self.bot.send_message(
            self.bridge_group_id,
            Protocol.encode(payload),
        )

    # ------------------------------------------------------------------
    # Bridge: worker -> bot (sent through the userbot/MTProto client)
    # ------------------------------------------------------------------

    async def send_result(self, payload: dict):
        await self.client.send_message(
            self.bridge_group_id,
            Protocol.encode(payload),
        )

    async def send_info(self, payload: dict):
        await self.client.send_message(
            self.bridge_group_id,
            Protocol.encode(payload),
        )

    async def send_error(self, payload: dict):
        await self.client.send_message(
            self.bridge_group_id,
            Protocol.encode(payload),
        )

    async def send_password_request(self, payload: dict):
        await self.client.send_message(
            self.bridge_group_id,
            Protocol.encode(payload),
        )

    # ------------------------------------------------------------------
    # Download
    # ------------------------------------------------------------------

    async def download(self, message, destination: Path) -> Path | None:

        destination.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        try:
            result = await message.download_media(
                file=str(destination),
            )
        except Exception:
            logger.exception(f"Download failed: {destination}")
            return None

        if not result:
            return None

        logger.info(f"Downloaded: {destination}")

        return destination

    # ------------------------------------------------------------------
    # Upload (worker -> bridge group, carries a Protocol caption)
    # ------------------------------------------------------------------

    async def upload_file(
        self,
        path: Path,
        caption: dict,
    ):
        await self.client.send_file(
            self.bridge_group_id,
            str(path),
            caption=Protocol.encode(caption),
        )

    # ------------------------------------------------------------------
    # User (bot -> end user, via Bot API)
    # ------------------------------------------------------------------

    async def send_text(
        self,
        user_id: int,
        text: str,
    ):
        await self.bot.send_message(
            user_id,
            text,
        )

    async def copy_message_to_user(
        self,
        *,
        user_id: int,
        from_chat_id: int,
        message_id: int,
        caption: str | None = None,
    ):
        await self.bot.copy_message(
            chat_id=user_id,
            from_chat_id=from_chat_id,
            message_id=message_id,
            caption=caption,
        )


# =====================================================
# Singleton (shared by bot.py, worker.py and processors
# so we don't spin up duplicate Bot/TelegramClient
# instances for the same credentials).
# =====================================================

telegram_service = TelegramService()
