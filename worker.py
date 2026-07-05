import asyncio
from telethon import events
from config import Config
from services.telegram import TelegramService
from core.protocol import Protocol
from core.job import Job
from dispatcher.dispatcher import Dispatcher
from utils.filetype import FileTypeDetector
from core.logger import get_logger

logger = get_logger(__name__)

telegram_service = TelegramService()
dispatcher = Dispatcher()

async def main():
    await telegram_service.client.start()
    logger.info("Worker started and listening to bridge group")

    @telegram_service.client.on(events.NewMessage(chats=Config.GROUP_ID))
    async def handle_bridge_message(event):
        try:
            if not event.message.message:
                return
            data = Protocol.decode(event.message.message)
            if data.get("type") == "job":
                logger.info(f"Received new job: {data.get('job_id')}")
                # TODO: Create Job, download file, dispatch
        except Exception as e:
            logger.error(f"Error processing bridge message: {e}")

    await telegram_service.client.run_until_disconnected()

if __name__ == "__main__":
    asyncio.run(main())
