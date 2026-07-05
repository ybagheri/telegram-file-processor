import asyncio
from aiogram import Bot
from telethon import TelegramClient
from config import Config
from core.logger import get_logger
from core.protocol import Protocol

logger = get_logger(__name__)

class TelegramService:
    def __init__(self):
        self.bot = Bot(token=Config.BOT_TOKEN)
        self.client = TelegramClient(
            Config.SESSION_NAME,
            Config.API_ID,
            Config.API_HASH
        )
        self.bridge_group_id = Config.GROUP_ID

    async def send_to_bridge(self, text: str):
        try:
            await self.bot.send_message(self.bridge_group_id, text)
        except Exception as e:
            logger.error(f"Bridge send failed: {e}")

    async def send_result_to_user(self, user_id: int, text: str):
        try:
            await self.bot.send_message(user_id, text)
        except Exception as e:
            logger.error(f"User send failed: {e}")

    async def download_file(self, message, file_path: str):
        try:
            return await message.download_media(file=file_path)
        except Exception as e:
            logger.error(f"Download failed: {e}")
            return None
