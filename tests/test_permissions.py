"""
Tests for the account-tier system in utils/permissions.py: the
Admin/Paid/Trial classification and the per-tier limit resolution
(submission rate limit, file-size cap) that the bot and the worker both
rely on. The classification is kept pure here by stubbing the access
store — see the tier_store fixture.
"""

import pytest

from config import Processing, RateLimiting, Tiers, Telegram

from utils.permissions import (
    AccountTier,
    check_tier_submission,
    get_account_tier,
    get_tier_from_payload,
    max_file_size_for_tier,
    submission_rate_limit_for_tier,
)


class _StubAccessStore:
    """Stands in for the access_store singleton inside
    utils/permissions: authorized = the set of user ids is_authorized
    answers True for."""

    def __init__(self, authorized=()):
        self.authorized = set(authorized)

    def is_authorized(self, user_id):
        return user_id in self.authorized


@pytest.fixture
def tier_store(monkeypatch):
    """Installs a _StubAccessStore into utils.permissions and returns it,
    so individual tests can put ids in/out of the 'paid' set."""

    import utils.permissions as perms

    stub = _StubAccessStore()
    monkeypatch.setattr(perms, "access_store", stub)
    return stub


# ======================================================================
# Classification
# ======================================================================


def test_admin_ids_win_over_everything(monkeypatch, tier_store):

    monkeypatch.setattr(Telegram, "ADMIN_IDS", [100])

    assert get_account_tier(100) is AccountTier.ADMIN


def test_authorized_user_is_paid(monkeypatch, tier_store):

    monkeypatch.setattr(Telegram, "ADMIN_IDS", [111, 222])
    tier_store.authorized.add(777)

    assert get_account_tier(777) is AccountTier.PAID


def test_unregistered_user_is_trial(monkeypatch, tier_store):

    monkeypatch.setattr(Telegram, "ADMIN_IDS", [111, 222])

    assert get_account_tier(777) is AccountTier.TRIAL


def test_expired_or_inactive_paid_user_falls_back_to_trial(monkeypatch):
    """is_authorized() already answers False for expired/inactive rows —
    the tier must follow that verdict, not just raw presence."""

    import utils.permissions as perms

    monkeypatch.setattr(Telegram, "ADMIN_IDS", [111, 222])
    monkeypatch.setattr(perms, "access_store", _StubAccessStore())

    assert get_account_tier(777) is AccountTier.TRIAL


def test_no_access_control_configured_means_everyone_is_trial(monkeypatch, tier_store):
    """Access control is opt-in (CLAUDE.md hard rule): with an empty
    ADMIN_IDS there is no paid concept — nobody accidentally becomes
    paid, and admins can't exist either."""

    monkeypatch.setattr(Telegram, "ADMIN_IDS", [])
    tier_store.authorized.add(777)

    assert get_account_tier(777) is AccountTier.TRIAL


# ======================================================================
# Worker-side payload resolution
# ======================================================================


def test_tier_round_trips_through_the_payload():

    for tier in AccountTier:
        assert get_tier_from_payload({"account_tier": tier.value}) is tier


def test_missing_or_unknown_tier_in_payload_falls_back_to_trial():
    """A missing/unknown tier must never loosen the limits — trial is
    the most restrictive default."""

    assert get_tier_from_payload({}) is AccountTier.TRIAL
    assert get_tier_from_payload({"account_tier": "guest"}) is AccountTier.TRIAL
    assert get_tier_from_payload(None) is AccountTier.TRIAL



# ======================================================================
# File-size cap per tier
# ======================================================================


def test_admin_and_paid_get_the_full_max_file_size(monkeypatch):

    monkeypatch.setattr(Telegram, "ADMIN_IDS", [111])
    monkeypatch.setattr(Processing, "MAX_FILE_SIZE", 2 * 1024**3)
    monkeypatch.setattr(Tiers, "TRIAL_MAX_FILE_SIZE", 500 * 1024**2)

    assert max_file_size_for_tier(AccountTier.ADMIN) == 2 * 1024**3
    assert max_file_size_for_tier(AccountTier.PAID) == 2 * 1024**3


