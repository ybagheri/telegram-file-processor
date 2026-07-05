import asyncio
import logging
from aiogram import Bot, Dispatcher as AiogramDispatcher
from aiogram.filters import Command
from aiogram.types import Message
from config import Config
from services.telegram import TelegramService
from core.logger import get_logger
from core.protocol import Protocol

logger = get_logger(__name__)

bot = Bot(token=Config.BOT_TOKEN)
dp = AiogramDispatcher()
telegram_service = TelegramService()

# In-memory store for pending passwords (can be improved with DB later)
pending_passwords = {}

@dp.message(Command("start"))
async def start(message: Message):
    await message.answer("سلام! فایل خود را ارسال کنید.")

@dp.message()
async def handle_message(message: Message):
    # Handle password response
    if message.chat.id in pending_passwords:
        job_id = pending_passwords[message.chat.id]
        await handle_password_response(message, job_id)
        return

    # Normal file handling (previous code)
    if message.document or message.video or message.audio:
        # ... (previous file handler code remains)
        pass
    else:
        await message.answer("لطفاً فایل یا رمز آرشیو را ارسال کنید.")

async def handle_password_response(message: Message, job_id):
    from core.protocol import Protocol
    await telegram_service.send_to_bridge(
        Protocol.encode({
            "type": "password_response",
            "job_id": job_id,
            "password": message.text,
            "user_id": message.from_user.id
        })
    )
    await message.answer("رمز دریافت شد. ادامه پردازش...")
    del pending_passwords[message.chat.id]

async def main():
    logging.basicConfig(level=logging.INFO)
    logger.info("Bot started with Password support")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
