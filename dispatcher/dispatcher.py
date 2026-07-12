from core.constants import JobStatus
from core.logger import get_logger
from core.registry import get_registered_processors

# Importing these triggers each processor's @register_processor(...)
# decorator. To add a new file type: create processors/your_type.py with
# a @register_processor("YOUR_TYPE") class, then add one import line here.
import processors.video       # noqa: F401
import processors.audio       # noqa: F401
import processors.pdf         # noqa: F401
import processors.archive     # noqa: F401

logger = get_logger(__name__)


class Dispatcher:

    def __init__(self):

        self._processor_classes = get_registered_processors()
        self._instances = {}

    def _get_processor(self, file_type: str):

        if file_type not in self._instances:

            cls = self._processor_classes.get(file_type)

            if cls is None:
                return None

            self._instances[file_type] = cls()

        return self._instances[file_type]

    async def dispatch(self, job):

        file_type = (job.file_type or "").upper()

        processor = self._get_processor(file_type)

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
