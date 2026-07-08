from aiogram import Dispatcher
from aiogram.filters import Command
from aiogram.types import Message


async def start_handler(
    message: Message,
):

    text = (
        "👋 به ربات پردازش فایل خوش آمدید.\n\n"
        "فایل خود را ارسال کنید.\n\n"
        "پشتیبانی:\n"
        "🎬 Video\n"
        "🎵 Audio\n"
        "📄 PDF\n"
        "📦 ZIP / RAR / 7Z"
    )

    await message.answer(
        text,
    )


def register_start_handlers(
    dp: Dispatcher,
):

    dp.message.register(
        start_handler,
        Command("start"),
    )
