import asyncio
import logging
from aiogram import Bot, Dispatcher as AiogramDispatcher
from aiogram.filters import Command
from aiogram.types import Message
from config import Config
from services.telegram import TelegramService
from core.logger import get_logger
from core.protocol import Protocol
from utils.filetype import FileTypeDetector

logger = get_logger(__name__)

bot = Bot(token=Config.BOT_TOKEN)
dp = AiogramDispatcher()
telegram_service = TelegramService()

pending_passwords = {}

@dp.message(Command("start"))
async def start(message: Message):
    await message.answer("سلام! فایل خود را ارسال کنید.")

@dp.message()
async def handle_file(message: Message):
    if message.chat.id in pending_passwords:
        # Password handling
        job_id = pending_passwords[message.chat.id]
        await telegram_service.send_to_bridge(
            Protocol.encode({
                "type": "password_response",
                "job_id": job_id,
                "password": message.text,
                "user_id": message.from_user.id
            })
        )
        await message.answer("رمز ارسال شد.")
        del pending_passwords[message.chat.id]
        return

    # File handling
    file = message.document or message.video or message.audio
    if not file:
        return

    file_name = file.file_name or f"file_{message.message_id}"
    mime_type = getattr(file, 'mime_type', '') or ''
    ext = file_name.split('.')[-1] if '.' in file_name else ''
    file_type = FileTypeDetector.detect(mime_type, f".{ext}")

    if file_type == "UNKNOWN":
        await message.answer("نوع فایل پشتیبانی نمی‌شود.")
        return

    # Forward the file to bridge group
    forwarded = await message.forward(Config.GROUP_ID)
    
    job_data = {
        "type": "job",
        "user_id": message.from_user.id,
        "message_id": forwarded.message_id,   # Important: forwarded message id
        "file_type": file_type,
        "file_name": file_name,
        "original_chat_id": message.chat.id
    }

    await message.answer(f"فایل {file_type} دریافت شد. در حال پردازش...")
    await telegram_service.send_to_bridge(Protocol.encode(job_data))

async def main():
    logging.basicConfig(level=logging.INFO)
    logger.info("Bot started with Forward support")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
