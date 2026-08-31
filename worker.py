import asyncio
import re
import shutil
import time

from dataclasses import asdict

from telethon import events
from telethon.errors import FloodWaitError

from config import Heartbeat, Paths, Processing, Telegram
from core.constants import MessageType
from core.delivery import upload_entry
from core.error_reporting import (
    ErrorCode,
    JobStage,
    USER_SAFE_ERROR_TEXT,
    build_admin_report,
)
from core.job import Job
from core.job_options import JobOptions
from core.logger import get_logger
from core.password_broker import password_broker
from core.protocol import Protocol
from dispatcher.dispatcher import Dispatcher
from processors.archive import ArchiveProcessor
from services.media import media_service
from services.progress import ProgressReporter
from services.telegram import telegram_service
from services.url_downloader import (
    DEFAULT_TIMEOUT_SECONDS,
    REASON_TOO_LARGE,
    URLDownloadError,
    describe_download_error,
    download_to_disk,
    resolve_download_url,
)
from utils.filetype import FileTypeDetector
from utils.permissions import get_tier_from_payload, max_file_size_for_tier
from utils.text import strip_excluded
from utils.url_validation import filename_from_url

from pathlib import Path   

logger = get_logger(__name__)

dispatcher = Dispatcher()
archive_processor = ArchiveProcessor()


def _extension_from_mime(mime: str) -> str:
    """Best-effort extension for common media types when Telegram gives
    no file_name. Returns empty string when unknown."""
    mime = (mime or "").lower().strip()
    mapping = {
        "video/mp4": ".mp4",
        "video/webm": ".webm",
        "video/quicktime": ".mov",
        "video/x-matroska": ".mkv",
        "video/x-msvideo": ".avi",
        "audio/mpeg": ".mp3",
        "audio/mp4": ".m4a",
        "audio/aac": ".aac",
        "audio/ogg": ".ogg",
        "audio/opus": ".opus",
        "audio/wav": ".wav",
        "audio/x-wav": ".wav",
        "application/pdf": ".pdf",
        "application/zip": ".zip",
        "application/x-rar": ".rar",
        "application/vnd.rar": ".rar",
        "application/x-7z-compressed": ".7z",
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "image/gif": ".gif",
    }
    return mapping.get(mime, "")


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


def _file_too_large_message(size_bytes: int, limit_bytes: int | None = None) -> str:
    """Persian text delivered to the user when a job is rejected for
    exceeding its tier's file-size cap — sent through the bridge as a
    Protocol.create_error payload (handlers/bridge.py prefixes it with
    "❌ خطا:"). The limit defaults to the global MAX_FILE_SIZE."""

    if limit_bytes is None:
        limit_bytes = Processing.MAX_FILE_SIZE

    limit_gb = limit_bytes / (1024 * 1024 * 1024)
    size_gb = size_bytes / (1024 * 1024 * 1024)

    return (
        f"حجم فایل ({size_gb:.1f} گیگابایت) بیشتر از حد مجاز "
        f"({limit_gb:.1f} گیگابایت) است."
    )


def _size_limit_for_payload(payload: dict) -> int:
    """The tier-aware file-size cap for the user who submitted this job.
    The tier is resolved by the bot at submission time and carried in the
    job payload (see handlers/files.py::finalize_job); a missing/unknown
    tier falls back to trial — the most restrictive default."""

    return max_file_size_for_tier(get_tier_from_payload(payload))


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


async def _start_progress(job: Job):
    """Attaches a real ProgressReporter to the job and sends its first
    (download-stage) message. Best-effort: ProgressReporter.start()
    already swallows its own failures, so this never raises."""

    destination = job.options.target_chat_id or job.user_id

    job.progress = ProgressReporter(
        telegram_service.bot,
        destination,
        job.job_id,
    )

    await job.progress.start()


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

    await job.progress.finish()

    job.cleanup()


