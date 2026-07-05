import asyncio
from aiogram import Bot
from telethon import TelegramClient
from config import Config
from core.logger import get_logger

logger = get_logger(__name__)

class TelegramService:
    def __init__(self):
        self.bot = Bot(token=Config.BOT_TOKEN)
        self.client = TelegramClient(
            Config.SESSION_NAME,
            Config.API_ID,
            Config.API_HASH
        )

    async def send_to_bridge(self, chat_id: int, text: str):
        await self.bot.send_message(chat_id, text)

    async def download_file(self, message, path: str):
        return await message.download_media(file=path)
