from __future__ import annotations

import asyncio
import zipfile

from core.constants import JobStatus
from core.logger import get_logger
from core.password_broker import password_broker
from core.protocol import Protocol

from services.telegram import telegram_service

logger = get_logger(__name__)

PASSWORD_TIMEOUT_SECONDS = 300


class _PasswordRequired(Exception):
    pass


class _WrongPassword(Exception):
    pass


class ArchiveProcessor:

    async def process(self, job):

        logger.info(f"Archive processing started ({job.job_id})")

        if job.input_file is None:
            raise ValueError("Input file not found")

        job.set_status(JobStatus.PROCESSING)

        suffix = job.input_file.suffix.lower()
        password = job.options.password or None

        loop = asyncio.get_event_loop()

        # Try to extract; if a password is required (or the given one is
        # wrong), ask the user through the bridge and retry once.
        for attempt in range(2):

            try:
                await loop.run_in_executor(
                    None,
                    self._extract,
                    job.input_file,
                    job.extracted_dir,
                    suffix,
                    password,
                )
                break

            except (_PasswordRequired, _WrongPassword):

                if attempt == 1:
                    job.set_status(JobStatus.FAILED)
                    raise ValueError("Correct password was not provided")

                password = await self._request_password(job)

        extracted_files = sorted(
            p for p in job.extracted_dir.rglob("*") if p.is_file()
        )

        if not extracted_files:
            job.set_status(JobStatus.FAILED)
            raise ValueError("Archive contained no files")

        await self._process_extracted(job, extracted_files)

        if job.has_output:
            job.set_status(JobStatus.COMPLETED)
        else:
            job.set_status(JobStatus.FAILED)

        logger.info(f"Archive processing finished ({job.job_id})")

        return job.has_output

    # =====================================================
    # Password flow
    # =====================================================

    async def _request_password(self, job) -> str:

        request = Protocol.create_password_request(
            user_id=job.user_id,
            job_id=job.job_id,
            filename=job.original_name,
        )

        waiter = password_broker.create_waiter(job.job_id)

        await telegram_service.send_password_request(request)

        try:
            return await asyncio.wait_for(waiter, timeout=PASSWORD_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            password_broker.cancel(job.job_id)
            raise ValueError("Timed out waiting for the archive password")

    # =====================================================
    # Extraction (blocking, runs in a thread pool executor)
    # =====================================================

    def _extract(self, input_file, destination, suffix, password):

        destination.mkdir(parents=True, exist_ok=True)

        if suffix == ".zip":
            self._extract_zip(input_file, destination, password)

        elif suffix == ".rar":
            self._extract_rar(input_file, destination, password)

        elif suffix == ".7z":
            self._extract_7z(input_file, destination, password)

        else:
            raise ValueError(f"Unsupported archive format: {suffix}")

    def _extract_zip(self, input_file, destination, password):

        with zipfile.ZipFile(input_file) as archive:

            needs_password = any(
                info.flag_bits & 0x1 for info in archive.infolist()
            )

            if needs_password and not password:
                raise _PasswordRequired()

            pwd_bytes = password.encode() if password else None

            try:
                archive.extractall(destination, pwd=pwd_bytes)
            except RuntimeError as e:
                if "password" in str(e).lower():
                    raise _WrongPassword()
                raise

    def _extract_rar(self, input_file, destination, password):

        import rarfile

        with rarfile.RarFile(input_file) as archive:

            if archive.needs_password() and not password:
                raise _PasswordRequired()

            try:
                archive.extractall(destination, pwd=password)
            except rarfile.RarWrongPassword:
                raise _WrongPassword()
            except rarfile.PasswordRequired:
                raise _PasswordRequired()

    def _extract_7z(self, input_file, destination, password):

        import py7zr

        try:
            with py7zr.SevenZipFile(input_file, mode="r", password=password) as archive:

                if archive.needs_password() and not password:
                    raise _PasswordRequired()

                archive.extractall(path=destination)

        except (_PasswordRequired, _WrongPassword):
            raise

        except Exception as e:
            message = str(e).lower()

            if "password" in message or "crc" in message:
                if password:
                    raise _WrongPassword()
                raise _PasswordRequired()

            raise

    # =====================================================
    # Recursive processing of extracted files
    # =====================================================

    async def _process_extracted(self, job, extracted_files):

        # Imported lazily to avoid a circular import
        # (dispatcher imports this module at module load time).
        from dispatcher.dispatcher import Dispatcher
        from utils.filetype import FileTypeDetector

        dispatcher = Dispatcher()

        original_input = job.input_file
        original_type = job.file_type
        original_name = job.original_name

        try:
            for extracted in extracted_files:

                file_type = FileTypeDetector.detect("", extracted.name)

                job.input_file = extracted
                job.original_name = extracted.name

                if file_type == "UNKNOWN":
                    job.add_output(extracted)
                    continue

                job.file_type = file_type

                try:
                    await dispatcher.dispatch(job)
                except Exception:
                    logger.exception(
                        f"Failed to process extracted file "
                        f"{extracted.name} ({job.job_id})"
                    )

        finally:
            job.input_file = original_input
            job.file_type = original_type
            job.original_name = original_name
