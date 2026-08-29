"""
Structured error tracking for job failures.

When a job blows up somewhere in the worker pipeline, two things must
happen — with very different audiences:

1. The USER gets one short, safe line (USER_SAFE_ERROR_TEXT). Never a
   stack trace, never an exception string, never a filesystem path —
   exception text is attacker/machine-controlled and has already been
   observed leaking internal details to end users via the old
   `str(e)[:300]` path.

2. The ADMINS get a full structured report (build_admin_report) with the
   exact pipeline stage, an error code, the job/user/file context and
   the complete traceback — so a failure can be debugged from the phone
   without SSHing into the box.

Everything that crosses a Telegram boundary goes through sanitize_text()
first: known secret values (BOT_TOKEN / API_HASH) and anything shaped
like a bot token are redacted, absolute paths under the project root are
replaced with a placeholder, and URL query strings (which routinely carry
signed tokens) are stripped. The traceback is preserved but sanitized
with the same rules — a traceback contains paths and, occasionally, the
values that triggered the error.
"""
from __future__ import annotations

import re
import traceback
from datetime import datetime
from enum import Enum
from html import escape as html_escape
from time import time

from config import ErrorReporting, Paths, Telegram


class JobStage(str, Enum):
    """Where in the request lifecycle a failure happened. The pipeline
    order is: the bot validates and queues the job (VALIDATION/QUEUE),
    the worker downloads the bytes (DOWNLOAD), converts/extracts them
    (PROCESSING), delivers the results to Telegram (UPLOAD, and
    TELEGRAM_API for delivery-control calls), and finally removes the
    working directories (CLEANUP)."""

    VALIDATION = "VALIDATION"

    DOWNLOAD = "DOWNLOAD"

    QUEUE = "QUEUE"

    PROCESSING = "PROCESSING"

    UPLOAD = "UPLOAD"

    TELEGRAM_API = "TELEGRAM_API"

    CLEANUP = "CLEANUP"


class ErrorCode(str, Enum):
    """Stable machine-readable error codes, one per failure class. These
    go into the admin report (and structured logs) so failures can be
    counted/filtered without parsing free text."""

    VALIDATION_FAILED = "VALIDATION_FAILED"

    DOWNLOAD_FAILED = "DOWNLOAD_FAILED"

    QUEUE_FAILED = "QUEUE_FAILED"

    PROCESSING_FAILED = "PROCESSING_FAILED"

    UPLOAD_FAILED = "UPLOAD_FAILED"

    TELEGRAM_API_FAILED = "TELEGRAM_API_FAILED"

    CLEANUP_FAILED = "CLEANUP_FAILED"

    UNKNOWN = "UNKNOWN"


# Shown to the user for ANY internal failure. The bridge prefixes it
# with "❌ خطا:" (handlers/bridge.py), so the user sees a friendly line
# with zero internal detail, no matter what actually blew up.
USER_SAFE_ERROR_TEXT = (
    "پردازش فایل ناموفق بود. لطفاً بعداً دوباره تلاش کنید."
)

# Matches Bot API token shape (<bot id>:<secret chars>) anywhere in
# free text — exception messages from HTTP libraries love to embed URLs
# that contain the token. No leading \b: inside a URL the id is glued
# to "bot" (…/bot123456:AAE…), where a word boundary never occurs.
_BOT_TOKEN_RE = re.compile(r"\d{4,}:[A-Za-z0-9_-]{25,}")

# http(s) query strings are scrubbed whole: signed/download URLs (S3,
# Telegram file refs, CDNs) put short-lived credentials in the query.
_URL_QUERY_RE = re.compile(r"(https?://[^\s?]+)\?\S*")

_REDACTED = "[REDACTED]"

_PATH_PLACEHOLDER = "<server-path>"

# Config values that must never appear in anything sent to Telegram.
# Resolved once at import — these are process constants.
_SECRET_VALUES = tuple(
    value
    for value in (
        getattr(Telegram, "BOT_TOKEN", ""),
        getattr(Telegram, "API_HASH", ""),
    )
    if value
)


def sanitize_text(text: str) -> str:
    """Makes a piece of internal text safe to send to Telegram (user OR
    admin): redacts configured secrets and bot-token-shaped strings,
    scrubs URL query strings, and replaces absolute paths under the
    project root with a placeholder. Idempotent — running it twice
    changes nothing."""

    if not text:
        return ""

    safe = str(text)

    for secret in _SECRET_VALUES:
        safe = safe.replace(secret, _REDACTED)

    safe = _BOT_TOKEN_RE.sub(_REDACTED, safe)

    safe = _URL_QUERY_RE.sub(r"\1?" + _REDACTED, safe)

    base = str(Paths.BASE)

    if base and base != "/":
        safe = safe.replace(base, _PATH_PLACEHOLDER)

    return safe


def _format_duration(seconds: float | None) -> str:
    """Human-readable operation duration for the report."""

    if seconds is None:
        return ""

    if seconds < 60:
        return f"{seconds:.1f}s"

    minutes, rest = divmod(seconds, 60)

    return f"{int(minutes)}m {rest:.1f}s"