async def _reject_too_large(job: Job, size_bytes: int, limit_bytes: int | None = None):
    """Tells the user the job was rejected for size and aborts cleanly.
    Must be called before any download starts, so there's nothing on
    disk to clean up beyond the job's (empty) working directories."""

    if limit_bytes is None:
        limit_bytes = Processing.MAX_FILE_SIZE

    logger.warning(
        "Job %s rejected: declared size %s bytes exceeds the tier size limit (%s)",
        job.job_id,
        size_bytes,
        limit_bytes,
    )

    await telegram_service.send_error(
        Protocol.create_error(
            user_id=job.user_id,
            job_id=job.job_id,
            message=_file_too_large_message(size_bytes, limit_bytes),
        )
    )

    await job.progress.finish()

    job.cleanup()


# Lazily-created per-job processing semaphore (see
# _get_processing_semaphore). Module-level so both process_job and
# process_multipart_job share one limit.
_processing_semaphore: asyncio.Semaphore | None = None


def _get_processing_semaphore() -> asyncio.Semaphore:
    """The semaphore bounding how many jobs run ffmpeg/extraction at
    once. Created lazily from Processing.MAX_CONCURRENT_JOBS rather than
    at import time so tests can retune the limit by resetting
    worker._processing_semaphore to None."""

    global _processing_semaphore

    if _processing_semaphore is None:

        _processing_semaphore = asyncio.Semaphore(
            max(
                1,
                Processing.MAX_CONCURRENT_JOBS,
            )
        )

    return _processing_semaphore


def _job_report_context(job: Job, operation: str) -> dict:
    """The Job-derived fields every admin failure report carries."""

    return {
        "job_id": job.job_id,
        "user_id": job.user_id,
        "username": job.username,
        "chat_id": job.options.target_chat_id or None,
        "file_name": job.original_name,
        "file_size": job.file_size,
        "file_type": job.file_type,
        "operation": operation,
        "job_status": job.status,
        "options": asdict(job.options),
    }


async def _report_job_failure(
    job: Job,
    operation: str,
    *,
    stage: JobStage,
    code: ErrorCode,
    exception: BaseException | None = None,
    exception_message: str | None = None,
    duration_seconds: float | None = None,
    extra_lines: list[str] | None = None,
    url: str = "",
):
    """Single structured-failure entry point for job-stage errors: one
    structured log line (code/stage/operation are greppable) plus one
    sanitized HTML report DM'd to the admins through the bridge. Never
    raises — a reporting failure must not mask the original error or
    take the pipeline down."""

    logger.error(
        "job_id=%s stage=%s code=%s operation=%s user_id=%s file=%r: %s",
        job.job_id,
        stage.value,
        code.value,
        operation,
        job.user_id,
        job.original_name,
        exception_message
        or (f"{type(exception).__name__}: {exception}" if exception else "failed"),
        exc_info=exception,
    )

    # Everything below is best-effort: a bug IN the reporting itself must
    # never escape into the pipeline — the job's own error handling (and
    # the user's safe error line) has already been decided by the caller.
    try:
        report = build_admin_report(
            stage=stage,
            code=code,
            exception=exception,
            exception_message=exception_message,
            duration_seconds=duration_seconds,
            extra_lines=extra_lines,
            url=url,
            **_job_report_context(job, operation),
        )

        await telegram_service.send_error(
            Protocol.create_admin_error(
                report=report,
                user_id=job.user_id,
                job_id=job.job_id,
            )
        )
    except Exception:
        logger.exception(
            "Failed to send the admin error report for job %s", job.job_id
        )


def _payload_report_context(payload: dict, job_label: str) -> dict:
    """Report fields for failures that happen BEFORE a Job object exists
    (media fetch, payload dispatch) — everything comes from the raw
    payload, so file_size/options-level detail is whatever the bot sent."""

    return {
        "job_id": job_label,
        "user_id": payload.get("user_id"),
        "username": payload.get("username", ""),
        "file_name": payload.get("file_name", ""),
        "file_type": payload.get("file_type", ""),
        "operation": "payload_dispatch",
    }


