from __future__ import annotations

from pathlib import Path

from config import Metadata
from core.constants import JobStatus
from core.logger import get_logger

from services.media import media_service
from services.tags import tag_service

logger = get_logger(__name__)


class AudioProcessor:

    async def process(self, job):

        logger.info(f"Audio processing started ({job.job_id})")

        if job.input_file is None:
            raise ValueError("Input file not found")

        job.set_status(JobStatus.PROCESSING)

        suffix = job.input_file.suffix.lower() or ".mp3"
        output = job.output_dir / (job.stem + suffix)

        if job.input_file != output:
            media_service.copy(job.input_file, output)
        else:
            output = job.input_file

        try:
            info = media_service.get_audio_info(output)

            job.update_media_info(
                duration=info["duration"],
                bitrate=info["bitrate"],
            )
        except Exception as e:
            logger.warning(f"Could not read audio info ({job.job_id}): {e}")

        if job.options.custom_thumbnail and Path(job.options.custom_thumbnail).exists():
            cover = job.thumbs_dir / "cover.jpg"
            await media_service.normalize_thumbnail(Path(job.options.custom_thumbnail), cover)
            job.set_thumbnail(cover)

        title = job.options.title or tag_service.build_title(job.original_name)
        artist = job.options.artist or Metadata.DEFAULT_ARTIST

        tag_service.update_audio(
            output,
            title=title,
            artist=artist,
            cover=job.thumbnail,
        )

        job.add_output(output)

        job.set_status(JobStatus.COMPLETED)

        logger.info(f"Audio processing finished ({job.job_id})")

        return True
