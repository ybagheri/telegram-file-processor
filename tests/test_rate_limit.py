"""
Tests for per-user file-submission rate limiting: the pure sliding-window
logic in utils/rate_limit.py, and handlers/core.py's
check_submission_rate_limit wiring (shared state dict, config knobs,
tier-aware limits via utils/permissions.py).
"""

import time

import pytest

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


@pytest.fixture(autouse=True)
def clean_access_store():
    """The tier classification consults the real access_store singleton —
    keep it empty so every user here classifies as trial tier regardless
    of test order (same pattern as tests/test_router_wiring.py)."""

    from services.access_store import access_store

    access_store._conn.execute("DELETE FROM authorized_users")
    access_store._conn.commit()


def test_check_submission_rate_limit_allows_then_rejects(monkeypatch):

    import handlers.core as core

    monkeypatch.setattr(RateLimiting, "MAX_FILES", 2)
    monkeypatch.setattr(RateLimiting, "WINDOW_MINUTES", 10)

    core.user_submission_times.pop(777, None)

    try:
        # New tier-aware API: returns (allowed, max_files, window_minutes)
        # with the limits actually applied for the user's tier.
        assert core.check_submission_rate_limit(777) == (True, 2, 10)
        assert core.check_submission_rate_limit(777) == (True, 2, 10)
        assert core.check_submission_rate_limit(777) == (False, 2, 10)
    finally:
        core.user_submission_times.pop(777, None)


def test_check_submission_rate_limit_window_expiry(monkeypatch):

    import handlers.core as core

    monkeypatch.setattr(RateLimiting, "MAX_FILES", 1)
    monkeypatch.setattr(RateLimiting, "WINDOW_MINUTES", 10)

    core.user_submission_times.pop(778, None)

    try:
        assert core.check_submission_rate_limit(778) == (True, 1, 10)

        # Simulate the single submission being 20 minutes old.
        core.user_submission_times[778][0] = time.time() - 20 * 60

        assert core.check_submission_rate_limit(778) == (True, 1, 10)
    finally:
        core.user_submission_times.pop(778, None)


def test_check_submission_rate_limit_records_per_user():

    import handlers.core as core

    core.user_submission_times.pop(779, None)
    core.user_submission_times.pop(780, None)

    try:
        core.check_submission_rate_limit(779)

        # Only user 779's history was touched — user 780 has none.
        assert 779 in core.user_submission_times
        assert 780 not in core.user_submission_times
    finally:
        core.user_submission_times.pop(779, None)
        core.user_submission_times.pop(780, None)


def test_check_submission_rate_limit_admin_bypasses(monkeypatch):

    import handlers.core as core
    from config import Telegram

    core.user_submission_times.pop(781, None)

    monkeypatch.setattr(RateLimiting, "MAX_FILES", 1)
    monkeypatch.setattr(RateLimiting, "WINDOW_MINUTES", 10)
    monkeypatch.setattr(Telegram, "ADMIN_IDS", [781])

    try:
        # Admins bypass the limiter entirely: unlimited submissions, and
        # the reported limits are (0, 0) — "off".
        for _ in range(5):
            assert core.check_submission_rate_limit(781) == (True, 0, 0)

        # Nothing was recorded for the admin either.
        assert 781 not in core.user_submission_times
    finally:
        core.user_submission_times.pop(781, None)
