from core.constants import JobStatus
from core.logger import get_logger

from processors.video import VideoProcessor
from processors.audio import AudioProcessor
from processors.pdf import PDFProcessor
from processors.archive import ArchiveProcessor

logger = get_logger(__name__)


class Dispatcher:

    def __init__(self):

        self._processors = {
            "VIDEO": VideoProcessor(),
            "AUDIO": AudioProcessor(),
            "PDF": PDFProcessor(),
            "ARCHIVE": ArchiveProcessor(),
        }

    async def dispatch(self, job):

        file_type = (job.file_type or "").upper()

        processor = self._processors.get(file_type)

        if processor is None:
            logger.error(
                "Unsupported file type: %s",
                file_type,
            )
            job.status = JobStatus.FAILED
            return False

        logger.info(
            "Job %s -> %s",
            job.job_id,
            processor.__class__.__name__,
        )

        job.status = JobStatus.PROCESSING

        try:

            result = await processor.process(job)

            job.status = JobStatus.COMPLETED

            logger.info(
                "Job %s completed",
                job.job_id,
            )

            return result if result is not None else True

        except Exception:

            logger.exception(
                "Processor failed for job %s",
                job.job_id,
            )

            job.status = JobStatus.FAILED

            return False