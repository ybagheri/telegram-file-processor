import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


class Paths:

    BASE = Path(__file__).resolve().parent

    DOWNLOADS = BASE / "downloads"
    OUTPUTS = BASE / "outputs"
    LOGS = BASE / "logs"

    LOGO = BASE / "logo"

    SESSIONS = BASE / "sessions"

    CACHE = BASE / "cache"

    TEMP = BASE / "temp"

    CONFIG = BASE / "config_data"

    LOGO_FILE = LOGO / "logo.jpg"

    JOB_FOLDERS = (
        "input",
        "output",
        "extracted",
        "temp",
        "thumbs",
    )

    @classmethod
    def create(cls):

        directories = [

            cls.DOWNLOADS,
            cls.OUTPUTS,
            cls.LOGS,

            cls.LOGO,

            cls.SESSIONS,

            cls.CACHE,

            cls.TEMP,

            cls.CONFIG,

        ]

        for directory in directories:

            directory.mkdir(
                parents=True,
                exist_ok=True,
            )


class Telegram:

    API_ID = int(
        os.getenv(
            "API_ID",
            "0",
        )
    )

    API_HASH = os.getenv(
        "API_HASH",
        "",
    )

    BOT_TOKEN = os.getenv(
        "BOT_TOKEN",
        "",
    )

    GROUP_ID = int(
        os.getenv(
            "GROUP_ID",
            "0",
        )
    )

    SESSION_NAME = os.getenv(
        "SESSION_NAME",
        "worker",
    )


class FFmpeg:

    EXECUTABLE = os.getenv(
        "FFMPEG",
        "/usr/bin/ffmpeg",
    )

    PROBE = os.getenv(
        "FFPROBE",
        "/usr/bin/ffprobe",
    )


class Metadata:

    DEFAULT_ARTIST = os.getenv(
        "DEFAULT_ARTIST",
        "telegram-file-processor",
    )

    DEFAULT_ALBUM = os.getenv(
        "DEFAULT_ALBUM",
        "",
    )

    DEFAULT_TITLE = os.getenv(
        "DEFAULT_TITLE",
        "",
    )

    DEFAULT_COMMENT = os.getenv(
        "DEFAULT_COMMENT",
        "",
    )

    DEFAULT_COPYRIGHT = os.getenv(
        "DEFAULT_COPYRIGHT",
        "",
    )


class Processing:

    THUMBNAIL_SECOND = int(
        os.getenv(
            "THUMBNAIL_SECOND",
            "3",
        )
    )

    DEFAULT_VIDEO_QUALITY = os.getenv(
        "DEFAULT_VIDEO_QUALITY",
        "360",
    )

    DEFAULT_AUDIO_BITRATE = os.getenv(
        "DEFAULT_AUDIO_BITRATE",
        "128k",
    )

    MAX_FILE_SIZE = int(
        os.getenv(
            "MAX_FILE_SIZE",
            str(
                5 * 1024 * 1024 * 1024
            ),
        )
    )

    DELETE_JOB_AFTER_FINISH = (
        os.getenv(
            "DELETE_JOB_AFTER_FINISH",
            "true",
        ).lower()
        == "true"
    )

    VIDEO_PROFILES = {

        "144": {
            "width": 256,
            "height": 144,
            "crf": 33,
            "preset": "ultrafast",
        },

        "240": {
            "width": 426,
            "height": 240,
            "crf": 31,
            "preset": "ultrafast",
        },

        "360": {
            "width": 640,
            "height": 360,
            "crf": 29,
            "preset": "veryfast",
        },

        "480": {
            "width": 854,
            "height": 480,
            "crf": 27,
            "preset": "medium",
        },

        "720": {
            "width": 1280,
            "height": 720,
            "crf": 25,
            "preset": "slow",
        },

    }

    SUPPORTED_VIDEO_EXTENSIONS = {
        ".mp4",
        ".mkv",
        ".avi",
        ".mov",
        ".webm",
    }

    SUPPORTED_AUDIO_EXTENSIONS = {
        ".mp3",
        ".m4a",
        ".aac",
        ".wav",
        ".flac",
        ".ogg",
    }

    SUPPORTED_ARCHIVE_EXTENSIONS = {
        ".zip",
        ".rar",
        ".7z",
    }

    SUPPORTED_IMAGE_EXTENSIONS = {
        ".jpg",
        ".jpeg",
        ".png",
        ".webp",
    }


class Logging:

    LEVEL = os.getenv(
        "LOG_LEVEL",
        "INFO",
    )


class Config:

    @classmethod
    def validate(cls):

        missing = []

        if Telegram.API_ID == 0:
            missing.append("API_ID")

        if not Telegram.API_HASH:
            missing.append("API_HASH")

        if not Telegram.BOT_TOKEN:
            missing.append("BOT_TOKEN")

        if Telegram.GROUP_ID == 0:
            missing.append("GROUP_ID")

        if missing:

            raise RuntimeError(
                "Missing config values: "
                + ", ".join(missing)
            )


Paths.create()

Config.validate()