async def _report_payload_failure(payload: dict, exception: BaseException):
    """Failure outside the Job lifecycle (e.g. the bridge media message
    can't be fetched). Previously this was only logged — the user got
    silence and the operator had nothing but the worker log file. Now:
    structured log + admin report + one safe line to the user."""

    message_id = payload.get("message_id")

    part_ids = payload.get("part_message_ids")

    if message_id:
        job_label = f"unknown:{message_id}"
    elif part_ids:
        job_label = f"unknown:parts-{part_ids[0]}"
    else:
        job_label = "unknown"

    logger.error(
        "job_id=%s stage=%s code=%s: %s",
        job_label,
        JobStage.QUEUE.value,
        ErrorCode.QUEUE_FAILED.value,
        f"{type(exception).__name__}: {exception}",
        exc_info=exception,
    )

    user_id = payload.get("user_id")

    # Same best-effort guarantee as _report_job_failure: building the
    # report from raw payload data must never raise into the caller.
    try:
        report = build_admin_report(
            stage=JobStage.QUEUE,
            code=ErrorCode.QUEUE_FAILED,
            exception=exception,
            **_payload_report_context(payload, job_label),
        )

        await telegram_service.send_error(
            Protocol.create_admin_error(
                report=report,
                user_id=user_id,
                job_id=job_label,
            )
        )
    except Exception:
        logger.exception(
            "Failed to send the admin error report for payload %s", job_label
        )

    if not user_id:
        return

    try:
        await telegram_service.send_error(
            Protocol.create_error(
                user_id=user_id,
                job_id=job_label,
                message=USER_SAFE_ERROR_TEXT,
            )
        )
    except Exception:
        logger.exception(
            "Failed to send the user error message for payload %s", job_label
        )


async def _send_user_safe_error(job: Job, message: str = USER_SAFE_ERROR_TEXT):
    """One safe, internal-free error line to the user through the bridge.
    Failing to deliver even this must not raise (the failure report has
    already gone out; the worker log has the rest)."""

    try:
        await telegram_service.send_error(
            Protocol.create_error(
                user_id=job.user_id,
                job_id=job.job_id,
                message=message,
                target_chat_id=job.options.target_chat_id,
            )
        )
    except Exception:
        logger.exception(
            "Failed to send the user error message for job %s", job.job_id
        )


