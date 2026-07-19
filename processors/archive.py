from __future__ import annotations

import asyncio
import math
import os
import re
import tempfile
import time
import zipfile
from datetime import datetime
from pathlib import Path

from telethon.errors import FloodWaitError

from core.constants import JobStatus
from core.delivery import upload_entry
from core.logger import get_logger
from core.password_broker import password_broker
from core.protocol import Protocol
from core.registry import get_registered_processors, register_processor

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

# .rar is handled by shelling out to the real `unrar` CLI (not the rarfile
# Python package): unrar is what actually understands multi-volume sets,
# handles partial-volume-availability correctly, and is what's already
# installed/trusted on the deployment server.
_UNRAR_LISTING_LINE_RE = re.compile(
    r"^([* ])\s*(\S+)\s+(\d+)\s+(\d+)\s+(\d+)%\s+(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})\s+([0-9A-Fa-f]{8})\s+(.+)$"
)


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


def _parse_unrar_listing(output: str) -> list[tuple[str, float, int]]:
    """Parses `unrar v` output (see the real sample this regex was built
    and tested against). Returns (relative_path, mtime, size) for files,
    skipping directory entries."""

    entries = []

    for line in output.splitlines():

        match = _UNRAR_LISTING_LINE_RE.match(line)

        if not match:
            continue

        _marker, attrs, size, _packed, _ratio, date, time_str, _checksum, name = match.groups()

        if "d" in attrs.lower():
            continue  # directory entry, not a file

        try:
            mtime = datetime.strptime(f"{date} {time_str}", "%Y-%m-%d %H:%M").timestamp()
        except ValueError:
            mtime = 0.0

        entries.append((name.strip().replace("\\", "/"), mtime, int(size)))

    return entries


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

            entries = await self._list_entries(job.input_file, suffix, password)

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

        path_to_check = archive_path if archive_path is not None else job.input_file

        for attempt in range(2):

            try:
                if suffix == ".rar":
                    await self._validate_rar_password(path_to_check, password)
                else:
                    loop = asyncio.get_event_loop()
                    await loop.run_in_executor(
                        None, self._validate_zip_password_sync, path_to_check, password,
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
    # ZIP: stdlib zipfile (unchanged)
    # =====================================================

    def _validate_zip_password_sync(self, input_file, password):

        with zipfile.ZipFile(input_file) as archive:

            infos = [i for i in archive.infolist() if not i.is_dir()]
            needs_password = any(info.flag_bits & 0x1 for info in infos)

            if needs_password and not password:
                raise _PasswordRequired()

            if needs_password:
                # Probe the smallest entry — cheaper, and consistent with
                # the RAR path's reasoning (see _validate_rar_password).
                smallest = min(infos, key=lambda i: i.file_size, default=None)
                if smallest:
                    try:
                        archive.read(smallest.filename, pwd=password.encode())
                    except RuntimeError as e:
                        if "password" in str(e).lower():
                            raise _WrongPassword()
                        raise

    def _list_zip_entries_sync(self, input_file) -> list[tuple[str, float, int]]:

        entries = []

        with zipfile.ZipFile(input_file) as archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                mtime = time.mktime((*info.date_time, 0, 0, -1))
                entries.append((info.filename.replace("\\", "/"), mtime, info.file_size))

        return entries

    def _extract_zip_single_sync(self, input_file, destination_root, password, rel_path) -> str:

        try:
            with zipfile.ZipFile(input_file) as archive:
                pwd_bytes = password.encode() if password else None
                return archive.extract(rel_path, path=str(destination_root), pwd=pwd_bytes)
        except RuntimeError as e:
            if "password" in str(e).lower():
                raise _WrongPassword()
            raise

    # =====================================================
    # RAR: shells out to the real `unrar` CLI (see module docstring note)
    # =====================================================

    async def _run_unrar(self, args: list[str]) -> tuple[int, str, str]:

        process = await asyncio.create_subprocess_exec(
            "unrar", *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout_bytes, stderr_bytes = await process.communicate()

        return (
            process.returncode,
            stdout_bytes.decode("utf-8", errors="replace"),
            stderr_bytes.decode("utf-8", errors="replace"),
        )

    async def _list_rar_via_unrar(self, archive_path: Path, password: str | None) -> list[tuple[str, float, int]]:

        args = ["v", "-y", f"-p{password}" if password else "-p-", str(archive_path)]

        returncode, stdout, stderr = await self._run_unrar(args)

        entries = _parse_unrar_listing(stdout)

        if not entries and returncode != 0:
            combined = (stdout + stderr).lower()
            if "password" in combined:
                raise _PasswordRequired()
            raise ValueError(f"unrar listing failed (code {returncode}): {stderr.strip()[:300]}")

        return entries

    async def _validate_rar_password(self, archive_path: Path, password: str | None):

        entries = await self._list_rar_via_unrar(archive_path, password)

        if not entries:
            raise ValueError("Archive contained no files")

        # Try extracting a handful of the SMALLEST entries to a throwaway
        # temp dir. For a multi-volume archive validated against only the
        # first volume, the first-*listed* file is often a large one (e.g.
        # a scanned textbook PDF) that spans several volumes — trying that
        # one would fail with a "missing volume" error that has nothing to
        # do with the password. Smaller entries are far more likely to be
        # fully containable in what we've downloaded so far. If every
        # candidate is inconclusive, defer to the real per-file extraction
        # pass later (which fetches more volumes on demand and is the
        # actual final judge of whether the password was right).
        candidates = sorted(entries, key=lambda e: e[2])[:5]

        with tempfile.TemporaryDirectory() as tmp_dir:

            for rel_path, _mtime, _size in candidates:

                args = [
                    "x", "-y", "-o+",
                    f"-p{password}" if password else "-p-",
                    str(archive_path), rel_path, tmp_dir + os.sep,
                ]

                returncode, stdout, stderr = await self._run_unrar(args)

                if returncode == 0:
                    return  # a clean extraction confirms the password is correct

                combined = (stdout + stderr).lower()

                if "password" in combined or "encrypt" in combined:
                    if not password:
                        raise _PasswordRequired()
                    raise _WrongPassword()

                # Otherwise inconclusive (most likely "needs a volume we
                # don't have yet") — try the next smallest candidate.

    async def _extract_rar_single(self, archive_path: Path, destination_root: Path, password: str | None, rel_path: str) -> Path:

        destination_root.mkdir(parents=True, exist_ok=True)

        args = [
            "x", "-y", "-o+",
            f"-p{password}" if password else "-p-",
            str(archive_path), rel_path, str(destination_root) + os.sep,
        ]

        returncode, stdout, stderr = await self._run_unrar(args)

        extracted_path = destination_root / rel_path

        if returncode != 0 or not extracted_path.exists():

            combined = (stdout + stderr).lower()

            if "password" in combined or "encrypt" in combined:
                if not password:
                    raise _PasswordRequired()
                raise _WrongPassword()

            raise RuntimeError(
                f"unrar extraction failed for {rel_path} (code {returncode}): {stderr.strip()[:300]}"
            )

        return extracted_path

    # =====================================================
    # Streaming extraction (zip / rar): list without extracting, validate
    # the password against a couple of entries, then extract one member
    # at a time on demand.
    # =====================================================

    async def _list_entries(self, input_file, suffix, password=None) -> list[tuple[str, float, int]]:

        if suffix == ".rar":
            return await self._list_rar_via_unrar(input_file, password)

        loop = asyncio.get_event_loop()

        return await loop.run_in_executor(
            None, self._list_zip_entries_sync, input_file,
        )

    async def _extract_single(self, input_file, destination_root, suffix, password, rel_path) -> Path:

        if suffix == ".rar":
            return await self._extract_rar_single(input_file, destination_root, password, rel_path)

        loop = asyncio.get_event_loop()

        result = await loop.run_in_executor(
            None,
            self._extract_zip_single_sync,
            input_file,
            destination_root,
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

        for rel_path, mtime, _size in entries:
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

        registered_types = get_registered_processors()

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

            if file_type not in registered_types:
                # No processor handles this type (e.g. a plain image, or
                # anything else FileTypeDetector recognizes but we don't
                # have a dedicated processor for) — this raw extracted
                # file IS the output, deliver it as-is rather than
                # silently dropping it.
                entry = job.add_output(extracted_path, kind="document")
                if entry:
                    await upload_entry(job, entry)
                continue

            job.file_type = file_type

            before_count = len(job.output_files)

            try:
                await dispatcher.dispatch(job)
            except Exception:
                logger.exception(
                    f"Failed to process extracted file {rel_path} ({job.job_id})"
                )

            # Deliver this file's output(s) right away — folder by folder,
            # not "announce every folder, then upload everything at the
            # very end" — so uploads keep pace with the folder they came
            # from as the user actually sees them.
            for new_entry in job.output_files[before_count:]:
                await upload_entry(job, new_entry)

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
        registered_types = get_registered_processors()

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

                    if file_type not in registered_types:
                        entry = job.add_output(extracted, kind="document")
                        if entry:
                            await upload_entry(job, entry)
                        continue

                    job.file_type = file_type

                    before_count = len(job.output_files)

                    try:
                        await dispatcher.dispatch(job)
                    except Exception:
                        logger.exception(
                            f"Failed to process extracted file "
                            f"{extracted.name} ({job.job_id})"
                        )

                    for new_entry in job.output_files[before_count:]:
                        await upload_entry(job, new_entry)

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
    # per-file sizes in the listing — never guessed — so we never risk
    # deleting a volume a pending file still needs.
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

    def _compute_keep_window(self, volume_size: int, entries) -> int:

        if volume_size <= 0:
            return 3  # can't compute safely; fall back to a cautious default

        max_span = 1

        for _rel_path, _mtime, size in entries:
            span = math.ceil((size or 0) / volume_size) + 1
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
                return await self._extract_rar_single(
                    state["volumes"][1], job.extracted_dir, state["password"], rel_path,
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

        entries = await self._list_rar_via_unrar(first_path, password)

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

        registered_types = get_registered_processors()

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

            if file_type not in registered_types:
                entry = job.add_output(extracted_path, kind="document")
                if entry:
                    await upload_entry(job, entry)
                continue

            job.file_type = file_type

            before_count = len(job.output_files)

            try:
                await dispatcher.dispatch(job)
            except Exception:
                logger.exception(
                    f"Failed to process extracted file {rel_path} ({job.job_id})"
                )

            for new_entry in job.output_files[before_count:]:
                await upload_entry(job, new_entry)

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

        clean_relative = "/".join(
            strip_excluded(part, job.options.exclude_text)
            for part in relative_str.split("/")
        )

        payload = Protocol.create_folder(
            user_id=job.user_id,
            job_id=job.job_id,
            folder=clean_relative,
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
