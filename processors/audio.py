from __future__ import annotations

from pathlib import Path

from config import Metadata
from core.constants import JobStatus
from core.logger import get_logger
from core.registry import register_processor

from services.media import media_service
from services.tags import tag_service
from utils.text import strip_excluded

logger = get_logger(__name__)


@register_processor("AUDIO")
class AudioProcessor:

    async def process(self, job):

        logger.info(f"Audio processing started ({job.job_id})")

        if job.input_file is None:
            raise ValueError("Input file not found")

        job.set_status(JobStatus.PROCESSING)

        suffix = job.input_file.suffix.lower() or ".mp3"
        output = job.resolve_output_dir() / (job.stem + suffix)

        if job.input_file != output:
            media_service.copy(job.input_file, output)
        else:
            output = job.input_file

        entry = job.add_output(output, kind="audio")

        if entry is None:
            job.set_status(JobStatus.FAILED)
            return False

        try:
            info = media_service.get_audio_info(output)
            entry.duration = info["duration"]
        except Exception as e:
            logger.warning(f"Could not read audio info ({job.job_id}): {e}")

        cover = None

        if job.options.custom_thumbnail and Path(job.options.custom_thumbnail).exists():
            cover = job.thumbs_dir / f"cover_{job.job_id}_{len(job.output_files)}.jpg"
            await media_service.normalize_thumbnail(Path(job.options.custom_thumbnail), cover)
            entry.thumbnail = cover

        title = strip_excluded(job.options.title, job.options.exclude_text) or tag_service.build_title(job.original_name)
        artist = strip_excluded(job.options.artist, job.options.exclude_text) or Metadata.DEFAULT_ARTIST

        entry.title = title
        entry.artist = artist

        tag_service.update_audio(
            output,
            title=title,
            artist=artist,
            cover=cover,
        )

        job.set_status(JobStatus.COMPLETED)

        logger.info(f"Audio processing finished ({job.job_id})")

        return True
