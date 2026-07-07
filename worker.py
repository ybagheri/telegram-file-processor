import asyncio
from pathlib import Path

from telethon import events

from config import Config
from core.job import Job
from core.logger import get_logger
from core.protocol import Protocol
from dispatcher.dispatcher import Dispatcher
from services.telegram import TelegramService
from utils.filetype import FileTypeDetector

logger = get_logger(__name__)

telegram = TelegramService()
dispatcher = Dispatcher()


async def process_job(event, payload: dict):

    if not event.message.media:
        logger.warning("Job message has no media")
        return

    job = Job(
        user_id=payload["user_id"],
        message_id=event.message.id,
        options=payload.get("options", {}),
    )

    job.create_folders()

    filename = "input"

    mime_type = ""

    if event.message.file:

        filename = event.message.file.name or filename
        mime_type = event.message.file.mime_type or ""
        job.file_size = event.message.file.size or 0

    job.file_name = filename
    job.mime_type = mime_type

    job.file_type = FileTypeDetector.detect(
        mime_type,
        filename,
    )

    input_path = job.input_dir / filename

    job.input_file = await telegram.download(
        event.message,
        input_path,
    )

    if job.input_file is None:

        await telegram.send_error(
            Protocol.create_error(
                user_id=job.user_id,
                job_id=job.job_id,
                message="Download failed",
            )
        )

        return

    success = await dispatcher.dispatch(job)

    if success:

        await telegram.send_result(
            Protocol.create_result(
                user_id=job.user_id,
                job_id=job.job_id,
                files=[
                    p.name
                    for p in job.output_files
                ],
            )
        )

        for output in job.output_files:

            await telegram.upload_file(
                output,
                Protocol.create_result(
                    user_id=job.user_id,
                    job_id=job.job_id,
                    files=[output.name],
                ),
            )

    else:

        await telegram.send_error(
            Protocol.create_error(
                user_id=job.user_id,
                job_id=job.job_id,
                message="Processing failed",
            )
        )

    job.cleanup()


async def main():

    await telegram.start()

    logger.info("Worker started")

    @telegram.client.on(events.NewMessage(chats=Config.GROUP_ID))
    async def bridge_handler(event):

        if not event.message.message:
            return

        try:

            payload = Protocol.decode(
                event.message.message,
            )

        except Exception:
            return

        if payload.get("type") != "JOB":
            return

        await process_job(
            event,
            payload,
        )

    await telegram.client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())