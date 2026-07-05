from pathlib import Path
import fitz  # PyMuPDF
from core.logger import get_logger
from core.job import Job

logger = get_logger(__name__)

class PDFProcessor:
    async def process(self, job: Job):
        input_file = job.input_file
        output_dir = job.folders["output"]
        
        logger.info(f"Processing PDF: {job.job_id}")
        
        output_file = output_dir / f"compressed_{input_file.name}"
        
        try:
            doc = fitz.open(input_file)
            doc.save(str(output_file), deflate=True, garbage=4)
            doc.close()
            
            job.output_files.append(output_file)
            logger.info("PDF compressed")
        except Exception as e:
            logger.error(f"PDF processing failed: {e}")
            raise