async def process_job(payload: dict):

    started = time.monotonic()

    part_ids = payload.get("part_message_ids")

    if part_ids and len(part_ids) > 1:
        await process_multipart_job(payload, part_ids)
        return

    message_id = payload.get("message_id")

    # URL-upload mode: the payload carries a direct http(s) link instead
    # of a bridge message_id pointing at forwarded media — the bytes get
    # onto disk by streaming the URL, then everything downstream
    # (guards, dispatch, delivery) runs exactly as for an upload.
    url = payload.get("url")

    if url:
        await process_url_job(payload, url)
        return

    # The bridge message that carries the JSON payload is a *separate*
    # text message from the one that carries the actual file (which was
    # forwarded/copied into the group by bot.py). We fetch the real
    # media message by its id instead of relying on whatever message
    # happened to trigger this handler.
    try:
        message = await telegram_service.client.get_messages(
            Telegram.GROUP_ID,
            ids=message_id,
        )
    except Exception as e:
        await _report_payload_failure(payload, e)
        return

    if message is None or not message.media:

        # This used to be log-and-return: the user got silence and the
        # operator had to grep the worker log to even notice. The bot
        # validated the media at submission time, so this is a real
        # pipeline failure (message deleted/expired, wrong id, ...) and
        # is reported as one.
        await _report_payload_failure(
            payload,
            RuntimeError(
                f"bridge media message {message_id} has no media"
            ),
        )
        return

    job = Job(
        user_id=payload["user_id"],
        message_id=message_id,
        options=_build_options(payload.get("options", {})),
    )

    job.username = payload.get("username", "")

    await _start_progress(job)

    # ------------------------------------------------------------------
    # Resolve a usable filename
    # ------------------------------------------------------------------
    filename = "input"
    mime_type = ""

    if message.file:
        filename = message.file.name or filename
        mime_type = message.file.mime_type or ""
        job.file_size = message.file.size or 0

    # Many videos/audios arrive without a file_name (especially when sent
    # as native video rather than document). Build a stable name with a
    # sensible extension so the path never becomes bare ".../input/input".
    if not filename or filename == "input" or Path(filename).suffix == "":
        ext = _extension_from_mime(mime_type) or ".bin"
        filename = f"{job.job_id}{ext}"

    job.original_name = strip_excluded(filename, job.options.exclude_text)
    job.mime_type = mime_type

    job.file_type = FileTypeDetector.detect(
        mime_type,
        filename,
    )
    
    # Tier-aware size enforcement: the user's account tier (resolved by
    # the bot at submission time, carried in the payload) decides the
    # cap. message.file.size is the declared size from Telegram
    # metadata, known before any bytes are transferred — reject here
    # rather than discovering the overrun mid-download.
    size_limit = _size_limit_for_payload(payload)

    if job.file_size > size_limit:
        await _reject_too_large(job, job.file_size, size_limit)
        return

    if not _has_enough_disk_space(job.file_size):
        await _reject_no_disk_space(job, job.file_size)
        return

    input_path = job.input_dir / filename

    job.input_file = await telegram_service.download(
        message,
        input_path,
        progress_callback=job.progress.make_download_callback(
            total=job.file_size or None,
            label=filename,
        ),
    )

    if job.input_file is None:

        # telegram_service.download already logs the underlying Telethon
        # exception — here we report the stage failure upward.
        await _report_job_failure(
            job,
            "single_file",
            stage=JobStage.DOWNLOAD,
            code=ErrorCode.DOWNLOAD_FAILED,
            exception_message=(
                f"Telethon download_media returned None for message {message_id}"
            ),
            duration_seconds=time.monotonic() - started,
        )

        await _send_user_safe_error(job)

        await job.progress.finish()

        job.cleanup()
        return

    try:
        # The semaphore wraps ONLY the processing step (ffmpeg/extract —
        # the CPU/IO-heavy part), not the download. asyncio.Semaphore
        # wakes waiters FIFO, and bridge events spawn these tasks in
        # arrival order, so queued jobs still process in the order they
        # arrived. An exception here propagates through the `async with`
        # and releases the slot — one broken job can't starve the rest.
        async with _get_processing_semaphore():
            job.progress.begin_processing(label="پردازش فایل")
            success = await dispatcher.dispatch(job)
    except Exception as e:

        success = False
        error_message = USER_SAFE_ERROR_TEXT

        await _report_job_failure(
            job,
            "single_file",
            stage=JobStage.PROCESSING,
            code=ErrorCode.PROCESSING_FAILED,
            exception=e,
            duration_seconds=time.monotonic() - started,
        )

    else:

        error_message = USER_SAFE_ERROR_TEXT

        if not success:

            # Graceful False (e.g. no processor for the file type). The
            # dispatcher/processor already logged the specific reason —
            # the report carries the job context so the operator can
            # correlate, no traceback exists in this path.
            await _report_job_failure(
                job,
                "single_file",
                stage=JobStage.PROCESSING,
                code=ErrorCode.PROCESSING_FAILED,
                exception_message=(
                    "dispatcher returned success=False "
                    "(see the preceding processor log lines for the reason)"
                ),
                duration_seconds=time.monotonic() - started,
            )

    await _deliver_and_cleanup(job, success, error_message)


