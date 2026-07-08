from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

import py7zr
import rarfile

from core.logger import get_logger

logger = get_logger(__name__)


class ArchiveService:

    # =====================================================
    # Public
    # =====================================================

    async def extract(
        self,
        archive_file: Path,
        output_dir: Path,
        password: str | None = None,
    ) -> bool:

        suffix = archive_file.suffix.lower()

        try:

            if suffix == ".zip":

                return self._extract_zip(
                    archive_file,
                    output_dir,
                    password,
                )

            if suffix == ".rar":

                return self._extract_rar(
                    archive_file,
                    output_dir,
                    password,
                )

            if suffix == ".7z":

                return self._extract_7z(
                    archive_file,
                    output_dir,
                    password,
                )

            logger.error(
                "Unsupported archive: %s",
                suffix,
            )

            return False

        except Exception as e:

            logger.exception(e)

            return False

    # =====================================================
    # ZIP
    # =====================================================

    def _extract_zip(
        self,
        archive: Path,
        output: Path,
        password: str | None,
    ) -> bool:

        with zipfile.ZipFile(archive) as z:

            if password:

                z.extractall(
                    output,
                    pwd=password.encode(),
                )

            else:

                z.extractall(
                    output,
                )

        return True

    # =====================================================
    # RAR
    # =====================================================

    def _extract_rar(
        self,
        archive: Path,
        output: Path,
        password: str | None,
    ) -> bool:

        with rarfile.RarFile(archive) as r:

            if password:

                r.extractall(
                    output,
                    pwd=password,
                )

            else:

                r.extractall(
                    output,
                )

        return True

    # =====================================================
    # 7Z
    # =====================================================

    def _extract_7z(
        self,
        archive: Path,
        output: Path,
        password: str | None,
    ) -> bool:

        with py7zr.SevenZipFile(

            archive,

            mode="r",

            password=password,

        ) as z:

            z.extractall(
                path=output,
            )

        return True
        
        
        # =====================================================
    # List Extracted Files
    # =====================================================

    def list_files(
        self,
        directory: Path,
    ) -> list[Path]:

        files: list[Path] = []

        for file in directory.rglob("*"):

            if file.is_file():

                files.append(file)

        files.sort()

        return files

    # =====================================================
    # Find First Archive
    # =====================================================

    def find_first_archive(
        self,
        directory: Path,
    ) -> Path | None:

        for file in sorted(directory.iterdir()):

            if not file.is_file():

                continue

            if self.is_archive(file):

                return file

        return None

    # =====================================================
    # Archive Check
    # =====================================================

    def is_archive(
        self,
        file: Path,
    ) -> bool:

        return file.suffix.lower() in {

            ".zip",

            ".rar",

            ".7z",

        }

    # =====================================================
    # Multipart Archive
    # =====================================================

    def is_multipart(
        self,
        file: Path,
    ) -> bool:

        name = file.name.lower()

        if ".part1.rar" in name:

            return True

        if ".part01.rar" in name:

            return True

        return False

    def find_main_archive(
        self,
        directory: Path,
    ) -> Path | None:

        for file in sorted(directory.iterdir()):

            if not file.is_file():

                continue

            if self.is_multipart(file):

                return file

        return self.find_first_archive(
            directory,
        )

    # =====================================================
    # Cleanup
    # =====================================================

    def cleanup(
        self,
        directory: Path,
    ):

        if not directory.exists():

            return

        shutil.rmtree(
            directory,
            ignore_errors=True,
        )


archive_service = ArchiveService()


    # =====================================================
    # Recursive Extraction
    # =====================================================

    async def extract_recursive(
        self,
        archive_file: Path,
        output_dir: Path,
        password: str | None = None,
        max_depth: int = 3,
    ) -> bool:

        ok = await self.extract(
            archive_file,
            output_dir,
            password,
        )

        if not ok:
            return False

        current_depth = 0

        while current_depth < max_depth:

            nested = self.find_first_archive(
                output_dir,
            )

            if nested is None:
                break

            logger.info(
                "Nested archive found: %s",
                nested.name,
            )

            nested_output = nested.parent / nested.stem

            nested_output.mkdir(
                parents=True,
                exist_ok=True,
            )

            ok = await self.extract(
                nested,
                nested_output,
                password,
            )

            if not ok:
                logger.warning(
                    "Cannot extract nested archive: %s",
                    nested.name,
                )
                break

            try:
                nested.unlink()
            except Exception:
                pass

            current_depth += 1

        return True

    # =====================================================
    # Utilities
    # =====================================================

    def count_files(
        self,
        directory: Path,
    ) -> int:

        return len(
            self.list_files(
                directory,
            )
        )

    def total_size(
        self,
        directory: Path,
    ) -> int:

        total = 0

        for file in directory.rglob("*"):

            if file.is_file():

                total += file.stat().st_size

        return total

    def remove_empty_dirs(
        self,
        directory: Path,
    ):

        for path in sorted(
            directory.rglob("*"),
            reverse=True,
        ):

            if path.is_dir():

                try:

                    path.rmdir()

                except OSError:

                    pass

    def flatten_directory(
        self,
        directory: Path,
    ):

        files = self.list_files(
            directory,
        )

        for file in files:

            if file.parent == directory:

                continue

            destination = directory / file.name

            counter = 1

            while destination.exists():

                destination = directory / (
                    f"{file.stem}_{counter}{file.suffix}"
                )

                counter += 1

            shutil.move(
                str(file),
                str(destination),
            )

        self.remove_empty_dirs(
            directory,
        )


archive_service = ArchiveService()