def _truncate_traceback(tb_text: str, budget: int) -> str:
    """Keeps the HEAD of the traceback (the frames closest to the
    pipeline call site — usually the most useful ones) when the report
    has to fit a Telegram message."""

    if len(tb_text) <= budget:
        return tb_text

    marker = f"\n... [traceback truncated, {len(tb_text) - budget} chars dropped] ...\n"

    return tb_text[: max(budget - len(marker), 0)] + marker


def build_admin_report(
    *,
    stage: JobStage | str,
    code: ErrorCode | str = ErrorCode.UNKNOWN,
    exception: BaseException | None = None,
    exception_message: str | None = None,
    job_id: str = "unknown",
    user_id: int | None = None,
    username: str = "",
    chat_id: int | None = None,
    file_name: str = "",
    file_size: int = 0,
    file_type: str = "",
    operation: str = "",
    job_status: str = "",
    options: dict | None = None,
    url: str = "",
    duration_seconds: float | None = None,
    extra_lines: list[str] | None = None,
    occurred_at: float | None = None,
) -> str:
    """Builds the full admin-facing failure report as HTML (send it with
    parse_mode="HTML"). Every dynamic value is HTML-escaped and passed
    through sanitize_text(), and the whole thing is capped under
    ErrorReporting.MAX_REPORT_CHARS so it always fits one Telegram
    message with the traceback intact-or-explicitly-truncated.

    Only non-empty fields are included; `exception` (preferred) or
    `exception_message` supplies the error detail and traceback."""

    # --------------------------------------------------------------
    # Error detail + traceback
    # --------------------------------------------------------------

    if exception is not None:

        exc_headline = f"{type(exception).__name__}: {exception}"

        tb_text = "".join(
            traceback.format_exception(
                type(exception),
                exception,
                exception.__traceback__,
            )
        )

    elif exception_message:

        exc_headline = exception_message
        tb_text = ""

    else:
        exc_headline = "no exception detail available"
        tb_text = ""

    exc_headline = sanitize_text(exc_headline)

    # --------------------------------------------------------------
    # Assemble the field block (HTML, escaped everywhere)
    # --------------------------------------------------------------

    def _field(label: str, value) -> str:

        if value is None or value == "":
            return ""

        return f"<b>{label}:</b> {html_escape(sanitize_text(str(value)))}\n"

    fields = "".join(
        [
            _field("code", getattr(code, "value", code)),
            _field("stage", getattr(stage, "value", stage)),
            _field("job_id", job_id),
            _field("user_id", user_id),
            _field("username", username),
            _field("chat_id", chat_id),
            _field("operation", operation),
            _field("job_status", job_status),
            _field("file", file_name),
            _field("file_size", f"{file_size} bytes" if file_size else ""),
            _field("file_type", file_type),
            _field("duration", _format_duration(duration_seconds)),
            _field("url", url),
        ]
    )

    options_lines = ""

    for key, value in (options or {}).items():

        if value in (None, "", False):
            continue

        options_lines += (
            f"  {html_escape(sanitize_text(str(key)))}"
            f"={html_escape(sanitize_text(str(value)))}\n"
        )

    if options_lines:
        options_lines = f"<b>options:</b>\n{options_lines}"

    extra_block = ""

    for line in extra_lines or []:
        extra_block += f"{html_escape(sanitize_text(str(line)))}\n"

    when = datetime.fromtimestamp(
        occurred_at if occurred_at is not None else time()
    ).strftime("%Y-%m-%d %H:%M:%S")

    # --------------------------------------------------------------
    # Traceback budget: whatever the fixed parts don't use, minus a
    # safety margin for the HTML wrapper.
    # --------------------------------------------------------------

    tb_block = ""

    if tb_text:

        # Budget accounting happens on the ESCAPED traceback (html_escape
        # expands it), so the finished report is guaranteed to fit and
        # the truncation marker can never be eaten by the final cap.
        header_len = 60 + len(exc_headline)

        wrapper_len = len("<b>traceback:</b>\n<pre></pre>") + 50

        fixed = (
            len(fields)
            + len(options_lines)
            + len(extra_block)
            + header_len
            + wrapper_len
        )

        budget = ErrorReporting.MAX_REPORT_CHARS - fixed

        tb_block = (
            "<b>traceback:</b>\n<pre>"
            + _truncate_traceback(
                html_escape(sanitize_text(tb_text).strip()),
                max(budget, 400),
            )
            + "</pre>"
        )

    separator = "\n" if (options_lines or extra_block) else ""

    report = (
        "🚨 <b>Job failure report</b>\n"
        f"🕐 {when}\n"
        f"🧾 {html_escape(exc_headline)}\n\n"
        f"{fields}"
        f"{options_lines}"
        f"{separator}"
        f"{extra_block}"
        f"{tb_block}"
    )

    # Last-resort cap (e.g. a pathological exception message): drop from
    # the end — the traceback lives there and the headline/context at
    # the top is what identifies the failure.
    if len(report) > ErrorReporting.MAX_REPORT_CHARS:
        report = report[: ErrorReporting.MAX_REPORT_CHARS - 1] + "…"

    return report

    return safe
