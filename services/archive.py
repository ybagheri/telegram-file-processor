from __future__ import annotations

import asyncio
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

        if not archive_file.exists():
            logger.error("Archive file does not exist: %s", archive_file)
            return False

        suffix = archive_file.suffix.lower()

        try:
            if suffix == ".zip":
                return await asyncio.to_thread(
                    self._extract_zip,
                    archive_file,
                    output_dir,
                    password,
                )

            if suffix == ".rar":
                return await asyncio.to_thread(
                    self._extract_rar,
                    archive_file,
                    output_dir,
                    password,
                )

            if suffix == ".7z":
                return await asyncio.to_thread(
                    self._extract_7z,
                    archive_file,
                    output_dir,
                    password,
                )

            logger.error("Unsupported archive: %s", suffix)
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

        output.mkdir(parents=True, exist_ok=True)

        with zipfile.ZipFile(archive) as z:
            # اعتبارسنجی امنیتی برای جلوگیری از Path Traversal
            for member in z.namelist():
                member_path = Path(member)
                if member_path.is_absolute() or ".." in member_path.parts:
                    logger.warning("Suspicious file path detected: %s", member)
                    continue

            try:
                if password:
                    z.extractall(output, pwd=password.encode())
                else:
                    z.extractall(output)
                return True
            except RuntimeError as e:
                if "Bad password" in str(e) or "password" in str(e).lower():
                    logger.error("Incorrect password for ZIP archive")
                else:
                    logger.exception(e)
                return False

    # =====================================================
    # RAR
    # =====================================================

    def _extract_rar(
        self,
        archive: Path,
        output: Path,
        password: str | None,
    ) -> bool:

        output.mkdir(parents=True, exist_ok=True)

        try:
            with rarfile.RarFile(archive) as r:
                # اعتبارسنجی امنیتی
                for member in r.infolist():
                    if ".." in member.filename or member.filename.startswith("/"):
                        logger.warning("Suspicious file path detected: %s", member.filename)
                        continue

                if password:
                    r.extractall(output, pwd=password)
                else:
                    r.extractall(output)
                return True

        except rarfile.RarCannotExec:
            logger.error("RAR executable not found. Please install unrar.")
            return False
        except rarfile.RarWrongPassword:
            logger.error("Incorrect password for RAR archive")
            return False
        except Exception as e:
            logger.exception(e)
            return False

    # =====================================================
    # 7Z
    # =====================================================

    def _extract_7z(
        self,
        archive: Path,
        output: Path,
        password: str | None,
    ) -> bool:

        output.mkdir(parents=True, exist_ok=True)

        try:
            with py7zr.SevenZipFile(
                archive,
                mode="r",
                password=password,
            ) as z:
                # اعتبارسنجی امنیتی
                for member in z.getnames():
                    member_path = Path(member)
                    if member_path.is_absolute() or ".." in member_path.parts:
                        logger.warning("Suspicious file path detected: %s", member)
                        continue

                z.extractall(path=output)
                return True

        except py7zr.exceptions.Bad7zFile:
            logger.error("Invalid or corrupted 7z file")
            return False
        except py7zr.exceptions.PasswordRequired:
            logger.error("Password required for 7z archive")
            return False
        except py7zr.exceptions.WrongPassword:
            logger.error("Incorrect password for 7z archive")
            return False
        except Exception as e:
            logger.exception(e)
            return False

    # =====================================================
    # List Extracted Files
    # =====================================================

    def list_files(
        self,
        directory: Path,
    ) -> list[Path]:

        if not directory.exists():
            return []

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

        if not directory.exists():
            return None

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
        # پشتیبانی از الگوهای مختلف چندبخشی
        patterns = [
            ".part1.rar",
            ".part01.rar",
            ".r00",
            ".r01",
            ".001",
            ".002",
            ".z01",
            ".z02",
        ]
        return any(p in name for p in patterns)

    def find_main_archive(
        self,
        directory: Path,
    ) -> Path | None:

        if not directory.exists():
            return None

        for file in sorted(directory.iterdir()):
            if not file.is_file():
                continue
            if self.is_multipart(file):
                return file

        return self.find_first_archive(directory)

    # =====================================================
    # Cleanup
    # =====================================================

    def cleanup(
        self,
        directory: Path,
    ):

        if not directory.exists():
            return

        try:
            shutil.rmtree(directory, ignore_errors=False)
            logger.info("Cleaned up directory: %s", directory)
        except Exception as e:
            logger.warning("Failed to cleanup directory %s: %s", directory, e)
            # تلاش مجدد با ignore_errors
            shutil.rmtree(directory, ignore_errors=True)

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

        # ایجاد دایرکتوری خروجی
        output_dir.mkdir(parents=True, exist_ok=True)

        # استخراج فایل اصلی
        ok = await self.extract(archive_file, output_dir, password)
        if not ok:
            logger.error("Failed to extract main archive: %s", archive_file.name)
            return False

        current_depth = 0
        extracted_paths = set()  # برای جلوگیری از حلقه بی‌نهایت

        while current_depth < max_depth:
            # پیدا کردن آرچیو تو در تو
            nested = self.find_first_archive(output_dir)
            if nested is None:
                break

            # جلوگیری از حلقه بی‌نهایت با بررسی مسیرهای تکراری
            nested_key = str(nested.resolve())
            if nested_key in extracted_paths:
                logger.warning("Duplicate nested archive detected: %s", nested.name)
                break
            extracted_paths.add(nested_key)

            logger.info("Nested archive found: %s (depth: %d)", nested.name, current_depth + 1)

            # ایجاد دایرکتوری برای استخراج تو در تو
            nested_output = nested.parent / f"{nested.stem}_extracted"
            nested_output.mkdir(parents=True, exist_ok=True)

            # استخراج آرچیو تو در تو
            ok = await self.extract(nested, nested_output, password)
            if not ok:
                logger.warning("Cannot extract nested archive: %s", nested.name)
                break

            # حذف فایل آرچیو اصلی
            try:
                nested.unlink()
                logger.debug("Deleted nested archive: %s", nested.name)
            except Exception as e:
                logger.warning("Cannot delete nested archive %s: %s", nested.name, e)
                # اگر نتوانست حذف کند، از حلقه خارج می‌شویم
                break

            # انتقال فایل‌های استخراج شده به دایرکتوری اصلی
            try:
                for file in nested_output.rglob("*"):
                    if file.is_file():
                        dest = output_dir / file.relative_to(nested_output)
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        shutil.move(str(file), str(dest))
                # حذف دایرکتوری موقت
                shutil.rmtree(nested_output, ignore_errors=True)
            except Exception as e:
                logger.warning("Error moving extracted files: %s", e)

            current_depth += 1

        logger.info("Recursive extraction completed. Depth: %d", current_depth)
        return True

    # =====================================================
    # Utilities
    # =====================================================

    def count_files(
        self,
        directory: Path,
    ) -> int:

        return len(self.list_files(directory))

    def total_size(
        self,
        directory: Path,
    ) -> int:

        if not directory.exists():
            return 0

        total = 0
        for file in directory.rglob("*"):
            if file.is_file():
                try:
                    total += file.stat().st_size
                except OSError:
                    continue
        return total

    def remove_empty_dirs(
        self,
        directory: Path,
    ):

        if not directory.exists():
            return

        # حذف دایرکتوری‌های خالی از عمیق‌ترین به سطحی‌ترین
        for path in sorted(directory.rglob("*"), reverse=True):
            if path.is_dir():
                try:
                    path.rmdir()
                    logger.debug("Removed empty directory: %s", path)
                except OSError:
                    # دایرکتوری خالی نیست یا دسترسی نداریم
                    pass

    def flatten_directory(
        self,
        directory: Path,
    ):

        if not directory.exists():
            return

        files = self.list_files(directory)

        for file in files:
            if file.parent == directory:
                continue

            # ایجاد نام جدید بدون تداخل
            destination = directory / file.name
            counter = 1

            while destination.exists():
                stem = file.stem
                suffix = file.suffix
                destination = directory / f"{stem}_{counter}{suffix}"
                counter += 1

            try:
                shutil.move(file, destination)
                logger.debug("Moved %s to %s", file.name, destination.name)
            except Exception as e:
                logger.warning("Failed to move %s: %s", file, e)

        self.remove_empty_dirs(directory)

    # =====================================================
    # Validation
    # =====================================================

    def validate_archive(
        self,
        archive_file: Path,
    ) -> bool:

        if not archive_file.exists():
            logger.error("Archive does not exist: %s", archive_file)
            return False

        if not self.is_archive(archive_file):
            logger.error("Not a supported archive: %s", archive_file)
            return False

        # بررسی اینکه فایل خالی نباشد
        if archive_file.stat().st_size == 0:
            logger.error("Archive is empty: %s", archive_file)
            return False

        return True


# نمونه‌سازی سرویس
archive_service = ArchiveService()