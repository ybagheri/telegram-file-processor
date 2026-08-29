"""
Tests for services/progress.py: the pure formatting helpers (bar
rendering, byte/speed/ETA formatting) and the ProgressReporter's
lifecycle/rendering logic. The Bot API itself is a simple fake object
here (not the real aiogram Bot) — services/telegram.py's own wiring of
`progress_callback` through to Telethon/ffmpeg is covered by
test_media_commands.py and test_url_downloader.py instead.
"""

import asyncio

import pytest

from services.progress import (
    ProgressReporter,
    format_bytes,
    format_eta,
    format_percent,
    format_speed,
    render_bar,
)


# ======================================================================
# Pure formatting helpers
# ======================================================================


@pytest.mark.parametrize(
    "fraction,expected_filled",
    [
        (0.0, 0),
        (0.2, 2),
        (0.5, 5),
        (0.8, 8),
        (1.0, 10),
        (0.31, 3),
        (0.67, 7),
    ],
)
def test_render_bar_matches_spec_examples(fraction, expected_filled):

    bar = render_bar(fraction)

    assert bar == "●" * expected_filled + "○" * (10 - expected_filled)


def test_render_bar_indeterminate_is_half_filled_not_a_guess():

    # No fraction available at all (spec: never invent a percentage) —
    # a fixed half-filled bar signals "still active", not a real number.
    assert render_bar(None) == "●" * 5 + "○" * 5


def test_format_percent_shows_a_decimal_only_below_ten_percent():

    assert format_percent(0.002) == "0.2%"
    assert format_percent(0.31) == "31%"
    assert format_percent(0.67) == "67%"
    assert format_percent(1.0) == "100%"


@pytest.mark.parametrize(
    "num_bytes,expected",
    [
        (500, "500 B"),
        (1024, "1.0 KB"),
        (1_048_576, "1.0 MB"),
        (1_048_576 * 531.5, "531.5 MB"),
        (1024 ** 3 * 2.5, "2.5 GB"),
        (None, "—"),
    ],
)
def test_format_bytes(num_bytes, expected):

    assert format_bytes(num_bytes) == expected


def test_format_speed_uses_format_bytes_plus_per_second():

    assert format_speed(1024 * 1024 * 1.8) == "1.8 MB/s"
    assert format_speed(0) == "—"
    assert format_speed(None) == "—"


@pytest.mark.parametrize(
    "seconds,expected",
    [
        (0, "0s"),
        (7, "7s"),
        (64, "1m 4s"),
        (304, "5m 4s"),
        (3725, "1h 2m 5s"),
        (None, "—"),
        (float("inf"), "—"),
        (-5, "—"),
    ],
)
def test_format_eta(seconds, expected):

    assert format_eta(seconds) == expected


# ======================================================================
# ProgressReporter
# ======================================================================


class FakeMessage:
    def __init__(self, message_id):
        self.message_id = message_id


class FakeBot:
    """Records every send/edit/delete instead of touching the network."""

    def __init__(self):
        self.sent = []
        self.edits = []
        self.deleted = []
        self._next_id = 1

    async def send_message(self, chat_id, text):
        self.sent.append((chat_id, text))
        msg = FakeMessage(self._next_id)
        self._next_id += 1
        return msg

    async def edit_message_text(self, chat_id, message_id, text):
        self.edits.append((chat_id, message_id, text))

    async def delete_message(self, chat_id, message_id):
        self.deleted.append((chat_id, message_id))


@pytest.mark.asyncio
async def test_start_sends_one_download_stage_message():

    bot = FakeBot()
    reporter = ProgressReporter(bot, chat_id=123, job_id="job1")

    await reporter.start()

    assert len(bot.sent) == 1
    assert bot.sent[0][0] == 123
    assert "دانلود" in bot.sent[0][1]
    assert reporter.message_id == 1

    await reporter.finish()


@pytest.mark.asyncio
async def test_finish_cancels_ticker_and_deletes_the_message():

    bot = FakeBot()
    reporter = ProgressReporter(bot, chat_id=123, job_id="job1")

    await reporter.start()
    await reporter.finish()

    assert bot.deleted == [(123, 1)]
    assert reporter._task is None or reporter._task.cancelled() or reporter._task.done()


@pytest.mark.asyncio
async def test_disabled_reporter_never_touches_the_bot():

    bot = FakeBot()

    # chat_id=0 is treated the same as "no destination known" —
    # reporting must become a safe no-op rather than erroring out.
    reporter = ProgressReporter(bot, chat_id=0, job_id="job1")

    await reporter.start()
    reporter.report_bytes(500, 1000)
    await reporter.finish()

    assert bot.sent == []
    assert bot.deleted == []


def test_download_progress_renders_bar_percent_size_speed():

    reporter = ProgressReporter(FakeBot(), chat_id=1, job_id="job1")

    total = round(531.5 * 1024 * 1024)
    current = 1024 * 1024  # exactly 1.0 MB

    reporter.begin_stage("download", total=total)
    reporter.report_bytes(current, total)

    text = reporter._render_text()

    assert "[" in text and "]" in text
    assert "0.2%" in text
    assert "1.0 MB / 531.5 MB" in text
    assert "دانلود" in text


def test_download_with_unknown_total_never_shows_a_percentage():

    reporter = ProgressReporter(FakeBot(), chat_id=1, job_id="job1")

    reporter.begin_stage("download", total=None)
    reporter.report_bytes(2048, None)

    text = reporter._render_text()

    assert "%" not in text
    assert "حجم کل نامشخص" in text


def test_indeterminate_processing_shows_no_percentage_and_a_label():

    reporter = ProgressReporter(FakeBot(), chat_id=1, job_id="job1")

    reporter.begin_processing(label="فشرده‌سازی")

    text = reporter._render_text()

    assert "%" not in text
    assert "فشرده‌سازی" in text
    assert render_bar(None) in text


def test_fractional_processing_shows_a_real_percentage_and_eta():

    reporter = ProgressReporter(FakeBot(), chat_id=1, job_id="job1")

    callback = reporter.make_processing_fraction_callback(total_duration=100.0)
    callback(25.0, 100.0, 0.25)

    text = reporter._render_text()

    assert "25%" in text
    assert render_bar(0.25) in text


@pytest.mark.asyncio
async def test_broken_edit_never_raises_out_of_the_ticker():

    class ExplodingBot(FakeBot):
        async def edit_message_text(self, chat_id, message_id, text):
            raise RuntimeError("Telegram is having a bad day")

    reporter = ProgressReporter(ExplodingBot(), chat_id=1, job_id="job1")

    await reporter.start()
    reporter.report_bytes(1, 2)

    # Directly exercise the tick that would normally fire on a timer —
    # it must swallow the failure rather than propagate it.
    await reporter._edit_once()

    await reporter.finish()


@pytest.mark.asyncio
async def test_a_failed_initial_send_disables_reporting_without_raising():

    class BrokenBot(FakeBot):
        async def send_message(self, chat_id, text):
            raise RuntimeError("network is down")

    reporter = ProgressReporter(BrokenBot(), chat_id=1, job_id="job1")

    await reporter.start()  # must not raise

    assert reporter.enabled is False
    assert reporter.message_id is None

    await reporter.finish()  # must also stay a no-op
