import asyncio
from telethon import events
from config import Config
from services.telegram import TelegramService
from core.protocol import Protocol
from core.job import Job
from dispatcher.dispatcher import Dispatcher
from utils.filetype import FileTypeDetector
from core.logger import get_logger
from pathlib import Path

logger = get_logger(__name__)

telegram_service = TelegramService()
dispatcher = Dispatcher()

async def main():
    await telegram_service.client.start()
    logger.info("Worker started and listening to bridge group")

    @telegram_service.client.on(events.NewMessage(chats=Config.GROUP_ID))
    async def handle_bridge_message(event):
        try:
            if not event.message.message or not event.message.message.strip():
                return
                
            data = Protocol.decode(event.message.message)
            
            if data.get("type") == "job":
                logger.info(f"New job received: {data.get('file_type')}")
                
                # TODO: In next phase we'll add full download + dispatch
                # For now just acknowledge
                await telegram_service.send_to_bridge(
                    Protocol.encode({
                        "type": "info",
                        "message": f"Job {data.get('file_type')} received"
                    })
                )
        except Exception as e:
            logger.error(f"Error in worker: {e}")

    await telegram_service.client.run_until_disconnected()

if __name__ == "__main__":
    asyncio.run(main())
