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

    ADMIN_IDS = [
        int(x.strip())
        for x in os.getenv("ADMIN_IDS", "").split(",")
        if x.strip().lstrip("-").isdigit()
    ]

    ADMIN_CONTACT_USERNAME = os.getenv(
        "ADMIN_CONTACT_USERNAME",
        "",
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

    # Only bother checking free disk space for files whose declared size
    # is above this threshold — small files can't meaningfully fill the
    # disk, and the check would be noise for them.
    DISK_SPACE_CHECK_THRESHOLD = int(
        os.getenv(
            "DISK_SPACE_CHECK_THRESHOLD",
            str(
                256 * 1024 * 1024
            ),
        )
    )

    # How much working space we demand per declared byte (checked against
    # shutil.disk_usage on DOWNLOADS/TEMP/OUTPUTS before download starts).
    # > 1 because ffmpeg re-encoding and archive extraction both need
    # room for the output *in addition to* the raw input, and archives
    # can expand well past their compressed size.
    DISK_SPACE_SAFETY_FACTOR = float(
        os.getenv(
            "DISK_SPACE_SAFETY_FACTOR",
            "2.0",
        )
    )

    DELETE_JOB_AFTER_FINISH = (
        os.getenv(
            "DELETE_JOB_AFTER_FINISH",
            "true",
        ).lower()
        == "true"
    )

    # At most this many jobs run ffmpeg/extraction at once on the VPS;
    # the rest queue on an asyncio.Semaphore instead of launching
    # unbounded parallel processing.
    MAX_CONCURRENT_JOBS = int(
        os.getenv(
            "MAX_CONCURRENT_JOBS",
            "2",
        )
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


class Downloads:

    # Some small/personal file hosts let their TLS certificate lapse
    # without taking the site down — the content is still served fine,
    # only the certificate's expiry date is wrong. When enabled (the
    # default), a URL download that fails *specifically* because the
    # target's certificate has expired is retried once without
    # certificate verification. This is deliberately narrow: any other
    # verification failure (hostname mismatch, self-signed, untrusted
    # CA — signs that could mean active tampering rather than an
    # operator forgetting to renew) is never auto-retried, regardless
    # of this setting. See CLAUDE.md's change log for the report this
    # came from.
    ALLOW_EXPIRED_SSL_CERT_FALLBACK = (
        os.getenv(
            "ALLOW_EXPIRED_SSL_CERT_FALLBACK",
            "true",
        ).lower()
        == "true"
    )


class Progress:

    # Master on/off switch for the download/processing/upload progress
    # messages (services/progress.py). Disabling this restores the old
    # silent behaviour without touching any call sites.
    ENABLED = (
        os.getenv(
            "PROGRESS_ENABLED",
            "true",
        ).lower()
        == "true"
    )

    # Minimum time between edits of the single progress message, per job.
    # Telegram's practical edit-rate limit for one chat is a handful of
    # edits per second, but staying well under it (and under a pace a
    # human could even read) avoids ever tripping a flood wait for a
    # message that isn't essential to the job succeeding.
    UPDATE_INTERVAL_SECONDS = float(
        os.getenv(
            "PROGRESS_UPDATE_INTERVAL_SECONDS",
            "2.5",
        )
    )

    # Number of ● / ○ segments in the progress bar.
    BAR_LENGTH = int(
        os.getenv(
            "PROGRESS_BAR_LENGTH",
            "10",
        )
    )


class Logging:

    LEVEL = os.getenv(
        "LOG_LEVEL",
        "INFO",
    )


class Heartbeat:

    # How often (seconds) the worker sends a liveness ping through the
    # bridge group; bot.py's /status uses it to notice a crashed worker.
    INTERVAL_SECONDS = int(
        os.getenv(
            "HEARTBEAT_INTERVAL_SECONDS",
            "300",
        )
    )


class RateLimiting:

    # Per-user submission cap: at most MAX_FILES new file submissions per
    # WINDOW_MINUTES. MAX_FILES <= 0 disables the limiter entirely.
    # These are the TRIAL-tier values — admins bypass rate limiting and
    # paid users have their own (see Tiers.PAID_RATE_LIMIT_*), so this
    # section only governs trial/free users.
    MAX_FILES = int(
        os.getenv(
            "RATE_LIMIT_MAX_FILES",
            "5",
        )
    )

    WINDOW_MINUTES = int(
        os.getenv(
            "RATE_LIMIT_WINDOW_MINUTES",
            "10",
        )
    )


class Tiers:

    # Account-tier-specific limits. The classification itself lives in
    # utils/permissions.py (Admin = ADMIN_IDS, Paid = active authorized
    # user in the access DB, Trial = everyone else) — this class only
    # holds the numbers.
    #
    # Trial-only file-size cap in bytes, applied on top of MAX_FILE_SIZE.
    # 0 = disabled: trial users get the same MAX_FILE_SIZE as everyone.
    TRIAL_MAX_FILE_SIZE = int(
        os.getenv(
            "TRIAL_MAX_FILE_SIZE",
            "0",
        )
    )

    # Paid users' submission rate limit. 0 disables it — paid users are
    # by default NOT subject to the trial submission caps.
    PAID_RATE_LIMIT_MAX_FILES = int(
        os.getenv(
            "PAID_RATE_LIMIT_MAX_FILES",
            "0",
        )
    )

    PAID_RATE_LIMIT_WINDOW_MINUTES = int(
        os.getenv(
            "PAID_RATE_LIMIT_WINDOW_MINUTES",
            "10",
        )
    )


class ErrorReporting:

    # Structured failure reports (core/error_reporting.py) are DM'd to the
    # admins as one Telegram message. Telegram's hard limit is 4096 chars;
    # stay under it so the traceback never gets cut off mid-send by the
    # API rejecting the message.
    MAX_REPORT_CHARS = int(
        os.getenv(
            "ADMIN_REPORT_MAX_CHARS",
            "3500",
        )
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