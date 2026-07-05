from core.logger import get_logger
from processors.video import VideoProcessor
from processors.audio import AudioProcessor
from processors.pdf import PDFProcessor
from processors.archive import ArchiveProcessor

logger = get_logger(__name__)

class Dispatcher:
    def __init__(self):
        self.processors = {
            "VIDEO": VideoProcessor(),
            "AUDIO": AudioProcessor(),
            "PDF": PDFProcessor(),
            "ARCHIVE": ArchiveProcessor(),
        }

    async def dispatch(self, job):
        """Dispatch job to appropriate processor"""
        processor = self.processors.get(job.file_type)
        if processor:
            logger.info(f"Dispatching {job.file_type} job {job.job_id}")
            await processor.process(job)
        else:
            logger.error(f"No processor for type {job.file_type}")
