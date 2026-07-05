from pathlib import Path
from core.logger import get_logger
from core.job import Job

logger = get_logger(__name__)

class ArchiveProcessor:
    async def process(self, job: Job):
        input_file = job.input_file
        output_dir = job.folders["extracted"]
        
        logger.info(f"Processing archive: {job.job_id}")
        
        try:
            # TODO: zip, rar, 7z extraction + password support
            # For now just placeholder
            logger.info("Archive extraction logic to be implemented")
            job.status = "completed"
        except Exception as e:
            logger.error(f"Archive processing failed: {e}")
            raise
