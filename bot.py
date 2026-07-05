import asyncio
import logging
from aiogram import Bot, Dispatcher as AiogramDispatcher
from aiogram.filters import Command
from config import Config
from services.telegram import TelegramService
from core.logger import get_logger
from core.protocol import Protocol

logger = get_logger(__name__)

bot = Bot(token=Config.BOT_TOKEN)
dp = AiogramDispatcher()
telegram_service = TelegramService()

@dp.message(Command("start"))
async def start(message):
    await message.answer("سلام! فایل خود را ارسال کنید برای پردازش.")

# TODO: File handler + forward to bridge group

async def main():
    logging.basicConfig(level=logging.INFO)
    logger.info("Bot started")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
