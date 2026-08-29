"""
Real-time progress reporting for a running job: one Telegram message that
gets *edited* in place as the job moves through download -> processing ->
upload, instead of spamming the chat with a new message per update.

Design notes (see CLAUDE.md if this needs revisiting later):

* The worker process already owns a usable Bot API client
  (`telegram_service.bot`, an aiogram `Bot`) even though the worker
  otherwise talks to Telegram over the separate Telethon/MTProto client
  for the bridge group. Sending/editing a message to the destination
  chat directly through the Bot API here means progress reporting needs
  no new bridge protocol message, no change to bot.py's routing, and no
  cross-process state — it's a self-contained, best-effort side channel
  that can fail without taking anything else down.

* Throttling is done with a single periodic "ticker" task per job rather
  than rate-limiting every call site individually: download/upload
  progress callbacks (and the ffmpeg progress parser in services/media.py)
  just update a few plain attributes as fast as they like — cheap, no
  I/O, never blocks the transfer they're reporting on. The ticker wakes
  up on its own schedule (Progress.UPDATE_INTERVAL_SECONDS) and is the
  only thing that ever calls the Bot API, so the edit rate is bounded
  regardless of how chatty the underlying transfer is.

* A failed edit (flood wait, message deleted by the user, network hiccup)
  is swallowed here and never propagates — a broken progress message must
  never break the download/processing/upload it's describing.
"""
from __future__ import annotations

import asyncio
import time

from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter

from config import Progress
from core.logger import get_logger

logger = get_logger(__name__)

FILLED = "●"
EMPTY = "○"

STAGE_DOWNLOAD = "download"
STAGE_PROCESSING = "processing"
STAGE_UPLOAD = "upload"

_BACKGROUND_NOTE = (
    "⚠️ توجه: بروزرسانی پیشرفت ممکن است کمی طول بکشد و به معنای شکست "
    "درخواست نیست. پردازش همچنان در پس‌زمینه در حال انجام است."
)

_STAGE_TITLES = {
    STAGE_DOWNLOAD: "📥 در حال دانلود به سرور...",
    STAGE_PROCESSING: "⚙️ در حال پردازش...",
    STAGE_UPLOAD: "📤 در حال ارسال به تلگرام...",
}


# =====================================================
# Pure formatting helpers (no Telegram/asyncio involved — easy to test
# in isolation).
# =====================================================

def render_bar(fraction: float | None, length: int = Progress.BAR_LENGTH) -> str:
    """`fraction` in [0, 1], or None for an indeterminate/"active" bar
    (spec: never fake a percentage — an indeterminate state gets a fixed
    half-filled bar instead of a real-looking-but-invented number)."""

    if fraction is None:
        filled = length // 2
    else:
        fraction = max(0.0, min(1.0, fraction))
        filled = round(length * fraction)

    filled = max(0, min(length, filled))

    return FILLED * filled + EMPTY * (length - filled)


def format_percent(fraction: float) -> str:

    percent = max(0.0, min(100.0, fraction * 100))

    if percent < 10:
        return f"{percent:.1f}%"

    return f"{percent:.0f}%"


def format_bytes(num_bytes: float | None) -> str:

    if num_bytes is None:
        return "—"

    num_bytes = max(0.0, float(num_bytes))

    for unit, divisor in (("GB", 1024 ** 3), ("MB", 1024 ** 2), ("KB", 1024)):
        if num_bytes >= divisor:
            return f"{num_bytes / divisor:.1f} {unit}"

    return f"{int(num_bytes)} B"


def format_speed(bytes_per_second: float | None) -> str:

    if not bytes_per_second or bytes_per_second <= 0:
        return "—"

    return f"{format_bytes(bytes_per_second)}/s"


def format_eta(seconds: float | None) -> str:

    if seconds is None or seconds < 0 or seconds == float("inf"):
        return "—"

    seconds = int(seconds)

    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)

    if hours:
        return f"{hours}h {minutes}m {secs}s"

    if minutes:
        return f"{minutes}m {secs}s"

    return f"{secs}s"


def format_elapsed(seconds: float) -> str:

    return format_eta(seconds)


# =====================================================
# ProgressReporter
# =====================================================

