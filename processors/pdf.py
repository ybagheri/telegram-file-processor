from __future__ import annotations

from core.constants import JobStatus
from core.logger import get_logger

from services.media import media_service

logger = get_logger(__name__)

COMPRESS_THRESHOLD_MB = 50


class PDFProcessor:

    async def process(self, job):

        logger.info(f"PDF processing started ({job.job_id})")

        if job.input_file is None:
            raise ValueError("Input file not found")

        job.set_status(JobStatus.PROCESSING)

        output = job.resolve_output_dir() / (job.stem + ".pdf")

        size_mb = media_service.size(job.input_file) / (1024 * 1024)

        if size_mb <= COMPRESS_THRESHOLD_MB:
            media_service.copy(job.input_file, output)

        else:
            try:
                import fitz  # PyMuPDF

                document = fitz.open(job.input_file)

                document.save(
                    str(output),
                    garbage=4,
                    deflate=True,
                    clean=True,
                )

                document.close()

                if not media_service.exists(output):
                    raise RuntimeError("Compression produced no output file")

                # Compression sometimes produces a larger file than the
                # original (already-optimized PDFs). Keep whichever is smaller.
                if media_service.size(output) >= media_service.size(job.input_file):
                    media_service.copy(job.input_file, output)

            except Exception as e:
                logger.warning(
                    f"PDF compression failed, keeping original ({job.job_id}): {e}"
                )
                media_service.copy(job.input_file, output)

        job.add_output(output, kind="document")

        job.set_status(JobStatus.COMPLETED)

        logger.info(f"PDF processing finished ({job.job_id})")

        return True
