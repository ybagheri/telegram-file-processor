"""
Tests for per-user file-submission rate limiting: the pure sliding-window
logic in utils/rate_limit.py, and handlers/core.py's
check_submission_rate_limit wiring (shared state dict, config knobs).
"""

import time

from types import SimpleNamespace

from config import RateLimiting

from utils.rate_limit import (
    is_rate_limited,
    prune_timestamps,
    record_submission,
)


# ======================================================================
# Pure sliding-window logic (utils/rate_limit.py)
# ======================================================================


def test_prune_drops_only_timestamps_older_than_window():

    history = [1.0, 50.0, 95.0, 105.0]

    prune_timestamps(history, now=100.0, window_seconds=30.0)

    assert history == [95.0, 105.0]


def test_prune_mutates_in_place():

    history = [1.0, 2.0]

    prune_timestamps(history, now=100.0, window_seconds=10.0)

    assert history == []


def test_under_the_cap_is_allowed():

    history = [90.0, 95.0]

    assert is_rate_limited(history, now=100.0, max_files=3, window_seconds=60.0) is False


def test_at_the_cap_is_rejected():

    history = [90.0, 95.0, 98.0]

    assert is_rate_limited(history, now=100.0, max_files=3, window_seconds=60.0) is True


def test_old_submissions_expire_out_of_the_window():

    # Three submissions, but all outside the 10-second window.
    history = [1.0, 2.0, 3.0]

    assert is_rate_limited(history, now=100.0, max_files=3, window_seconds=10.0) is False


def test_max_files_zero_disables_the_limiter():

    history = [1.0, 2.0, 3.0, 4.0]

    assert is_rate_limited(history, now=100.0, max_files=0, window_seconds=60.0) is False


def test_record_submission_appends():

    history = []

    record_submission(history, now=123.0)

    assert history == [123.0]


# ======================================================================
# handlers/core.py wiring
# ======================================================================


def _fake_submission_message():

    return SimpleNamespace(
        from_user=SimpleNamespace(id=777),
        chat=SimpleNamespace(type="private", id=777),
        document=SimpleNamespace(file_name="video.mp4", mime_type="video/mp4"),
        video=None,
        audio=None,
        photo=None,
        text=None,
    )


def test_check_submission_rate_limit_allows_then_rejects(monkeypatch):

    import handlers.core as core

    monkeypatch.setattr(RateLimiting, "MAX_FILES", 2)
    monkeypatch.setattr(RateLimiting, "WINDOW_MINUTES", 10)

    core.user_submission_times.pop(777, None)

    message = _fake_submission_message()

    try:
        assert core.check_submission_rate_limit(777, message) is True
        assert core.check_submission_rate_limit(777, message) is True
        assert core.check_submission_rate_limit(777, message) is False
    finally:
        core.user_submission_times.pop(777, None)


def test_check_submission_rate_limit_window_expiry(monkeypatch):

    import handlers.core as core

    monkeypatch.setattr(RateLimiting, "MAX_FILES", 1)
    monkeypatch.setattr(RateLimiting, "WINDOW_MINUTES", 10)

    core.user_submission_times.pop(778, None)

    message = _fake_submission_message()

    try:
        assert core.check_submission_rate_limit(778, message) is True

        # Simulate the single submission being 20 minutes old.
        core.user_submission_times[778][0] = time.time() - 20 * 60

        assert core.check_submission_rate_limit(778, message) is True
    finally:
        core.user_submission_times.pop(778, None)


def test_check_submission_rate_limit_records_per_user():

    import handlers.core as core

    core.user_submission_times.pop(779, None)
    core.user_submission_times.pop(780, None)

    try:
        core.check_submission_rate_limit(779, _fake_submission_message())

        # Only user 779's history was touched — user 780 has none.
        assert 779 in core.user_submission_times
        assert 780 not in core.user_submission_times
    finally:
        core.user_submission_times.pop(779, None)
        core.user_submission_times.pop(780, None)
