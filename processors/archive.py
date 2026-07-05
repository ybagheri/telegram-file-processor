from core.logger import get_logger
from core.job import Job

logger = get_logger(__name__)

class ArchiveProcessor:
    async def process(self, job: Job):
        logger.info(f"Processing archive job {job.job_id}")
        # TODO: Extract zip/rar/7z + password support
        job.status = "completed"
