from pathlib import Path

from config import Processing


class FileType:

    VIDEO = "VIDEO"

    AUDIO = "AUDIO"

    IMAGE = "IMAGE"

    PDF = "PDF"

    ARCHIVE = "ARCHIVE"

    UNKNOWN = "UNKNOWN"


class FileTypeDetector:

    @classmethod
    def detect(
        cls,
        filename: str = "",
        mime_type: str = "",
    ) -> str:

        suffix = Path(filename).suffix.lower()

        mime_type = (mime_type or "").lower()

        # ----------------------------
        # Video
        # ----------------------------

        if (
            suffix in Processing.SUPPORTED_VIDEO_EXTENSIONS
            or mime_type.startswith("video/")
        ):

            return FileType.VIDEO

        # ----------------------------
        # Audio
        # ----------------------------

        if (
            suffix in Processing.SUPPORTED_AUDIO_EXTENSIONS
            or mime_type.startswith("audio/")
        ):

            return FileType.AUDIO

        # ----------------------------
        # Image
        # ----------------------------

        if (
            suffix in Processing.SUPPORTED_IMAGE_EXTENSIONS
            or mime_type.startswith("image/")
        ):

            return FileType.IMAGE

        # ----------------------------
        # PDF
        # ----------------------------

        if (
            suffix == ".pdf"
            or mime_type == "application/pdf"
        ):

            return FileType.PDF

        # ----------------------------
        # Archive
        # ----------------------------

        if suffix in Processing.SUPPORTED_ARCHIVE_EXTENSIONS:

            return FileType.ARCHIVE

        return FileType.UNKNOWN

    @classmethod
    def is_video(
        cls,
        filename: str,
        mime_type: str = "",
    ):

        return cls.detect(
            filename,
            mime_type,
        ) == FileType.VIDEO

    @classmethod
    def is_audio(
        cls,
        filename: str,
        mime_type: str = "",
    ):

        return cls.detect(
            filename,
            mime_type,
        ) == FileType.AUDIO

    @classmethod
    def is_archive(
        cls,
        filename: str,
        mime_type: str = "",
    ):

        return cls.detect(
            filename,
            mime_type,
        ) == FileType.ARCHIVE

    @classmethod
    def is_pdf(
        cls,
        filename: str,
        mime_type: str = "",
    ):

        return cls.detect(
            filename,
            mime_type,
        ) == FileType.PDF

    @classmethod
    def is_image(
        cls,
        filename: str,
        mime_type: str = "",
    ):

        return cls.detect(
            filename,
            mime_type,
        ) == FileType.IMAGE
