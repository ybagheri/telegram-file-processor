import asyncio

from aiogram import Dispatcher as AiogramDispatcher, F
from aiogram.filters import Command
from aiogram.types import Message

from config import Telegram
from core.constants import MessageType
from core.logger import get_logger
from core.protocol import Protocol
from services.telegram import telegram_service
from utils.filetype import FileTypeDetector

logger = get_logger(__name__)

bot = telegram_service.bot
dp = AiogramDispatcher()

# chat_id (== user_id for private chats) -> job_id waiting for a password
pending_passwords: dict[int, str] = {}


# ======================================================================
# User side (private chat with the bot)
# ======================================================================

@dp.message(Command("start"), F.chat.type == "private")
async def start(message: Message):
    await message.answer("سلام! فایل خود را ارسال کنید.")


@dp.message(F.chat.type == "private")
async def handle_private_message(message: Message):

    # ------------------------------------------------------------
    # This is a password reply for a pending encrypted archive
    # ------------------------------------------------------------

    if message.chat.id in pending_passwords and message.text:

        job_id = pending_passwords.pop(message.chat.id)

        await telegram_service.send_password_response(
            Protocol.create_password_response(
                user_id=message.from_user.id,
                job_id=job_id,
                password=message.text,
            )
        )

        await message.answer("رمز ارسال شد، پردازش ادامه پیدا می‌کند.")
        return

    # ------------------------------------------------------------
    # Otherwise, treat it as a new file to process
    # ------------------------------------------------------------

    file = message.document or message.video or message.audio

    if not file:
        return

    file_name = getattr(file, "file_name", None) or f"file_{message.message_id}"
    mime_type = getattr(file, "mime_type", "") or ""

    file_type = FileTypeDetector.detect(mime_type, file_name)

    if file_type == "UNKNOWN":
        await message.answer("نوع فایل پشتیبانی نمی‌شود.")
        return

    # Forward the file into the bridge group; the worker will fetch this
    # exact message (by id) via the userbot client to download it.
    forwarded = await message.forward(Telegram.GROUP_ID)

    job_data = {
        "type": MessageType.JOB.value,
        "user_id": message.from_user.id,
        "message_id": forwarded.message_id,
        "file_type": file_type,
        "file_name": file_name,
        "original_chat_id": message.chat.id,
        "options": {},
    }

    await message.answer(f"فایل {file_type} دریافت شد. در حال پردازش...")

    await telegram_service.send_job(job_data)


# ======================================================================
# Bridge side: messages coming back from worker.py through the group
# ======================================================================

@dp.message(F.chat.id == Telegram.GROUP_ID)
async def handle_bridge_message(message: Message):

    raw = message.text or message.caption

    if not raw:
        return

    try:
        payload = Protocol.decode(raw)
    except Exception:
        return

    message_type = payload.get("type")
    user_id = payload.get("user_id")

    if not user_id:
        return

    if message_type == MessageType.RESULT.value:

        # Only the messages that actually carry the processed file
        # should be relayed to the user; the plain-text summary is
        # informational only and can be skipped.
        if message.document or message.video or message.audio:

            await telegram_service.copy_message_to_user(
                user_id=user_id,
                from_chat_id=message.chat.id,
                message_id=message.message_id,
            )

        return

    if message_type == MessageType.ERROR.value:

        await telegram_service.send_text(
            user_id,
            f"❌ خطا: {payload.get('message', 'پردازش ناموفق بود.')}",
        )

        return

    if message_type == MessageType.PASSWORD_REQUEST.value:

        job_id = payload.get("job_id")
        filename = payload.get("filename", "")

        pending_passwords[user_id] = job_id

        await telegram_service.send_text(
            user_id,
            f"🔒 فایل «{filename}» رمزدار است. لطفاً رمز آن را ارسال کنید.",
        )

        return

    if message_type == MessageType.INFO.value:

        await telegram_service.send_text(
            user_id,
            payload.get("message", ""),
        )

        return


async def main():
    logger.info("Bot started")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
