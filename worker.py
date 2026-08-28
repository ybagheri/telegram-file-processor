import asyncio
import re
import shutil

from telethon import events
from telethon.errors import FloodWaitError

from config import Paths, Processing, Telegram
from core.constants import MessageType
from core.delivery import upload_entry
from core.job import Job
from core.job_options import JobOptions
from core.logger import get_logger
from core.password_broker import password_broker
from core.protocol import Protocol
from dispatcher.dispatcher import Dispatcher
from processors.archive import ArchiveProcessor
from services.telegram import telegram_service
from utils.filetype import FileTypeDetector
from utils.text import strip_excluded

logger = get_logger(__name__)

dispatcher = Dispatcher()
archive_processor = ArchiveProcessor()


def _build_options(raw: dict) -> JobOptions:
    allowed = JobOptions.__dataclass_fields__.keys()
    filtered = {k: v for k, v in (raw or {}).items() if k in allowed}
    return JobOptions(**filtered)


# Recognizes common multi-volume RAR naming so the base course name can be
# recovered for renaming/titles: "name.part03.rar", "name.r00", "name.r01".
_PART_SUFFIX_RE = re.compile(r"\.part\d+(\.rar)?$|\.r\d{2}$", re.IGNORECASE)


def _strip_part_suffix(filename: str) -> str:
    from pathlib import Path

    stem_with_ext = _PART_SUFFIX_RE.sub("", filename)

    if stem_with_ext == filename:
        return filename

    if not Path(stem_with_ext).suffix:
        stem_with_ext += ".rar"

    return stem_with_ext


def _file_too_large_message(size_bytes: int) -> str:
    """Persian text delivered to the user when a job is rejected for
    exceeding Processing.MAX_FILE_SIZE — sent through the bridge as a
    Protocol.create_error payload (handlers/bridge.py prefixes it with
    "❌ خطا:")."""

    limit_gb = Processing.MAX_FILE_SIZE / (1024 * 1024 * 1024)
    size_gb = size_bytes / (1024 * 1024 * 1024)

    return (
        f"حجم فایل ({size_gb:.1f} گیگابایت) بیشتر از حد مجاز "
        f"({limit_gb:.1f} گیگابایت) است."
    )


def _required_headroom(declared_size: int) -> int:
    """Working space demanded before we'll start downloading a file of
    the given declared size. 0 = "small enough that the check would be
    noise, skip it" — consistent with the disk-conscious streaming
    approach in processors/archive.py (which never holds the whole
    extracted tree at once), we only pre-check that there's room for the
    input plus a safety factor of working space (ffmpeg output,
    extraction), not for every hypothetical expansion."""

    if declared_size <= Processing.DISK_SPACE_CHECK_THRESHOLD:
        return 0

    return int(declared_size * Processing.DISK_SPACE_SAFETY_FACTOR)


def _has_enough_disk_space(declared_size: int) -> bool:
    """True when every path the job will write to (DOWNLOADS, TEMP,
    OUTPUTS) has at least the required headroom free. They all normally
    sit on the same filesystem, but they're checked individually so a
    deployment that bind-mounts one of them separately is still guarded."""

    headroom = _required_headroom(declared_size)

    if headroom <= 0:
        return True

    for path in (Paths.DOWNLOADS, Paths.TEMP, Paths.OUTPUTS):

        free = shutil.disk_usage(path).free

        if free < headroom:

            logger.warning(
                "Low disk space on %s: %s bytes free, need %s",
                path,
                free,
                headroom,
            )
            return False

    return True


async def _reject_no_disk_space(job: Job, declared_size: int):

    logger.warning(
        "Job %s rejected: not enough free disk space for a %s-byte file",
        job.job_id,
        declared_size,
    )

    await telegram_service.send_error(
        Protocol.create_error(
            user_id=job.user_id,
            job_id=job.job_id,
            message=(
                "فضای دیسک سرور برای پردازش این فایل کافی نیست. "
                "لطفاً بعداً دوباره تلاش کنید."
            ),
        )
    )

    job.cleanup()


async def _reject_too_large(job: Job, size_bytes: int):
    """Tells the user the job was rejected for size and aborts cleanly.
    Must be called before any download starts, so there's nothing on
    disk to clean up beyond the job's (empty) working directories."""

    logger.warning(
        "Job %s rejected: declared size %s bytes exceeds MAX_FILE_SIZE (%s)",
        job.job_id,
        size_bytes,
        Processing.MAX_FILE_SIZE,
    )

    await telegram_service.send_error(
        Protocol.create_error(
            user_id=job.user_id,
            job_id=job.job_id,
            message=_file_too_large_message(size_bytes),
        )
    )

    job.cleanup()


