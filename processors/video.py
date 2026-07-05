import subprocess
from pathlib import Path
from core.logger import get_logger
from core.job import Job

logger = get_logger(__name__)

class VideoProcessor:
    async def process(self, job: Job):
        input_file = job.input_file
        output_dir = job.folders["output"]
        
        logger.info(f"Starting video processing for {job.job_id}")

        # Example: Convert to 720p + add logo (placeholder)
        output_file = output_dir / f"processed_{input_file.name}"
        
        try:
            # Basic FFmpeg command example
            cmd = [
                'ffmpeg', '-i', str(input_file),
                '-vf', 'scale=1280:720',
                '-c:v', 'libx264', '-preset', 'fast',
                str(output_file)
            ]
            subprocess.run(cmd, check=True, capture_output=True)
            
            job.output_files.append(output_file)
            logger.info(f"Video processed successfully: {output_file}")
        except Exception as e:
            logger.error(f"Video processing failed: {e}")
            raise
