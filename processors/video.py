from core.logger import get_logger
from core.job import Job

logger = get_logger(__name__)

class VideoProcessor:
    async def process(self, job: Job):
        logger.info(f"Processing video job {job.job_id}")
        # TODO: Video processing (quality, logo, extract audio...)
        job.status = "completed"
