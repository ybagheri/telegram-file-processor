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

@dp.message(Command("start"))
async def start(message: Message):
    await message.answer("سلام! فایل خود را (ویدیو، صوت، PDF، آرشیو) ارسال کنید.")

@dp.message()
async def handle_file(message: Message):
    if not message.document and not message.video and not message.audio:
        await message.answer("لطفاً فایل ارسال کنید.")
        return

    file = message.document or message.video or message.audio
    mime_type = file.mime_type or ""
    file_name = file.file_name or "file"
    ext = file_name.split('.')[-1] if '.' in file_name else ""

    file_type = FileTypeDetector.detect(mime_type, f".{ext}")

    if file_type == "UNKNOWN":
        await message.answer("نوع فایل پشتیبانی نمی‌شود.")
        return

    # Create job info
    job_data = {
        "type": "job",
        "user_id": message.from_user.id,
        "message_id": message.message_id,
        "file_type": file_type,
        "file_name": file_name,
        "options": {}
    }

    await message.answer(f"فایل دریافت شد. نوع: {file_type}\nدر حال ارسال به Worker...")

    # Send to bridge
    await telegram_service.send_to_bridge(Protocol.encode(job_data))
    logger.info(f"Job forwarded to bridge: {file_type}")

async def main():
    logging.basicConfig(level=logging.INFO)
    logger.info("Bot started")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