def _url_download_error_text(reason: str, detail: str = "") -> str:
    """Persian feedback for a failed URL download, mapped from the
    REASON_* tokens of services/url_downloader.py. Delegates to
    describe_download_error() for everything except REASON_TOO_LARGE,
    where only the worker knows the user's actual tier limit."""

    if reason == REASON_TOO_LARGE:
        return _file_too_large_message(int(detail)) if detail.isdigit() else (
            "حجم فایل در لینک بیشتر از حد مجاز است."
        )

    return describe_download_error(reason, detail)


async def process_url_job(payload: dict, url: str):
    """URL-upload jobs: stream the file from the URL to disk on this
    server (respecting MAX_FILE_SIZE via Content-Length AND a hard cap
    while streaming, plus the free-disk-space guard), then feed it into
    the exact same dispatch/delivery pipeline as an uploaded file — no
    parallel pipeline, the only new piece is "bytes from a URL" instead
    of "bytes from a Telegram message"."""

    started = time.monotonic()

    job = Job(
        user_id=payload["user_id"],
        message_id=0,  # no bridge media message exists for URL jobs
        options=_build_options(payload.get("options", {})),
    )

    job.username = payload.get("username", "")

    await _start_progress(job)

    # Tier-aware caps for URL jobs too — a link must not be a way around
    # the user's account limits.
    size_limit = _size_limit_for_payload(payload)

    try:
        # Some hosts (picofile.com, for now) hand out a landing *page*
        # rather than a direct link — resolved here, before the filename
        # is derived from the URL, so a page URL like
        # ".../file/123/movie.zip.html" doesn't get mistaken for a
        # ".zip.html" file. download_to_disk() below re-resolves too
        # (cheap/idempotent — see services/url_downloader.py), so this
        # is purely about getting the filename right, not a second
        # source of truth.
        url = await asyncio.to_thread(
            resolve_download_url,
            url,
            DEFAULT_TIMEOUT_SECONDS,
        )

        filename = payload.get("file_name") or filename_from_url(url)

        job.original_name = strip_excluded(filename, job.options.exclude_text)

        job.file_type = payload.get("file_type") or FileTypeDetector.detect(
            "",
            filename,
        )

        input_path = job.input_dir / filename

        job.input_file = await download_to_disk(
            url,
            input_path,
            size_limit,
            progress_callback=job.progress.make_download_callback(label=filename),
        )
    except URLDownloadError as e:

        logger.warning(
            "URL download failed for job %s: %s (%s)",
            job.job_id,
            e.reason,
            e.detail,
        )

        # The user's mapped Persian text stays (it's already safe and
        # specific); the admin additionally gets the structured report.
        await _report_job_failure(
            job,
            "url_download",
            stage=JobStage.DOWNLOAD,
            code=ErrorCode.DOWNLOAD_FAILED,
            exception=e,
            url=url,
            duration_seconds=time.monotonic() - started,
        )

        try:
            await telegram_service.send_error(
                Protocol.create_error(
                    user_id=job.user_id,
                    job_id=job.job_id,
                    message=_url_download_error_text(e.reason, e.detail),
                )
            )
        except Exception:

            # Never let a failed user notification replace the structured
            # admin report / logging that already happened above.
            logger.exception(
                "Failed to send the user error message for job %s", job.job_id
            )

        await job.progress.finish()

        job.cleanup()
        return

    except Exception as e:

        # A non-URLDownloadError here used to propagate out of the whole
        # pipeline (user silence, no cleanup). Report it like any other
        # download failure.
        await _report_job_failure(
            job,
            "url_download",
            stage=JobStage.DOWNLOAD,
            code=ErrorCode.DOWNLOAD_FAILED,
            exception=e,
            url=url,
            duration_seconds=time.monotonic() - started,
        )

        await _send_user_safe_error(job)

        await job.progress.finish()

        job.cleanup()
        return

    job.file_size = media_service.size(job.input_file)

    # Same guards as uploaded files, now sized from the actual bytes on
    # disk (belt-and-braces on top of the downloader's tier-aware cap).
    if job.file_size > size_limit:
        await _reject_too_large(job, job.file_size, size_limit)
        return

    if not _has_enough_disk_space(job.file_size):
        await _reject_no_disk_space(job, job.file_size)
        return

    try:

        if payload.get("direct_upload"):

            # "Send it as-is" URL mode: skip the processing pipeline
            # entirely — the untouched file goes straight into the shared
            # delivery path as a document. No semaphore: this branch does
            # no ffmpeg/extraction, so it shouldn't occupy a processing
            # slot that real conversions are waiting on.
            job.add_output(job.input_file, kind="document")
            success = True

        else:
            # Same processing semaphore as every other job.
            async with _get_processing_semaphore():
                job.progress.begin_processing(label="پردازش فایل")
                success = await dispatcher.dispatch(job)

    except Exception as e:

        success = False
        error_message = USER_SAFE_ERROR_TEXT

        await _report_job_failure(
            job,
            "url_download",
            stage=JobStage.PROCESSING,
            code=ErrorCode.PROCESSING_FAILED,
            exception=e,
            url=url,
            duration_seconds=time.monotonic() - started,
        )

    else:

        error_message = USER_SAFE_ERROR_TEXT

        if not success:

            await _report_job_failure(
                job,
                "url_download",
                stage=JobStage.PROCESSING,
                code=ErrorCode.PROCESSING_FAILED,
                exception_message=(
                    "dispatcher returned success=False "
                    "(see the preceding processor log lines for the reason)"
                ),
                url=url,
                duration_seconds=time.monotonic() - started,
            )

    await _deliver_and_cleanup(job, success, error_message)


