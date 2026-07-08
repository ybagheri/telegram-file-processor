from __future__ import annotations

from pathlib import Path

from config import (
    Metadata,
    Paths,
    Processing,
)

from core.constants import JobStatus
from core.logger import get_logger

from services.media import media_service
from services.tags import tag_service

logger = get_logger(__name__)


class VideoProcessor:

    async def process(self, job):

        logger.info(
            "Video job started (%s)",
            job.job_id,
        )

        job.set_status(
            JobStatus.PROCESSING,
        )

        quality = job.options.quality.lower()

        if quality == "mp3":
            return await self.extract_mp3(job)

        if quality == "m4a":
            return await self.extract_m4a(job)

        if quality == "voice":
            return await self.extract_voice(job)

        return await self.convert(job)

    # =====================================================
    # Convert Video
    # =====================================================

    async def convert(
        self,
        job,
    ):

        profile = self.get_profile(
            job.options.quality,
        )

        output = self.build_output_name(
            job,
            ".mp4",
        )

        ok = await media_service.convert_video(
            input_file=job.input_file,
            output_file=output,
            width=profile["width"],
            height=profile["height"],
            crf=profile["crf"],
            preset=profile["preset"],
            logo=Paths.LOGO_FILE,
        )

        if not ok:

            job.set_status(
                JobStatus.FAILED,
            )

            return False

        job.add_output(
            output,
        )

        thumb = self.build_thumbnail_name(
            job,
            output,
        )

        ok = await media_service.generate_thumbnail(
            output,
            thumb,
            Processing.THUMBNAIL_SECOND,
        )

        if ok:

            job.set_thumbnail(
                thumb,
            )

        self.update_video_info(
            job,
            output,
        )

        tag_service.update_video(
            output,
            title=self.get_title(
                job,
            ),
            artist=self.get_artist(
                job,
            ),
            cover=job.thumbnail,
        )

        job.set_status(
            JobStatus.COMPLETED,
        )

        logger.info(
            "Video completed (%s)",
            job.job_id,
        )

        return True

    # =====================================================
    # Extract MP3
    # =====================================================

    async def extract_mp3(
        self,
        job,
    ):

        output = self.build_output_name(
            job,
            ".mp3",
        )

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
            title=self.get_title(
                job,
            ),
            artist=self.get_artist(
                job,
            ),
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
    # Extract M4A
    # =====================================================

    async def extract_m4a(
        self,
        job,
    ):

        output = self.build_output_name(
            job,
            ".m4a",
        )

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
            title=self.get_title(
                job,
            ),
            artist=self.get_artist(
                job,
            ),
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
    # Extract Voice (OGG/Opus)
    # =====================================================

    async def extract_voice(
        self,
        job,
    ):

        output = self.build_output_name(
            job,
            ".ogg",
        )

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
            title=self.get_title(
                job,
            ),
            artist=self.get_artist(
                job,
            ),
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

    def get_profile(
        self,
        quality: str,
    ) -> dict:

        return Processing.VIDEO_PROFILES.get(
            quality,
            Processing.VIDEO_PROFILES[Processing.DEFAULT_VIDEO_QUALITY],
        )

    def build_output_name(
        self,
        job,
        extension: str,
    ) -> Path:

        return job.output_dir / (f"{job.stem}{extension}")

    def build_thumbnail_name(
        self,
        job,
        output: Path,
    ) -> Path:

        return job.thumbs_dir / (f"{output.stem}.jpg")

    # =====================================================
    # Metadata Helpers
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

    # =====================================================
    # Media Info
    # =====================================================

    def update_video_info(
        self,
        job,
        output: Path,
    ):

        info = media_service.get_video_info(
            output,
        )

        job.update_media_info(
            duration=info.get(
                "duration",
                0,
            ),
            width=info.get(
                "width",
                0,
            ),
            height=info.get(
                "height",
                0,
            ),
            bitrate=info.get(
                "bitrate",
                0,
            ),
        )

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

    # =====================================================
    # Common Helpers
    # =====================================================

    def get_profile(
        self,
        quality: str,
    ) -> dict:

        return Processing.VIDEO_PROFILES.get(
            quality,
            Processing.VIDEO_PROFILES[Processing.DEFAULT_VIDEO_QUALITY],
        )

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

    def update_video_info(
        self,
        job,
        output: Path,
    ):

        info = media_service.get_video_info(
            output,
        )

        job.update_media_info(
            duration=info.get(
                "duration",
                0,
            ),
            width=info.get(
                "width",
                0,
            ),
            height=info.get(
                "height",
                0,
            ),
            bitrate=info.get(
                "bitrate",
                0,
            ),
        )

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
