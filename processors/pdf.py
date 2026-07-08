from __future__ import annotations

from core.constants import JobStatus
from core.logger import get_logger

from services.pdf import pdf_service

logger = get_logger(__name__)


class PDFProcessor:

    async def process(
        self,
        job,
    ):

        logger.info(
            "PDF job started (%s)",
            job.job_id,
        )

        job.set_status(
            JobStatus.PROCESSING,
        )

        output = job.output_dir / (
            f"{job.stem}.pdf"
        )

        ok = await pdf_service.optimize(

            input_file=job.input_file,

            output_file=output,

        )

        if not ok:

            logger.error(
                "PDF optimization failed."
            )

            job.set_status(
                JobStatus.FAILED,
            )

            return False

        job.add_output(
            output,
        )

        job.set_status(
            JobStatus.COMPLETED,
        )

        logger.info(
            "PDF completed (%s)",
            job.job_id,
        )

        return True