def test_trial_size_cap_applies_when_configured(monkeypatch):

    monkeypatch.setattr(Processing, "MAX_FILE_SIZE", 2 * 1024**3)
    monkeypatch.setattr(Tiers, "TRIAL_MAX_FILE_SIZE", 500 * 1024**2)

    assert max_file_size_for_tier(AccountTier.TRIAL) == 500 * 1024**2


def test_trial_size_cap_never_exceeds_the_global_cap(monkeypatch):

    monkeypatch.setattr(Processing, "MAX_FILE_SIZE", 2 * 1024**3)
    monkeypatch.setattr(Tiers, "TRIAL_MAX_FILE_SIZE", 5 * 1024**3)

    assert max_file_size_for_tier(AccountTier.TRIAL) == 2 * 1024**3


def test_trial_size_cap_disabled_means_global_cap(monkeypatch):

    monkeypatch.setattr(Processing, "MAX_FILE_SIZE", 2 * 1024**3)
    monkeypatch.setattr(Tiers, "TRIAL_MAX_FILE_SIZE", 0)

    assert max_file_size_for_tier(AccountTier.TRIAL) == 2 * 1024**3


# ======================================================================
# Submission rate limit per tier
# ======================================================================


def test_admin_rate_limit_is_off(monkeypatch):

    monkeypatch.setattr(RateLimiting, "MAX_FILES", 3)
    monkeypatch.setattr(RateLimiting, "WINDOW_MINUTES", 10)

    assert submission_rate_limit_for_tier(AccountTier.ADMIN) == (0, 0)


def test_paid_rate_limit_disabled_by_default(monkeypatch):

    monkeypatch.setattr(Tiers, "PAID_RATE_LIMIT_MAX_FILES", 0)
    monkeypatch.setattr(Tiers, "PAID_RATE_LIMIT_WINDOW_MINUTES", 10)

    assert submission_rate_limit_for_tier(AccountTier.PAID) == (0, 0)


def test_paid_rate_limit_when_configured(monkeypatch):

    monkeypatch.setattr(Tiers, "PAID_RATE_LIMIT_MAX_FILES", 50)
    monkeypatch.setattr(Tiers, "PAID_RATE_LIMIT_WINDOW_MINUTES", 30)

    assert submission_rate_limit_for_tier(AccountTier.PAID) == (50, 30)


def test_trial_gets_the_trial_limits(monkeypatch):

    monkeypatch.setattr(RateLimiting, "MAX_FILES", 3)
    monkeypatch.setattr(RateLimiting, "WINDOW_MINUTES", 10)
    monkeypatch.setattr(Tiers, "PAID_RATE_LIMIT_MAX_FILES", 50)

    assert submission_rate_limit_for_tier(AccountTier.TRIAL) == (3, 10)


# ======================================================================
# check_tier_submission
# ======================================================================


def test_check_tier_submission_enforces_and_records(monkeypatch, tier_store):

    monkeypatch.setattr(Telegram, "ADMIN_IDS", [111, 222])
    monkeypatch.setattr(RateLimiting, "MAX_FILES", 2)
    monkeypatch.setattr(RateLimiting, "WINDOW_MINUTES", 10)

    history = []

    assert check_tier_submission(777, history, now=100.0) == (True, (2, 10))
    assert check_tier_submission(777, history, now=101.0) == (True, (2, 10))
    assert check_tier_submission(777, history, now=102.0) == (False, (2, 10))
    assert history == [100.0, 101.0]


def test_check_tier_submission_admin_is_never_limited(monkeypatch, tier_store):

    monkeypatch.setattr(Telegram, "ADMIN_IDS", [777])
    monkeypatch.setattr(RateLimiting, "MAX_FILES", 1)
    monkeypatch.setattr(RateLimiting, "WINDOW_MINUTES", 10)

    history = []

    for now in (100.0, 100.5, 101.0, 101.5):
        assert check_tier_submission(777, history, now=now) == (True, (0, 0))

    assert history == []
