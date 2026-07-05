from core.logger import get_logger
from core.job import Job

logger = get_logger(__name__)

class AudioProcessor:
    async def process(self, job: Job):
        logger.info(f"Processing audio job {job.job_id}")
        # TODO: Metadata, artist, title...
        job.status = "completed"
