import shutil
from pathlib import Path
from core.logger import get_logger

logger = get_logger(__name__)

def create_job_folders(job: 'Job', base_downloads: Path):
    """Create all folders for a job"""
    return job.create_folders(base_downloads)

def cleanup_job(job_dir: Path):
    """Safely clean up job directory"""
    try:
        if job_dir.exists():
            shutil.rmtree(job_dir)
            logger.info(f"Cleaned up job directory: {job_dir}")
    except Exception as e:
        logger.error(f"Cleanup failed for {job_dir}: {e}")
