from core.logger import get_logger

logger = get_logger(__name__)


class ArchiveProcessor:

    async def process(self, job):

        logger.info(f"Archive processing started ({job.job_id})")

        if job.input_file is None:
            raise ValueError("Input file not found")

        # TODO
        # Detect password
        # Ask password
        # Extract
        # Process extracted files

        logger.info(f"Archive processing finished ({job.job_id})")
