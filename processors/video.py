from __future__ import annotations

from pathlib import Path

from config import (
    Metadata,
    Processing,
    Paths,
)

from core.constants import JobStatus
from core.logger import get_logger

from services.media import media_service
from services.tags import tag_service

logger = get_logger(__name__)


class VideoProcessor:

    async def process(self, job):

        logger.info("Video job started (%s)", job.job_id)

        job.set_status(JobStatus.PROCESSING)

        quality = (job.options.quality or "").lower()

        if quality == "mp3":
            return await self.extract_mp3(job)

        if quality == "m4a":
            return await self.extract_m4a(job)

        if quality == "voice":
            return await self.extract_voice(job)

        return await self.convert(job)

    # =====================================================
    # Convert (re-encode + watermark + thumbnail + tags)
    # =====================================================

    async def convert(self, job):

        profile = Processing.VIDEO_PROFILES.get(
            job.options.quality,
            Processing.VIDEO_PROFILES[Processing.DEFAULT_VIDEO_QUALITY],
        )

        output = job.output_dir / (job.stem + ".mp4")

        logo_path = None

        if job.options.watermark:
            logo_path = Path(job.options.logo_path) if job.options.logo_path else Paths.LOGO_FILE

        ok = await media_service.convert_video(
            input_file=job.input_file,
            output_file=output,
            width=profile["width"],
            height=profile["height"],
            crf=profile["crf"],
            preset=profile["preset"],
            logo=logo_path,
            logo_position=job.options.logo_position,
        )

        if not ok:
            job.set_status(JobStatus.FAILED)
            return False

        job.add_output(output)

        thumb = job.thumbs_dir / (output.stem + ".jpg")

        if job.options.custom_thumbnail and Path(job.options.custom_thumbnail).exists():
            await media_service.normalize_thumbnail(Path(job.options.custom_thumbnail), thumb)
            job.set_thumbnail(thumb)
        else:
            thumb_ok = await media_service.generate_thumbnail(
                output,
                thumb,
                job.options.thumbnail_second or Processing.THUMBNAIL_SECOND,
            )

            if thumb_ok:
                job.set_thumbnail(thumb)

        info = media_service.get_video_info(output)

        job.update_media_info(
            duration=info["duration"],
            width=info["width"],
            height=info["height"],
            bitrate=info["bitrate"],
        )

        title = job.options.title or tag_service.build_title(job.original_name)

        tag_service.update_audio(
            output,
            title=title,
            artist=job.options.artist or Metadata.DEFAULT_ARTIST,
            cover=job.thumbnail,
        )

        job.set_status(JobStatus.COMPLETED)

        logger.info("Video completed (%s)", job.job_id)

        return True

    # =====================================================
    # MP3
    # =====================================================

    async def extract_mp3(self, job):

        output = job.output_dir / (job.stem + ".mp3")

        ok = await media_service.extract_mp3(
            job.input_file,
            output,
            bitrate=Processing.DEFAULT_AUDIO_BITRATE,
        )

        if not ok:
            job.set_status(JobStatus.FAILED)
            return False

        job.add_output(output)

        info = media_service.get_audio_info(output)

        job.update_media_info(
            duration=info["duration"],
            bitrate=info["bitrate"],
        )

        title = job.options.title or tag_service.build_title(job.original_name)

        tag_service.update_mp3(
            output,
            title=title,
            artist=job.options.artist or Metadata.DEFAULT_ARTIST,
            cover=job.thumbnail,
        )

        job.set_status(JobStatus.COMPLETED)

        logger.info("MP3 completed (%s)", job.job_id)

        return True

    # =====================================================
    # M4A
    # =====================================================

    async def extract_m4a(self, job):

        output = job.output_dir / (job.stem + ".m4a")

        ok = await media_service.extract_m4a(
            job.input_file,
            output,
            bitrate=Processing.DEFAULT_AUDIO_BITRATE,
        )

        if not ok:
            job.set_status(JobStatus.FAILED)
            return False

        job.add_output(output)

        info = media_service.get_audio_info(output)

        job.update_media_info(
            duration=info["duration"],
            bitrate=info["bitrate"],
        )

        title = job.options.title or tag_service.build_title(job.original_name)

        tag_service.update_m4a(
            output,
            title=title,
            artist=job.options.artist or Metadata.DEFAULT_ARTIST,
        )

        job.set_status(JobStatus.COMPLETED)

        logger.info("M4A completed (%s)", job.job_id)

        return True

    # =====================================================
    # Voice note (opus/ogg, mono, low bitrate)
    # =====================================================

    async def extract_voice(self, job):

        output = job.output_dir / (job.stem + ".ogg")

        ok = await media_service.extract_voice(
            job.input_file,
            output,
        )

        if not ok:
            job.set_status(JobStatus.FAILED)
            return False

        job.add_output(output)

        info = media_service.get_audio_info(output)

        job.update_media_info(
            duration=info["duration"],
            bitrate=info["bitrate"],
        )

        job.set_status(JobStatus.COMPLETED)

        logger.info("Voice completed (%s)", job.job_id)

        return True