async def process_multipart_job(payload: dict, part_ids: list[int]):
    """A user-declared multi-volume RAR archive: the parts arrive as
    separate bridge messages (one per volume the user sent). Delegates the
    actual disk-conscious download/extract/upload cycle to
    ArchiveProcessor.process_multivolume — see processors/archive.py."""

    started = time.monotonic()

    try:
        messages = await telegram_service.client.get_messages(
            Telegram.GROUP_ID,
            ids=part_ids,
        )
    except Exception as e:
        await _report_payload_failure(payload, e)
        return

    messages = [m for m in messages if m is not None and m.media]

    if len(messages) < 2:

        # Same as the single-file "no media" case: the parts were
        # validated by the bot at submission, so a shrunken set is a
        # pipeline failure, not something to log-and-forget.
        await _report_payload_failure(
            payload,
            RuntimeError(
                f"multipart job has too few valid parts: {part_ids}"
            ),
        )
        return

    job = Job(
        user_id=payload["user_id"],
        message_id=part_ids[0],
        options=_build_options(payload.get("options", {})),
    )

    job.username = payload.get("username", "")

    await _start_progress(job)

    job.file_type = "ARCHIVE"

    first_name = (messages[0].file.name if messages[0].file else None) or "archive.rar"
    base_name = _strip_part_suffix(first_name)
    job.original_name = strip_excluded(base_name, job.options.exclude_text)

    # Tier-aware size enforcement: sum the declared size of every part
    # and reject before any volume is downloaded (same rule as
    # single-file jobs — a multi-volume set isn't a way around the
    # user's account limits).
    size_limit = _size_limit_for_payload(payload)

    total_size = sum(
        (m.file.size or 0)
        for m in messages
        if m.file
    )

    if total_size > size_limit:
        await _reject_too_large(job, total_size, size_limit)
        return

    # Same free-disk-space guard, sized from the declared *total* of the
    # volume set (volumes download on demand, but extraction output and
    # the sum of the parts still have to fit alongside each other).
    if not _has_enough_disk_space(total_size):
        await _reject_no_disk_space(job, total_size)
        return

    success = False
    error_message = USER_SAFE_ERROR_TEXT

    try:
        # Same processing semaphore as single-file jobs — a multi-volume
        # extraction is at least as heavy as anything it competes with.
        async with _get_processing_semaphore():
            job.progress.begin_processing(label="پردازش آرشیو چندبخشی")
            success = await archive_processor.process_multivolume(job, messages)
    except Exception as e:

        success = False

        await _report_job_failure(
            job,
            "multipart_archive",
            stage=JobStage.PROCESSING,
            code=ErrorCode.PROCESSING_FAILED,
            exception=e,
            duration_seconds=time.monotonic() - started,
        )

    else:

        if not success:

            await _report_job_failure(
                job,
                "multipart_archive",
                stage=JobStage.PROCESSING,
                code=ErrorCode.PROCESSING_FAILED,
                exception_message=(
                    "multi-volume processor returned success=False "
                    "(see the preceding processor log lines for the reason)"
                ),
                duration_seconds=time.monotonic() - started,
            )

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

        delivered = 0
        failed_uploads: list[str] = []

        for entry in job.output_files:

            if entry.uploaded:
                delivered += 1  # already delivered live during archive processing
                continue

            if await upload_entry(job, entry):
                delivered += 1
            else:
                # upload_entry already logged the underlying exception and
                # freed the disk — track it so the failure is visible to
                # the user and the admins instead of silently vanishing.
                failed_uploads.append(entry.path.name)

            # Small pacing delay to avoid tripping flood limits on jobs
            # that unpack into dozens/hundreds of files (e.g. archives).
            if len(job.output_files) > 5:
                await asyncio.sleep(0.5)

        if failed_uploads:

            await _report_job_failure(
                job,
                "delivery",
                stage=JobStage.UPLOAD,
                code=ErrorCode.UPLOAD_FAILED,
                exception_message=(
                    f"{len(failed_uploads)} of {len(job.output_files)} "
                    "output file(s) failed to upload"
                ),
                extra_lines=[
                    f"failed file: {name}"
                    for name in failed_uploads[:10]
                ],
            )

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

        await _send_user_safe_error(
            job,
            error_message if not job.has_output else "Some files failed to process",
        )

    elif failed_uploads and delivered == 0:

        # Processing "succeeded" but not a single output made it to
        # Telegram — without this, the user got the success flow with
        # zero files and no explanation.
        await _send_user_safe_error(job)

    try:
        await job.progress.finish()
    except Exception:
        logger.exception(
            "Failed to finish/remove the progress message for job %s",
            job.job_id,
        )

    try:
        job.cleanup()
    except Exception as e:

        # Cleanup runs at the very end of every path — a failure here
        # (locked file, permission) must not escape into the payload
        # dispatcher, but it also must not stay silent: it leaks disk
        # space on a resource-constrained VPS.
        await _report_job_failure(
            job,
            "cleanup",
            stage=JobStage.CLEANUP,
            code=ErrorCode.CLEANUP_FAILED,
            exception=e,
        )


async def heartbeat_loop():
    """Periodically sends a small HEARTBEAT message through the bridge
    group so bot.py can track "worker last seen" (its /status command
    reports staleness — an operator can notice a crashed worker without
    SSHing in). Failures are logged and retried on the next tick; a
    missed heartbeat must never take the worker down."""

    while True:

        try:
            await telegram_service.send_info(Protocol.create_heartbeat())
        except Exception:
            logger.exception("Failed to send bridge heartbeat")

        await asyncio.sleep(Heartbeat.INTERVAL_SECONDS)


async def _process_job_safe(payload: dict):
    """Top-level safety net for one bridge job task. process_job now
    reports stage failures itself; this only catches what escaped the
    whole pipeline (and would previously vanish into the log file with
    the user getting silence)."""

    try:
        await process_job(payload)
    except Exception as e:
        await _report_payload_failure(payload, e)


async def main():

    await telegram_service.start()

    logger.info("Worker started")

    asyncio.create_task(heartbeat_loop())

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
