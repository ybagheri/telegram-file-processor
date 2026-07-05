import subprocess
from pathlib import Path
from core.logger import get_logger
from core.job import Job
from core.protocol import Protocol

logger = get_logger(__name__)

class ArchiveProcessor:
    async def process(self, job: Job):
        input_file = job.input_file
        extract_dir = job.folders["extracted"]
        
        logger.info(f"Processing archive: {job.job_id}")
        
        try:
            ext = input_file.suffix.lower()
            
            if ext == '.zip':
                if job.password:
                    cmd = ['unzip', '-P', job.password, str(input_file), '-d', str(extract_dir)]
                else:
                    cmd = ['unzip', str(input_file), '-d', str(extract_dir)]
            elif ext == '.rar':
                # Requires unrar
                cmd = ['unrar', 'x', f'-p{job.password}' if job.password else '', str(input_file), str(extract_dir)]
            else:
                logger.warning(f"Unsupported archive: {ext}")
                return

            subprocess.run(cmd, check=True, capture_output=True)
            logger.info("Archive extracted successfully")
            job.status = "completed"
            
        except subprocess.CalledProcessError as e:
            if "password" in str(e.stderr).lower() or "incorrect" in str(e.stderr).lower():
                logger.info("Password required")
                # Send password request
                await self._request_password(job)
            else:
                raise
        except Exception as e:
            logger.error(f"Archive error: {e}")
            raise

    async def _request_password(self, job):
        from services.telegram import telegram_service
        await telegram_service.send_to_bridge(
            Protocol.encode({
                "type": "password_request",
                "job_id": job.job_id,
                "user_id": job.user_id
            })
        )
