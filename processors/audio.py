from pathlib import Path
from core.logger import get_logger
from core.job import Job

logger = get_logger(__name__)

class AudioProcessor:
    async def process(self, job: Job):
        input_file = job.input_file
        output_dir = job.folders["output"]
        
        logger.info(f"Processing audio: {job.job_id}")
        
        output_file = output_dir / f"processed_{input_file.name}"
        
        try:
            # Example: Change metadata
            cmd = [
                'ffmpeg', '-i', str(input_file),
                '-metadata', 'title=Processed Audio',
                '-metadata', 'artist=Telegram Bot',
                '-c:a', 'libmp3lame',
                str(output_file)
            ]
            import subprocess
            subprocess.run(cmd, check=True, capture_output=True)
            
            job.output_files.append(output_file)
            logger.info("Audio processed")
        except Exception as e:
            logger.error(f"Audio processing failed: {e}")
            raise
