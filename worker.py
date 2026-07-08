import asyncio

from telethon import events

from config import Telegram
from core.constants import MessageType
from core.job import Job
from core.job_options import JobOptions
from core.logger import get_logger
from core.password_broker import password_broker
from core.protocol import Protocol
from dispatcher.dispatcher import Dispatcher
from services.telegram import telegram_service
from utils.filetype import FileTypeDetector

logger = get_logger(__name__)

dispatcher = Dispatcher()


def _build_options(raw: dict) -> JobOptions:
    allowed = JobOptions.__dataclass_fields__.keys()
    filtered = {k: v for k, v in (raw or {}).items() if k in allowed}
    return JobOptions(**filtered)


async def process_job(payload: dict):

    message_id = payload.get("message_id")

    # The bridge message that carries the JSON payload is a *separate*
    # text message from the one that carries the actual file (which was
    # forwarded/copied into the group by bot.py). We fetch the real
    # media message by its id instead of relying on whatever message
    # happened to trigger this handler.
    message = await telegram_service.client.get_messages(
        Telegram.GROUP_ID,
        ids=message_id,
    )

    if message is None or not message.media:
        logger.warning("Job message %s has no media", message_id)
        return

    job = Job(
        user_id=payload["user_id"],
        message_id=message_id,
        options=_build_options(payload.get("options", {})),
    )

    filename = "input"
    mime_type = ""

    if message.file:
        filename = message.file.name or filename
        mime_type = message.file.mime_type or ""
        job.file_size = message.file.size or 0

    job.original_name = filename
    job.mime_type = mime_type

    job.file_type = FileTypeDetector.detect(
        mime_type,
        filename,
    )

    input_path = job.input_dir / filename

    job.input_file = await telegram_service.download(
        message,
        input_path,
    )

    if job.input_file is None:

        await telegram_service.send_error(
            Protocol.create_error(
                user_id=job.user_id,
                job_id=job.job_id,
                message="Download failed",
            )
        )

        job.cleanup()
        return

    success = False

    try:
        success = await dispatcher.dispatch(job)
    except Exception as e:
        logger.exception("Unhandled error while dispatching job %s", job.job_id)
        success = False
        error_message = str(e) or "Processing failed"
    else:
        error_message = "Processing failed"

    if job.has_output:

        await telegram_service.send_result(
            Protocol.create_result(
                user_id=job.user_id,
                job_id=job.job_id,
                files=[p.name for p in job.output_files],
                target_chat_id=job.options.target_chat_id,
            )
        )

        as_video = job.options.upload_as == "video"
        as_voice = job.options.quality == "voice"

        for output in job.output_files:

            await telegram_service.upload_file(
                output,
                Protocol.create_result(
                    user_id=job.user_id,
                    job_id=job.job_id,
                    files=[output.name],
                    target_chat_id=job.options.target_chat_id,
                ),
                force_document=not (as_video or as_voice),
                voice_note=as_voice,
            )

    if not success or not job.has_output:

        await telegram_service.send_error(
            Protocol.create_error(
                user_id=job.user_id,
                job_id=job.job_id,
                message=error_message if not job.has_output else "Some files failed to process",
                target_chat_id=job.options.target_chat_id,
            )
        )

    job.cleanup()


async def _process_job_safe(payload: dict):
    try:
        await process_job(payload)
    except Exception:
        logger.exception("Unhandled error processing job payload: %s", payload)


async def main():

    await telegram_service.start()

    logger.info("Worker started")

    @telegram_service.client.on(events.NewMessage(chats=Telegram.GROUP_ID))
    async def bridge_handler(event):

        if not event.message.message:
            return

        try:
            payload = Protocol.decode(event.message.message)
        except Exception:
            return

        message_type = payload.get("type")

        if message_type == MessageType.JOB.value:
            asyncio.create_task(_process_job_safe(payload))

        elif message_type == MessageType.PASSWORD_RESPONSE.value:
            password_broker.resolve(
                payload.get("job_id"),
                payload.get("password", ""),
            )

    await telegram_service.client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