async def process_job(payload: dict):

    part_ids = payload.get("part_message_ids")

    if part_ids and len(part_ids) > 1:
        await process_multipart_job(payload, part_ids)
        return

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

    job.original_name = strip_excluded(filename, job.options.exclude_text)
    job.mime_type = mime_type

    job.file_type = FileTypeDetector.detect(
        mime_type,
        filename,
    )

    # MAX_FILE_SIZE enforcement: message.file.size is the declared size
    # from Telegram metadata, known before any bytes are transferred —
    # reject here rather than discovering the overrun mid-download.
    if job.file_size > Processing.MAX_FILE_SIZE:
        await _reject_too_large(job, job.file_size)
        return

    # Free-disk-space guard: don't even start downloading if there isn't
    # headroom for the input plus working space (ffmpeg/extraction).
    if not _has_enough_disk_space(job.file_size):
        await _reject_no_disk_space(job, job.file_size)
        return

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
    error_message = "Processing failed"

    try:
        success = await dispatcher.dispatch(job)
    except Exception as e:
        logger.exception("Unhandled error while dispatching job %s", job.job_id)
        success = False
        error_message = (str(e) or "Processing failed")[:300]

    await _deliver_and_cleanup(job, success, error_message)


async def process_multipart_job(payload: dict, part_ids: list[int]):
    """A user-declared multi-volume RAR archive: the parts arrive as
    separate bridge messages (one per volume the user sent). Delegates the
    actual disk-conscious download/extract/upload cycle to
    ArchiveProcessor.process_multivolume — see processors/archive.py."""

    messages = await telegram_service.client.get_messages(
        Telegram.GROUP_ID,
        ids=part_ids,
    )

    messages = [m for m in messages if m is not None and m.media]

    if len(messages) < 2:
        logger.warning("Multipart job has too few valid parts: %s", part_ids)
        return

    job = Job(
        user_id=payload["user_id"],
        message_id=part_ids[0],
        options=_build_options(payload.get("options", {})),
    )

    job.file_type = "ARCHIVE"

    first_name = (messages[0].file.name if messages[0].file else None) or "archive.rar"
    base_name = _strip_part_suffix(first_name)
    job.original_name = strip_excluded(base_name, job.options.exclude_text)

    # MAX_FILE_SIZE enforcement: sum the declared size of every part and
    # reject before any volume is downloaded (same rule as single-file
    # jobs — a multi-volume set isn't a way around the size limit).
    total_size = sum(
        (m.file.size or 0)
        for m in messages
        if m.file
    )

    if total_size > Processing.MAX_FILE_SIZE:
        await _reject_too_large(job, total_size)
        return

    # Same free-disk-space guard, sized from the declared *total* of the
    # volume set (volumes download on demand, but extraction output and
    # the sum of the parts still have to fit alongside each other).
    if not _has_enough_disk_space(total_size):
        await _reject_no_disk_space(job, total_size)
        return

    success = False
    error_message = "Processing failed"

    try:
        success = await archive_processor.process_multivolume(job, messages)
    except Exception as e:
        logger.exception("Unhandled error while processing multipart job %s", job.job_id)
        success = False
        error_message = (str(e) or "Processing failed")[:300]

    await _deliver_and_cleanup(job, success, error_message)


async def _deliver_and_cleanup(job, success: bool, error_message: str):

    if job.has_output:

        all_names = [entry.path.name for entry in job.output_files]
        preview_names = all_names[:15]

        for attempt in range(2):
            try:
                await telegram_service.send_result(
                    Protocol.create_result(
                        user_id=job.user_id,
                        job_id=job.job_id,
                        files=preview_names,
                        target_chat_id=job.options.target_chat_id,
                    )
                )
                break
            except FloodWaitError as e:
                if attempt == 1:
                    logger.warning(
                        "Giving up on result summary after flood wait (%s)",
                        job.job_id,
                    )
                    break
                await asyncio.sleep(e.seconds + 1)
            except Exception:
                # This is just an informational ping; a failure here (e.g.
                # the message ended up too long for a huge batch) must
                # never block the actual file uploads below.
                logger.exception(
                    "Failed to send result summary for job %s", job.job_id
                )
                break

        for entry in job.output_files:

            if entry.uploaded:
                continue  # already delivered live during archive processing

            await upload_entry(job, entry)

            # Small pacing delay to avoid tripping flood limits on jobs
            # that unpack into dozens/hundreds of files (e.g. archives).
            if len(job.output_files) > 5:
                await asyncio.sleep(0.5)

        try:
            await telegram_service.send_info(
                Protocol.create_done(
                    user_id=job.user_id,
                    job_id=job.job_id,
                    target_chat_id=job.options.target_chat_id,
                )
            )
        except Exception:
            logger.exception(
                "Failed to send completion notice for job %s", job.job_id
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
