from __future__ import annotations

import asyncio
import os
import re
import time
import zipfile
from pathlib import Path

from telethon.errors import FloodWaitError

from core.constants import JobStatus
from core.logger import get_logger
from core.password_broker import password_broker
from core.protocol import Protocol
from core.registry import register_processor

from services.telegram import telegram_service
from utils.text import strip_excluded

logger = get_logger(__name__)

PASSWORD_TIMEOUT_SECONDS = 300

# .zip and .rar support true random-access extraction (one member at a
# time, without touching the rest of the archive), so we stream: extract
# one file -> process it -> delete the raw extracted copy -> next. This
# keeps peak disk usage to roughly "the archive itself + one file", instead
# of "the archive + the entire extracted tree" at once.
#
# .7z almost always uses solid compression by default, where files are
# compressed together in blocks and can't be read out individually without
# decompressing everything before them in the same block anyway — so for
# .7z we still extract everything up front, same as before.
STREAMABLE_SUFFIXES = {".zip", ".rar"}


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


@register_processor("ARCHIVE")
class ArchiveProcessor:

    async def process(self, job):

        logger.info(f"Archive processing started ({job.job_id})")

        if job.input_file is None:
            raise ValueError("Input file not found")

        job.set_status(JobStatus.PROCESSING)

        suffix = job.input_file.suffix.lower()
        password = job.options.password or None

        if suffix in STREAMABLE_SUFFIXES:

            password = await self._prepare_streaming(job, suffix, password)

            entries = await self._list_entries(job.input_file, suffix)

            if not entries:
                job.set_status(JobStatus.FAILED)
                raise ValueError("Archive contained no files")

            await self._stream_and_process(job, suffix, password, entries)

        else:

            password = await self._prepare_bulk(job, suffix, password)

            if not job.extracted_dir.exists() or not any(job.extracted_dir.iterdir()):
                job.set_status(JobStatus.FAILED)
                raise ValueError("Archive contained no files")

            await self._walk_disk_and_process(job)

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

    async def _prepare_bulk(self, job, suffix, password) -> str | None:

        loop = asyncio.get_event_loop()

        for attempt in range(2):

            try:
                await loop.run_in_executor(
                    None,
                    self._extract_bulk,
                    job.input_file,
                    job.extracted_dir,
                    suffix,
                    password,
                )
                return password

            except (_PasswordRequired, _WrongPassword):

                if attempt == 1:
                    job.set_status(JobStatus.FAILED)
                    raise ValueError("Correct password was not provided")

                password = await self._request_password(job)

        return password

    async def _prepare_streaming(self, job, suffix, password, archive_path=None) -> str | None:

        loop = asyncio.get_event_loop()

        path_to_check = archive_path if archive_path is not None else job.input_file

        for attempt in range(2):

            try:
                await loop.run_in_executor(
                    None,
                    self._validate_password_sync,
                    path_to_check,
                    suffix,
                    password,
                )
                return password

            except (_PasswordRequired, _WrongPassword):

                if attempt == 1:
                    job.set_status(JobStatus.FAILED)
                    raise ValueError("Correct password was not provided")

                password = await self._request_password(job)

        return password

    # =====================================================
    # Bulk extraction (7z only — see STREAMABLE_SUFFIXES note above)
    # =====================================================

    def _extract_bulk(self, input_file, destination, suffix, password):

        destination.mkdir(parents=True, exist_ok=True)

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
    # Streaming extraction (zip / rar): list without extracting, validate
    # the password against one entry in memory, then extract one member
    # at a time on demand.
    # =====================================================

    def _validate_password_sync(self, input_file, suffix, password):

        if suffix == ".zip":

            with zipfile.ZipFile(input_file) as archive:

                infos = archive.infolist()
                needs_password = any(info.flag_bits & 0x1 for info in infos)

                if needs_password and not password:
                    raise _PasswordRequired()

                if needs_password:
                    first_file = next((i for i in infos if not i.is_dir()), None)
                    if first_file:
                        try:
                            archive.read(first_file.filename, pwd=password.encode())
                        except RuntimeError as e:
                            if "password" in str(e).lower():
                                raise _WrongPassword()
                            raise

        elif suffix == ".rar":

            import rarfile

            with rarfile.RarFile(input_file) as archive:

                needs_password = archive.needs_password()

                if needs_password and not password:
                    raise _PasswordRequired()

                if needs_password:
                    infos = archive.infolist()
                    first_file = next((i for i in infos if not i.isdir()), None)
                    if first_file:
                        try:
                            archive.read(first_file.filename, pwd=password)
                        except rarfile.RarWrongPassword:
                            raise _WrongPassword()
                        except rarfile.PasswordRequired:
                            raise _PasswordRequired()

    async def _list_entries(self, input_file, suffix) -> list[tuple[str, float]]:

        loop = asyncio.get_event_loop()

        return await loop.run_in_executor(
            None, self._list_entries_sync, input_file, suffix,
        )

    def _list_entries_sync(self, input_file, suffix) -> list[tuple[str, float]]:

        entries = []

        if suffix == ".zip":

            with zipfile.ZipFile(input_file) as archive:
                for info in archive.infolist():
                    if info.is_dir():
                        continue
                    mtime = time.mktime((*info.date_time, 0, 0, -1))
                    entries.append((info.filename.replace("\\", "/"), mtime))

        elif suffix == ".rar":

            import rarfile

            with rarfile.RarFile(input_file) as archive:
                for info in archive.infolist():
                    if info.isdir():
                        continue
                    mtime = time.mktime((*info.date_time, 0, 0, -1))
                    entries.append((info.filename.replace("\\", "/"), mtime))

        return entries

    def _extract_single_sync(self, input_file, destination_root, suffix, password, rel_path) -> str:

        try:
            if suffix == ".zip":
                with zipfile.ZipFile(input_file) as archive:
                    pwd_bytes = password.encode() if password else None
                    return archive.extract(rel_path, path=str(destination_root), pwd=pwd_bytes)

            if suffix == ".rar":
                import rarfile
                with rarfile.RarFile(input_file) as archive:
                    return archive.extract(rel_path, path=str(destination_root), pwd=password)

            raise ValueError(f"Unsupported streaming format: {suffix}")

        except RuntimeError as e:
            if "password" in str(e).lower():
                raise _WrongPassword()
            raise

        except Exception as e:
            try:
                import rarfile
                if isinstance(e, rarfile.RarWrongPassword):
                    raise _WrongPassword()
                if isinstance(e, rarfile.PasswordRequired):
                    raise _PasswordRequired()
            except ImportError:
                pass
            raise

    async def _extract_single(self, input_file, destination_root, suffix, password, rel_path) -> Path:

        loop = asyncio.get_event_loop()

        result = await loop.run_in_executor(
            None,
            self._extract_single_sync,
            input_file,
            destination_root,
            suffix,
            password,
            rel_path,
        )

        return Path(result)

    # =====================================================
    # Shared ordering logic (audio/PDF pairing + sort) — works on any
    # "item" as long as you can get a display name and an mtime from it.
    # =====================================================

    def _order_items(self, job, items, name_of, mtime_of):

        from utils.filetype import FileTypeDetector

        reverse = job.options.sort_order == "desc"

        key = mtime_of if job.options.sort_mode == "date" else (lambda it: _natural_key(name_of(it)))

        audio, pdf, other = [], [], []

        for it in items:
            kind = FileTypeDetector.detect("", name_of(it))
            if kind == "AUDIO":
                audio.append(it)
            elif kind == "PDF":
                pdf.append(it)
            else:
                other.append(it)

        audio.sort(key=key, reverse=reverse)
        pdf.sort(key=key, reverse=reverse)
        other.sort(key=key, reverse=reverse)

        used = set()
        ordered = []

        for a in audio:
            ordered.append(a)

            a_name = name_of(a)
            a_clean = strip_excluded(Path(a_name).stem, job.options.exclude_text).lower()
            a_num = _leading_number(a_name)

            match = None
            for p in pdf:
                if p in used:
                    continue
                p_name = name_of(p)
                p_clean = strip_excluded(Path(p_name).stem, job.options.exclude_text).lower()
                if p_clean == a_clean or (a_num is not None and _leading_number(p_name) == a_num):
                    match = p
                    break

            if match is not None:
                ordered.append(match)
                used.add(match)

        ordered.extend(p for p in pdf if p not in used)
        ordered.extend(other)

        return ordered

    # =====================================================
    # Streaming walk (zip / rar): builds the folder tree purely from the
    # listing (no disk extraction needed just to see the structure), then
    # extracts + processes one file at a time.
    # =====================================================

    async def _stream_and_process(self, job, suffix, password, entries):

        from dispatcher.dispatcher import Dispatcher
        from utils.filetype import FileTypeDetector

        dispatcher = Dispatcher()

        tree = {"__files__": [], "__children__": {}}

        for rel_path, mtime in entries:
            parts = rel_path.split("/")
            node = tree
            for part in parts[:-1]:
                node = node["__children__"].setdefault(
                    part, {"__files__": [], "__children__": {}},
                )
            node["__files__"].append((rel_path, mtime))

        original_input = job.input_file
        original_type = job.file_type
        original_name = job.original_name
        original_rename = job.options.rename_to

        # A rename only makes sense for a single file; for a whole archive
        # it would collapse every extracted file onto the same output name.
        job.options.rename_to = ""

        try:
            await self._stream_node(
                job, tree, "", suffix, password, dispatcher, FileTypeDetector, original_input,
            )
        finally:
            job.input_file = original_input
            job.file_type = original_type
            job.original_name = original_name
            job.options.rename_to = original_rename
            job.current_extract_folder = ""

    async def _stream_node(self, job, node, relative_folder, suffix, password, dispatcher, FileTypeDetector, archive_path):

        if relative_folder:
            await self._announce_folder(job, relative_folder)

        job.current_extract_folder = relative_folder

        ordered_files = self._order_items(
            job,
            node["__files__"],
            name_of=lambda it: Path(it[0]).name,
            mtime_of=lambda it: it[1],
        )

        for rel_path, _mtime in ordered_files:

            try:
                extracted_path = await self._extract_single(
                    archive_path, job.extracted_dir, suffix, password, rel_path,
                )
            except Exception:
                logger.exception(
                    f"Failed to extract {rel_path} ({job.job_id})"
                )
                continue

            file_type = FileTypeDetector.detect("", extracted_path.name)

            job.input_file = extracted_path
            job.original_name = strip_excluded(extracted_path.name, job.options.exclude_text)

            if file_type == "UNKNOWN":
                # This raw extracted file IS the output — keep it on disk
                # until it's uploaded (worker.py deletes it after upload).
                job.add_output(extracted_path, kind="document")
                continue

            job.file_type = file_type

            try:
                await dispatcher.dispatch(job)
            except Exception:
                logger.exception(
                    f"Failed to process extracted file {rel_path} ({job.job_id})"
                )

            # The processor (video/audio/pdf/archive) writes its own output
            # file elsewhere under output_dir — the raw extracted copy here
            # is no longer needed, so free it immediately rather than
            # waiting for the whole job to finish.
            if extracted_path.exists():
                try:
                    extracted_path.unlink()
                except OSError:
                    pass

        reverse = job.options.sort_order == "desc"
        child_names = sorted(node["__children__"].keys(), key=_natural_key, reverse=reverse)

        for child_name in child_names:
            child_relative = f"{relative_folder}/{child_name}" if relative_folder else child_name
            await self._stream_node(
                job, node["__children__"][child_name], child_relative,
                suffix, password, dispatcher, FileTypeDetector, archive_path,
            )

    # =====================================================
    # Disk-based walk (7z only): same ordering/pairing rules, but the
    # whole archive is already extracted to job.extracted_dir up front.
    # =====================================================

    def _order_folder_files(self, job, files: list[Path]) -> list[Path]:

        return self._order_items(
            job,
            files,
            name_of=lambda p: p.name,
            mtime_of=lambda p: p.stat().st_mtime,
        )

    async def _walk_disk_and_process(self, job):

        from dispatcher.dispatcher import Dispatcher
        from utils.filetype import FileTypeDetector

        dispatcher = Dispatcher()

        reverse = job.options.sort_order == "desc"

        original_input = job.input_file
        original_type = job.file_type
        original_name = job.original_name
        original_rename = job.options.rename_to

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

    # =====================================================
    # Multi-volume RAR (user-declared: they send each part as a separate
    # message because the whole archive is too big for local disk at
    # once). We keep volume 1 (needed to open/list the archive) plus a
    # sliding window of the most recent volumes, sized from the *real*
    # per-file compressed sizes in the listing — never guessed — so we
    # never risk deleting a volume a pending file still needs.
    # =====================================================

    def _sort_parts(self, messages):

        def key(message):
            name = (message.file.name if message.file else "") or ""

            match = re.search(r"\.part(\d+)", name, re.IGNORECASE)
            if match:
                return int(match.group(1))

            match = re.search(r"\.r(\d{2,})$", name, re.IGNORECASE)
            if match:
                return int(match.group(1)) + 1

            return 0  # unrecognized naming: keep as sent (stable sort)

        return sorted(messages, key=key)

    async def _download_volume(self, job, message, index: int) -> Path | None:

        filename = (message.file.name if message.file else None) or f"part{index}.rar"

        destination = job.input_dir / filename

        return await telegram_service.download(message, destination)

    def _list_rar_entries_with_size_sync(self, input_file) -> list[tuple[str, float, int]]:

        import rarfile

        entries = []

        with rarfile.RarFile(input_file) as archive:
            for info in archive.infolist():
                if info.isdir():
                    continue
                mtime = time.mktime((*info.date_time, 0, 0, -1))
                entries.append((
                    info.filename.replace("\\", "/"),
                    mtime,
                    info.compress_size,
                ))

        return entries

    def _compute_keep_window(self, volume_size: int, entries) -> int:

        if volume_size <= 0:
            return 3  # can't compute safely; fall back to a cautious default

        import math

        max_span = 1

        for _rel_path, _mtime, compress_size in entries:
            span = math.ceil((compress_size or 0) / volume_size) + 1
            max_span = max(max_span, span)

        return max(2, max_span)

    async def _fetch_next_volume(self, job, state):

        idx = state["next_to_fetch"]

        if idx > state["total_parts"]:
            raise ValueError("No more parts left to fetch")

        message = state["parts"][idx - 1]

        path = await self._download_volume(job, message, idx)

        if path is None:
            raise ValueError(f"Failed to download part {idx}")

        state["volumes"][idx] = path
        state["next_to_fetch"] = idx + 1

        # Keep volume 1 forever (it's the entry point for opening/listing
        # the set) plus a sliding window of the most recent other volumes.
        non_first = sorted(k for k in state["volumes"].keys() if k != 1)

        while len(non_first) > state["keep_window"]:
            oldest = non_first.pop(0)
            old_path = state["volumes"].pop(oldest)
            if old_path.exists():
                try:
                    old_path.unlink()
                except OSError:
                    pass

    async def _extract_with_more_volumes(self, job, state, rel_path) -> Path | None:

        while True:

            try:
                return await self._extract_single(
                    state["volumes"][1], job.extracted_dir, ".rar", state["password"], rel_path,
                )

            except Exception:

                if state["next_to_fetch"] > state["total_parts"]:
                    logger.exception(
                        f"Extraction failed for {rel_path} with every part "
                        f"already downloaded ({job.job_id})"
                    )
                    return None

                try:
                    await self._fetch_next_volume(job, state)
                except Exception:
                    logger.exception(
                        f"Failed to fetch the next volume for {rel_path} ({job.job_id})"
                    )
                    return None

    async def process_multivolume(self, job, messages: list) -> bool:

        logger.info(f"Multi-volume archive processing started ({job.job_id})")

        job.set_status(JobStatus.PROCESSING)

        parts = self._sort_parts(messages)
        total_parts = len(parts)

        first_path = await self._download_volume(job, parts[0], 1)

        if first_path is None:
            job.set_status(JobStatus.FAILED)
            raise ValueError("Failed to download the first part")

        volume_size = first_path.stat().st_size

        password = job.options.password or None
        password = await self._prepare_streaming(job, ".rar", password, archive_path=first_path)

        loop = asyncio.get_event_loop()

        entries = await loop.run_in_executor(
            None, self._list_rar_entries_with_size_sync, first_path,
        )

        if not entries:
            job.set_status(JobStatus.FAILED)
            raise ValueError("Archive contained no files")

        keep_window = self._compute_keep_window(volume_size, entries)

        logger.info(
            f"Multi-volume archive: {total_parts} parts declared, "
            f"keeping volume 1 + a window of {keep_window} ({job.job_id})"
        )

        tree = {"__files__": [], "__children__": {}}

        for rel_path, mtime, _size in entries:
            path_parts = rel_path.split("/")
            node = tree
            for part in path_parts[:-1]:
                node = node["__children__"].setdefault(
                    part, {"__files__": [], "__children__": {}},
                )
            node["__files__"].append((rel_path, mtime))

        from dispatcher.dispatcher import Dispatcher
        from utils.filetype import FileTypeDetector

        dispatcher = Dispatcher()

        original_rename = job.options.rename_to
        job.options.rename_to = ""

        state = {
            "volumes": {1: first_path},
            "next_to_fetch": 2,
            "keep_window": keep_window,
            "total_parts": total_parts,
            "parts": parts,
            "password": password,
        }

        try:
            await self._stream_node_multivolume(
                job, tree, "", dispatcher, FileTypeDetector, state,
            )
        finally:
            job.options.rename_to = original_rename
            job.current_extract_folder = ""

            for path in state["volumes"].values():
                if path.exists():
                    try:
                        path.unlink()
                    except OSError:
                        pass

        if job.has_output:
            job.set_status(JobStatus.COMPLETED)
        else:
            job.set_status(JobStatus.FAILED)

        logger.info(f"Multi-volume archive processing finished ({job.job_id})")

        return job.has_output

    async def _stream_node_multivolume(self, job, node, relative_folder, dispatcher, FileTypeDetector, state):

        if relative_folder:
            await self._announce_folder(job, relative_folder)

        job.current_extract_folder = relative_folder

        ordered_files = self._order_items(
            job,
            node["__files__"],
            name_of=lambda it: Path(it[0]).name,
            mtime_of=lambda it: it[1],
        )

        for rel_path, _mtime in ordered_files:

            extracted_path = await self._extract_with_more_volumes(job, state, rel_path)

            if extracted_path is None:
                continue

            file_type = FileTypeDetector.detect("", extracted_path.name)

            job.input_file = extracted_path
            job.original_name = strip_excluded(extracted_path.name, job.options.exclude_text)

            if file_type == "UNKNOWN":
                job.add_output(extracted_path, kind="document")
                continue

            job.file_type = file_type

            try:
                await dispatcher.dispatch(job)
            except Exception:
                logger.exception(
                    f"Failed to process extracted file {rel_path} ({job.job_id})"
                )

            if extracted_path.exists():
                try:
                    extracted_path.unlink()
                except OSError:
                    pass

        reverse = job.options.sort_order == "desc"
        child_names = sorted(node["__children__"].keys(), key=_natural_key, reverse=reverse)

        for child_name in child_names:
            child_relative = f"{relative_folder}/{child_name}" if relative_folder else child_name
            await self._stream_node_multivolume(
                job, node["__children__"][child_name], child_relative,
                dispatcher, FileTypeDetector, state,
            )

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
