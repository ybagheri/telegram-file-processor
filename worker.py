import asyncio
from telethon import events
from config import Config
from services.telegram import TelegramService
from core.protocol import Protocol
from core.job import Job
from dispatcher.dispatcher import Dispatcher
from utils.filetype import FileTypeDetector
from utils.files import create_job_folders, cleanup_job
from core.logger import get_logger
from pathlib import Path

logger = get_logger(__name__)

telegram_service = TelegramService()
dispatcher = Dispatcher()

async def main():
    await telegram_service.client.start()
    logger.info("Worker started")

    @telegram_service.client.on(events.NewMessage(chats=Config.GROUP_ID))
    async def handle_bridge_message(event):
        try:
            if not event.message.message:
                return
            data = Protocol.decode(event.message.message)

            if data.get("type") == "job":
                await process_job(data, event)
        except Exception as e:
            logger.error(f"Worker error: {e}")

    await telegram_service.client.run_until_disconnected()

async def process_job(data, event):
    """Create Job, download file, dispatch"""
    job = Job(
        user_id=data["user_id"],
        message_id=data["message_id"],
        file_type=data["file_type"]
    )

    job_dir = create_job_folders(job, Config.PATHS["downloads"])
    input_path = job.folders["input"] / data.get("file_name", "input_file")

    logger.info(f"Downloading file for job {job.job_id}")
    
    # Download the file (we need the original message - simplified for now)
    # In real scenario we forward message_id or use file_id
    # For now placeholder:
    # file = await telegram_service.download_file(original_message, str(input_path))
    
    job.input_file = input_path
    job.status = "downloading"

    logger.info(f"Job {job.job_id} ready. Dispatching...")
    await dispatcher.dispatch(job)

    # Cleanup
    cleanup_job(job_dir.parent)

    # Send result back
    await telegram_service.send_to_bridge(
        Protocol.encode({
            "type": "result",
            "job_id": job.job_id,
            "status": job.status,
            "user_id": job.user_id
        })
    )

if __name__ == "__main__":
    asyncio.run(main())
