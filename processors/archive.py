from __future__ import annotations

import asyncio
import os
import re
import zipfile
from pathlib import Path

from telethon.errors import FloodWaitError

from core.constants import JobStatus
from core.logger import get_logger
from core.password_broker import password_broker
from core.protocol import Protocol

from services.telegram import telegram_service
from utils.text import strip_excluded

logger = get_logger(__name__)

PASSWORD_TIMEOUT_SECONDS = 300


class _PasswordRequired(Exception):
    pass


class _WrongPassword(Exception):
    pass


def _natural_key(text: str):
    """Sort key that treats embedded numbers numerically, so '2' sorts
    before '10' instead of after it (like a human would expect)."""

    stem = Path(text).stem or text

    return [
        (0, int(part)) if part.isdigit() else (1, part.lower())
        for part in re.split(r"(\d+)", stem) if part
    ]


def _leading_number(name: str):
    """First run of digits in a filename, used to pair '001.mp3' with
    '001.pdf' even when their names otherwise differ."""

    match = re.match(r"^\D*(\d+)", Path(name).stem)

    return int(match.group(1)) if match else None


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

        if not job.extracted_dir.exists() or not any(job.extracted_dir.iterdir()):
            job.set_status(JobStatus.FAILED)
            raise ValueError("Archive contained no files")

        await self._walk_and_process(job)

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
    # Folder-by-folder, ordered processing of extracted files
    # =====================================================

    def _order_folder_files(self, job, files: list[Path]) -> list[Path]:
        """Groups files the way a human browsing the course would expect:
        each audio file immediately followed by its matching PDF (same
        leading number or same cleaned name), then any leftover PDFs,
        then everything else — each group internally sorted per the
        user's chosen sort mode/order."""

        from utils.filetype import FileTypeDetector

        reverse = job.options.sort_order == "desc"

        if job.options.sort_mode == "date":
            key = lambda p: p.stat().st_mtime
        else:
            key = lambda p: _natural_key(p.name)

        audio, pdf, other = [], [], []

        for f in files:
            kind = FileTypeDetector.detect("", f.name)
            if kind == "AUDIO":
                audio.append(f)
            elif kind == "PDF":
                pdf.append(f)
            else:
                other.append(f)

        audio.sort(key=key, reverse=reverse)
        pdf.sort(key=key, reverse=reverse)
        other.sort(key=key, reverse=reverse)

        used_pdfs = set()
        ordered = []

        for a in audio:
            ordered.append(a)

            a_clean = strip_excluded(a.stem, job.options.exclude_text).lower()
            a_num = _leading_number(a.name)

            match = None
            for p in pdf:
                if p in used_pdfs:
                    continue
                p_clean = strip_excluded(p.stem, job.options.exclude_text).lower()
                if p_clean == a_clean or (a_num is not None and _leading_number(p.name) == a_num):
                    match = p
                    break

            if match:
                ordered.append(match)
                used_pdfs.add(match)

        ordered.extend(p for p in pdf if p not in used_pdfs)
        ordered.extend(other)

        return ordered

    async def _walk_and_process(self, job):

        # Imported lazily to avoid a circular import
        # (dispatcher imports this module at module load time).
        from dispatcher.dispatcher import Dispatcher

        dispatcher = Dispatcher()

        from utils.filetype import FileTypeDetector

        reverse = job.options.sort_order == "desc"

        original_input = job.input_file
        original_type = job.file_type
        original_name = job.original_name
        original_rename = job.options.rename_to

        # A rename only makes sense for a single file; for a whole archive
        # it would collapse every extracted file onto the same output name.
        job.options.rename_to = ""

        try:
            for dirpath, dirnames, filenames in os.walk(job.extracted_dir):

                dirnames.sort(key=_natural_key, reverse=reverse)

                root = Path(dirpath)
                relative = root.relative_to(job.extracted_dir)
                relative_str = "" if str(relative) == "." else str(relative).replace(os.sep, "/")

                if relative_str:
                    await self._announce_folder(job, relative_str)

                job.current_extract_folder = relative_str

                files = self._order_folder_files(
                    job,
                    [root / name for name in filenames],
                )

                for extracted in files:

                    job.add_extracted(extracted)

                    file_type = FileTypeDetector.detect("", extracted.name)

                    job.input_file = extracted
                    job.original_name = strip_excluded(extracted.name, job.options.exclude_text)

                    if file_type == "UNKNOWN":
                        job.add_output(extracted, kind="document")
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
            job.options.rename_to = original_rename
            job.current_extract_folder = ""

    async def _announce_folder(self, job, relative_str: str):

        payload = Protocol.create_folder(
            user_id=job.user_id,
            job_id=job.job_id,
            folder=relative_str,
            target_chat_id=job.options.target_chat_id,
        )

        for attempt in range(2):

            try:
                await telegram_service.send_info(payload)
                break

            except FloodWaitError as e:
                if attempt == 1:
                    logger.warning(
                        f"Giving up announcing folder {relative_str} "
                        f"after flood wait ({job.job_id})"
                    )
                    break
                logger.warning(
                    f"Flood wait ({e.seconds}s) announcing folder "
                    f"{relative_str}, retrying once"
                )
                await asyncio.sleep(e.seconds + 1)

            except Exception:
                logger.exception(
                    f"Failed to announce folder {relative_str} ({job.job_id})"
                )
                break

        # Small pacing delay, same idea as the upload loop: archives with
        # many nested folders would otherwise fire announcements back to
        # back and trip Telegram's flood limits.
        await asyncio.sleep(0.4)
