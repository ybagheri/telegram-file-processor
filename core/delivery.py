from __future__ import annotations

import asyncio

from telethon.errors import FloodWaitError

from core.logger import get_logger
from core.protocol import Protocol

from services.telegram import telegram_service

logger = get_logger(__name__)


async def upload_entry(job, entry) -> bool:
    """Uploads one OutputFile now, with the correct Telegram delivery
    behaviour for its kind (real video/audio/voice vs. plain document),
    flood-wait retry, and cleanup afterward. Marks entry.uploaded so it's
    never delivered twice. Returns whether the upload succeeded."""

    if entry.uploaded:
        return True

    output = entry.path

    force_document = True
    voice_note = False
    video_attrs = None
    audio_attrs = None

    if entry.kind == "voice":
        force_document = False
        voice_note = True

    elif entry.kind == "photo":
        force_document = False

    elif entry.kind == "video":
        force_document = job.options.upload_as != "video"
        if not force_document:
            video_attrs = {
                "duration": entry.duration,
                "width": entry.width,
                "height": entry.height,
            }

    elif entry.kind == "audio":
        force_document = False
        audio_attrs = {
            "duration": entry.duration,
            "title": entry.title,
            "performer": entry.artist,
        }

    success = False

    for attempt in range(2):

        try:
            await telegram_service.upload_file(
                output,
                Protocol.create_result(
                    user_id=job.user_id,
                    job_id=job.job_id,
                    files=[output.name[:200]],
                    target_chat_id=job.options.target_chat_id,
                ),
                force_document=force_document,
                voice_note=voice_note,
                thumb=entry.thumbnail,
                video_attributes=video_attrs,
                audio_attributes=audio_attrs,
            )
            success = True
            break

        except FloodWaitError as e:
            if attempt == 1:
                logger.exception(
                    "Gave up uploading %s after flood wait (%s)",
                    output.name, job.job_id,
                )
                break
            logger.warning(
                "Flood wait (%ss) while uploading %s, retrying once",
                e.seconds, output.name,
            )
            await asyncio.sleep(e.seconds + 1)

        except Exception:
            # One bad file (too large, corrupted, etc.) must not stop the
            # rest of a batch from being delivered.
            logger.exception(
                "Failed to upload output file %s for job %s",
                output.name, job.job_id,
            )
            break

    entry.uploaded = True

    # Free disk immediately regardless of outcome — no reason to keep a
    # file around once we've either delivered it or given up on it.
    for path in (output, entry.thumbnail):
        if path and path.exists():
            try:
                path.unlink()
            except OSError:
                pass

    return success
