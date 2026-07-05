from pathlib import Path
from core.logger import get_logger

logger = get_logger(__name__)

def cleanup_job(job_dir: Path):
    """Clean up job directory"""
    if job_dir.exists():
        logger.info(f"Cleaning up {job_dir}")
        # shutil.rmtree(job_dir)  # بعد از تست فعال شود
