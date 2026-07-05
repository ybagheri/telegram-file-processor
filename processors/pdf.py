from core.logger import get_logger
from core.job import Job

logger = get_logger(__name__)

class PDFProcessor:
    async def process(self, job: Job):
        logger.info(f"Processing PDF job {job.job_id}")
        # TODO: Compress if large
        job.status = "completed"
