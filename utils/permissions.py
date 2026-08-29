"""
Central account-tier classification and limit resolution.

This module is the single source of truth for "what is this user allowed
to do". The hierarchy is:

    Admin  (Telegram.ADMIN_IDS)      -> no application-level limits
    Paid   (active row in access DB) -> no trial submission limits
    Trial  (everyone else)           -> trial restrictions

Every per-user limit (submission rate, file-size cap, and later the
anti-spam/burst rules) should resolve through the helpers here instead
of checking is_admin()/is_authorized()/config values inline, so the
tier rules stay in one place and new tiers can be added without touching
the call sites.

The bot includes the resolved tier in every job payload ("account_tier"),
so the worker can enforce the same tier limits without opening the
access DB itself.
"""
from __future__ import annotations

from enum import Enum

from config import Processing, RateLimiting, Tiers, Telegram

from utils.rate_limit import is_rate_limited, record_submission

try:

    from services.access_store import access_store

except Exception:  # pragma: no cover - worker-side import safety

    access_store = None


class AccountTier(str, Enum):

    ADMIN = "admin"

    PAID = "paid"

    TRIAL = "trial"


def get_account_tier(user_id: int) -> AccountTier:

    if user_id in Telegram.ADMIN_IDS:
        return AccountTier.ADMIN

    # Paid = an active, non-expired row in the access DB. Only consulted
    # when access control is actually configured (non-empty ADMIN_IDS) —
    # otherwise there is no paid concept and everyone is trial-tier.
    if (
        Telegram.ADMIN_IDS
        and access_store is not None
        and access_store.is_authorized(user_id)
    ):
        return AccountTier.PAID

    return AccountTier.TRIAL


def get_tier_from_payload(payload: dict) -> AccountTier:
    """Worker-side tier resolution: the tier the bot computed at
    submission time, carried in the job payload. Falls back to trial
    (the most restrictive tier) when the field is missing or unknown —
    a missing tier must never loosen the limits."""

    raw = (payload or {}).get("account_tier", "")

    try:
        return AccountTier(raw)
    except ValueError:
        return AccountTier.TRIAL


def max_file_size_for_tier(tier: AccountTier) -> int:
    """The file-size cap (bytes) that applies to this tier. Admins are
    unrestricted; paid users get the full MAX_FILE_SIZE; trial users get
    the trial cap when one is configured (never more than the global
    MAX_FILE_SIZE)."""

    if tier == AccountTier.ADMIN:
        return Processing.MAX_FILE_SIZE

    if tier == AccountTier.PAID:
        return Processing.MAX_FILE_SIZE

    if Tiers.TRIAL_MAX_FILE_SIZE > 0:
        return min(Tiers.TRIAL_MAX_FILE_SIZE, Processing.MAX_FILE_SIZE)

    return Processing.MAX_FILE_SIZE


def max_file_size_for_user(user_id: int) -> int:
    return max_file_size_for_tier(get_account_tier(user_id))


def submission_rate_limit_for_tier(tier: AccountTier) -> tuple[int, int]:
    """(max_files, window_minutes) for the tier; max_files <= 0 means
    the rate limiter is off for this tier."""

    if tier == AccountTier.ADMIN:
        return (0, 0)

    if tier == AccountTier.PAID:
        if Tiers.PAID_RATE_LIMIT_MAX_FILES <= 0:
            return (0, 0)
        return (
            Tiers.PAID_RATE_LIMIT_MAX_FILES,
            Tiers.PAID_RATE_LIMIT_WINDOW_MINUTES,
        )

    return (RateLimiting.MAX_FILES, RateLimiting.WINDOW_MINUTES)


def submission_rate_limit_for_user(user_id: int) -> tuple[int, int]:
    return submission_rate_limit_for_tier(get_account_tier(user_id))


def check_tier_submission(
    user_id: int,
    history: list[float],
    now: float,
) -> tuple[bool, tuple[int, int]]:
    """Tier-aware submission gate. Returns (allowed, (max_files,
    window_minutes)) — the limits values are the ones that were applied,
    so callers can build accurate rejection messages. Records the
    submission when allowed."""

    max_files, window_minutes = submission_rate_limit_for_user(user_id)

    if is_rate_limited(
        history,
        now,
        max_files,
        window_minutes * 60,
    ):
        return False, (max_files, window_minutes)

    # Only record when the limiter is actually on: with max_files <= 0
    # is_rate_limited short-circuits before pruning, so recording would
    # make the history grow without bound (violating the
    # keep-the-dict-small rule in handlers/core.py).
    if max_files > 0:
        record_submission(history, now)

    return True, (max_files, window_minutes)