class ProgressReporter:
    """One instance per job. Owns exactly one Telegram message in the
    job's destination chat, edited in place as the job progresses.
    Every public method is safe to call even when reporting is disabled
    or has failed to start — they just become no-ops."""

    def __init__(self, bot, chat_id: int, job_id: str):

        self.bot = bot
        self.chat_id = chat_id
        self.job_id = job_id

        self.enabled = bool(Progress.ENABLED) and bool(chat_id)

        self.message_id: int | None = None

        self.stage: str | None = None
        self.label: str = ""

        self._current: float = 0.0
        self._total: float | None = None
        self._fraction: float | None = None  # known-accurate fraction, e.g. ffmpeg progress

        self._stage_started_at: float = time.monotonic()
        self._stage_generation = 0

        self._sample_generation = -1
        self._sample_time: float = 0.0
        self._sample_bytes: float = 0.0
        self._speed: float = 0.0

        self._last_text: str | None = None
        self._task: asyncio.Task | None = None
        self._stopped = False

    # -------------------------------------------------
    # Lifecycle
    # -------------------------------------------------

    async def start(self):

        if not self.enabled:
            return

        self.begin_stage(STAGE_DOWNLOAD)

        try:
            text = self._render_text()
            sent = await self.bot.send_message(self.chat_id, text)
            self.message_id = sent.message_id
            self._last_text = text
        except Exception:
            logger.warning(
                "Could not send the initial progress message for job %s "
                "(progress reporting disabled for this job)",
                self.job_id,
                exc_info=True,
            )
            self.enabled = False
            return

        self._task = asyncio.create_task(self._tick_loop())

    async def finish(self, *, delete: bool = True):
        """Stops the ticker and (best-effort) removes the progress
        message. Must never raise — this runs from cleanup paths that
        can't be allowed to fail."""

        self._stopped = True

        if self._task is not None:

            self._task.cancel()

            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass

            self._task = None

        if delete and self.message_id is not None:

            try:
                await self.bot.delete_message(self.chat_id, self.message_id)
            except Exception:
                # Not worth logging — the message may already be gone,
                # or the job's own DONE/ERROR text will follow right
                # after this and make the stale progress line moot.
                pass

    # -------------------------------------------------
    # Stage transitions
    # -------------------------------------------------

    def begin_stage(self, stage: str, *, total: float | None = None, label: str = ""):

        self.stage = stage
        self.label = label

        self._current = 0.0
        self._total = total
        self._fraction = None

        self._stage_started_at = time.monotonic()
        self._stage_generation += 1

    def begin_processing(self, label: str = ""):
        self.begin_stage(STAGE_PROCESSING, label=label)

    # -------------------------------------------------
    # Progress callbacks — cheap, synchronous, called as often as the
    # underlying transfer/processor likes. Never touch the network.
    # -------------------------------------------------

    def report_bytes(self, current: float, total: float | None = None):

        self._current = current

        if total:
            self._total = total

    def report_fraction(self, fraction: float | None, *, elapsed: float | None = None):
        """For processing stages where a real (not invented) completion
        fraction is available, e.g. parsed from ffmpeg's `-progress`
        output against the input's known duration."""

        self._fraction = fraction

        if elapsed is not None:
            self._current = elapsed

    def make_download_callback(self, *, total: float | None = None, label: str = ""):

        self.begin_stage(STAGE_DOWNLOAD, total=total, label=label)

        def _callback(current, total_):
            self.report_bytes(current, total_)

        return _callback

    def make_upload_callback(self, *, total: float | None = None, label: str = ""):

        self.begin_stage(STAGE_UPLOAD, total=total, label=label)

        def _callback(current, total_):
            self.report_bytes(current, total_)

        return _callback

    def make_processing_fraction_callback(self, *, total_duration: float, label: str = ""):
        """For ffmpeg conversions where the input's duration is known
        upfront (services/media.py parses `-progress` output and calls
        this with (elapsed_seconds, total_seconds, fraction))."""

        self.begin_stage(STAGE_PROCESSING, total=total_duration, label=label)

        def _callback(elapsed_seconds, total_seconds, fraction):
            if total_seconds:
                self._total = total_seconds
            self.report_fraction(fraction, elapsed=elapsed_seconds)

        return _callback

    # -------------------------------------------------
    # Rendering
    # -------------------------------------------------

    def _speed_and_eta(self, now: float) -> tuple[float, float | None]:
        """Instantaneous-ish speed computed between ticker samples (not
        a since-the-beginning average, so it reacts to real throughput
        changes), plus the ETA it implies. Resyncs silently on a stage
        change so switching stages never produces a bogus negative or
        huge delta."""

        if self._sample_generation != self._stage_generation:
            self._sample_generation = self._stage_generation
            self._sample_time = now
            self._sample_bytes = self._current
            self._speed = 0.0

        else:
            elapsed = now - self._sample_time

            if elapsed >= 0.5:
                delta = self._current - self._sample_bytes
                self._speed = max(0.0, delta / elapsed) if elapsed > 0 else self._speed
                self._sample_time = now
                self._sample_bytes = self._current

        eta = None

        if self._total and self._speed > 0:
            remaining = max(0.0, self._total - self._current)
            eta = remaining / self._speed

        return self._speed, eta

    def _render_text(self) -> str:

        now = time.monotonic()
        elapsed_in_stage = now - self._stage_started_at

        if self.stage == STAGE_PROCESSING and self._fraction is None:
            return self._render_indeterminate_processing(elapsed_in_stage)

        if self.stage == STAGE_PROCESSING:
            return self._render_fractional_processing()

        return self._render_transfer(now)

    def _render_transfer(self, now: float) -> str:

        title = _STAGE_TITLES.get(self.stage, _STAGE_TITLES[STAGE_DOWNLOAD])

        speed, eta = self._speed_and_eta(now)

        lines = [title]

        if self._total:
            fraction = self._current / self._total if self._total else 0.0
            lines.append(f"[{render_bar(fraction)}]")
            lines.append(format_percent(fraction))
        else:
            # Spec: never invent a percentage when the total is unknown.
            lines.append(f"[{render_bar(None)}]")

        lines.append("حجم فایل:")
        lines.append(
            f"{format_bytes(self._current)} / {format_bytes(self._total)}"
            if self._total
            else f"{format_bytes(self._current)} (حجم کل نامشخص)"
        )

        lines.append("سرعت:")
        lines.append(format_speed(speed))

        lines.append("زمان تقریبی باقی‌مانده:")
        lines.append(format_eta(eta) if self._total else "—")

        lines.append("")
        lines.append(_BACKGROUND_NOTE)

        return "\n".join(lines)

    def _render_indeterminate_processing(self, elapsed: float) -> str:

        lines = [
            _STAGE_TITLES[STAGE_PROCESSING],
            f"[{render_bar(None)}]",
            "مرحله:",
            self.label or "پردازش فایل",
            "زمان سپری‌شده:",
            format_elapsed(elapsed),
            "",
            _BACKGROUND_NOTE,
        ]

        return "\n".join(lines)

    def _render_fractional_processing(self) -> str:

        fraction = max(0.0, min(1.0, self._fraction or 0.0))
        elapsed = self._current
        eta = None

        if fraction > 0:
            eta = max(0.0, (elapsed / fraction) - elapsed)

        lines = [
            _STAGE_TITLES[STAGE_PROCESSING],
            f"[{render_bar(fraction)}]",
            format_percent(fraction),
            "زمان سپری‌شده:",
            format_elapsed(elapsed),
            "زمان تقریبی باقی‌مانده:",
            format_eta(eta),
            "",
            _BACKGROUND_NOTE,
        ]

        return "\n".join(lines)

    # -------------------------------------------------
    # Ticker
    # -------------------------------------------------

    async def _tick_loop(self):

        try:
            while not self._stopped:

                await asyncio.sleep(Progress.UPDATE_INTERVAL_SECONDS)

                if self._stopped:
                    break

                await self._edit_once()

        except asyncio.CancelledError:
            raise

        except Exception:
            # The ticker itself must never take the job down.
            logger.exception(
                "Progress ticker crashed for job %s", self.job_id
            )

    async def _edit_once(self):

        if self.message_id is None:
            return

        text = self._render_text()

        if text == self._last_text:
            return

        try:
            await self.bot.edit_message_text(
                chat_id=self.chat_id,
                message_id=self.message_id,
                text=text,
            )
            self._last_text = text

        except TelegramRetryAfter:
            # Skip this tick; the next scheduled tick will try again.
            # Never sleep/block here — a failed progress update must
            # never delay the actual job.
            logger.warning(
                "Progress edit rate-limited for job %s, skipping this tick",
                self.job_id,
            )

        except TelegramBadRequest:
            # Most commonly "message is not modified" (a race with the
            # equality check above) or the message/chat having gone
            # away (deleted by the user, bot blocked, ...). Either way,
            # a broken progress message must not affect the job.
            pass

        except Exception:
            logger.exception(
                "Progress update failed for job %s", self.job_id
            )


class NullProgressReporter(ProgressReporter):
    """A reporter that never talks to Telegram at all. Used whenever
    Progress.ENABLED is off or no destination chat is known — every
    call site can unconditionally do `job.progress.report_bytes(...)`
    etc. without an `if job.progress:` check everywhere."""

    def __init__(self):
        super().__init__(bot=None, chat_id=0, job_id="")
        self.enabled = False

    async def start(self):
        return

    async def finish(self, *, delete: bool = True):
        return
