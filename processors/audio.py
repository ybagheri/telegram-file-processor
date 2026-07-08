from __future__ import annotations

from pathlib import Path

from config import (
    Metadata,
    Processing,
)

from core.constants import JobStatus
from core.logger import get_logger

from services.media import media_service
from services.tags import tag_service

logger = get_logger(__name__)


class AudioProcessor:

    async def process(self, job):

        logger.info(
            "Audio job started (%s)",
            job.job_id,
        )

        job.set_status(
            JobStatus.PROCESSING,
        )

        quality = job.options.quality.lower()

        if quality == "mp3":

            return await self.to_mp3(job)

        if quality == "m4a":

            return await self.to_m4a(job)

        if quality == "voice":

            return await self.to_voice(job)

        logger.warning(
            "Unknown output format (%s)",
            quality,
        )

        job.set_status(
            JobStatus.FAILED,
        )

        return False

    # =====================================================
    # MP3
    # =====================================================

    async def to_mp3(
        self,
        job,
    ):

        output = job.output_dir / f"{job.stem}.mp3"

        ok = await media_service.extract_mp3(

            job.input_file,

            output,

            bitrate=Processing.DEFAULT_AUDIO_BITRATE,

        )

        if not ok:

            job.set_status(
                JobStatus.FAILED,
            )

            return False

        job.add_output(
            output,
        )

        self.update_audio_info(
            job,
            output,
        )

        tag_service.update_mp3(

            output,

            title=self.get_title(job),

            artist=self.get_artist(job),

            album=job.options.album,

            comment=job.options.comment,

            cover=job.thumbnail,

        )

        job.set_status(
            JobStatus.COMPLETED,
        )

        logger.info(
            "MP3 completed (%s)",
            job.job_id,
        )

        return True

    # =====================================================
    # M4A
    # =====================================================

    async def to_m4a(
        self,
        job,
    ):

        output = job.output_dir / f"{job.stem}.m4a"

        ok = await media_service.extract_m4a(

            job.input_file,

            output,

            bitrate=Processing.DEFAULT_AUDIO_BITRATE,

        )

        if not ok:

            job.set_status(
                JobStatus.FAILED,
            )

            return False

        job.add_output(
            output,
        )

        self.update_audio_info(
            job,
            output,
        )

        tag_service.update_m4a(

            output,

            title=self.get_title(job),

            artist=self.get_artist(job),

            album=job.options.album,

            comment=job.options.comment,

        )

        job.set_status(
            JobStatus.COMPLETED,
        )

        logger.info(
            "M4A completed (%s)",
            job.job_id,
        )

        return True
        
    # =====================================================
    # Voice (OGG / Opus)
    # =====================================================

    async def to_voice(
        self,
        job,
    ):

        output = job.output_dir / f"{job.stem}.ogg"

        ok = await media_service.extract_voice(

            job.input_file,

            output,

        )

        if not ok:

            job.set_status(
                JobStatus.FAILED,
            )

            return False

        job.add_output(
            output,
        )

        self.update_audio_info(
            job,
            output,
        )

        tag_service.update_ogg(

            output,

            title=self.get_title(job),

            artist=self.get_artist(job),

        )

        job.set_status(
            JobStatus.COMPLETED,
        )

        logger.info(
            "Voice completed (%s)",
            job.job_id,
        )

        return True

    # =====================================================
    # Helpers
    # =====================================================

    def get_title(
        self,
        job,
    ) -> str:

        if job.options.title:

            return job.options.title

        return tag_service.build_title(
            job.original_name,
        )

    def get_artist(
        self,
        job,
    ) -> str:

        if job.options.artist:

            return job.options.artist

        return Metadata.DEFAULT_ARTIST

    def update_audio_info(
        self,
        job,
        output: Path,
    ):

        info = media_service.get_audio_info(
            output,
        )

        job.update_media_info(

            duration=info.get(
                "duration",
                0,
            ),

            bitrate=info.get(
                "bitrate",
                0,
            ),

        )
