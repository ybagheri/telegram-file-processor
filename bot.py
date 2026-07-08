import asyncio

from aiogram import Bot
from aiogram import Dispatcher

from config import Config

from handlers.start import register_start_handlers
from handlers.upload import register_upload_handlers
from handlers.callback import register_callback_handlers
from handlers.bridge import register_bridge_handlers
from handlers.password import register_password_handlers

bot = Bot(
    Config.BOT_TOKEN,
)

dp = Dispatcher()

register_start_handlers(dp)
register_upload_handlers(dp)
register_callback_handlers(dp)
register_bridge_handlers(dp)
register_password_handlers(dp)


async def main():

    await dp.start_polling(
        bot,
    )


if __name__ == "__main__":

    asyncio.run(
        main(),
    )
