from core.logger import get_logger

logger = get_logger(__name__)


class AudioProcessor:

    async def process(self, job):

        logger.info(f"Audio processing started ({job.job_id})")

        if job.input_file is None:
            raise ValueError("Input file not found")

        # TODO
        # Artist
        # Title
        # Metadata

        job.add_output(job.input_file)

        logger.info(f"Audio processing finished ({job.job_id})")
