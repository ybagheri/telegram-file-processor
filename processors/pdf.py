from core.logger import get_logger

logger = get_logger(__name__)


class PDFProcessor:

    async def process(self, job):

        logger.info(f"PDF processing started ({job.job_id})")

        if job.input_file is None:
            raise ValueError("Input file not found")

        # TODO
        # Compress PDF

        job.add_output(job.input_file)

        logger.info(f"PDF processing finished ({job.job_id})")
