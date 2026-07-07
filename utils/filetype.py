from pathlib import Path

from core.logger import get_logger

logger = get_logger(__name__)


class FileTypeDetector:

    VIDEO_EXTENSIONS = {
        ".mp4",
        ".mkv",
        ".avi",
        ".mov",
        ".wmv",
        ".flv",
        ".webm",
        ".m4v",
        ".ts",
    }

    AUDIO_EXTENSIONS = {
        ".mp3",
        ".m4a",
        ".aac",
        ".wav",
        ".ogg",
        ".opus",
        ".flac",
        ".wma",
    }

    PDF_EXTENSIONS = {
        ".pdf",
    }

    ARCHIVE_EXTENSIONS = {
        ".zip",
        ".rar",
        ".7z",
        ".tar",
        ".gz",
        ".bz2",
        ".xz",
    }

    IMAGE_EXTENSIONS = {
        ".jpg",
        ".jpeg",
        ".png",
        ".bmp",
        ".gif",
        ".webp",
    }

    @classmethod
    def detect(
        cls,
        mime_type: str | None,
        filename: str | None,
    ) -> str:

        mime_type = (mime_type or "").lower()

        extension = Path(filename or "").suffix.lower()

        # ---------- VIDEO ----------

        if mime_type.startswith("video/"):
            return "VIDEO"

        if extension in cls.VIDEO_EXTENSIONS:
            return "VIDEO"

        # ---------- AUDIO ----------

        if mime_type.startswith("audio/"):
            return "AUDIO"

        if extension in cls.AUDIO_EXTENSIONS:
            return "AUDIO"

        # ---------- PDF ----------

        if mime_type == "application/pdf":
            return "PDF"

        if extension in cls.PDF_EXTENSIONS:
            return "PDF"

        # ---------- ARCHIVE ----------

        archive_mimes = {
            "application/zip",
            "application/x-rar",
            "application/vnd.rar",
            "application/x-7z-compressed",
            "application/x-tar",
            "application/gzip",
        }

        if mime_type in archive_mimes:
            return "ARCHIVE"

        if extension in cls.ARCHIVE_EXTENSIONS:
            return "ARCHIVE"

        # ---------- IMAGE ----------

        if mime_type.startswith("image/"):
            return "IMAGE"

        if extension in cls.IMAGE_EXTENSIONS:
            return "IMAGE"

        logger.warning(
            "Unknown file type | mime=%s | file=%s",
            mime_type,
            filename,
        )

        return "UNKNOWN"