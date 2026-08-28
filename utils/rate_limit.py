"""
Per-user file-submission rate limiting (pure logic — no Telegram, no
imports from bot-side modules, so it's trivially testable).

The bot keeps, per user_id, a list of recent submission timestamps (the
`user_submission_times` dict in state.py — mutate in place, never
reassign). The limiter is deliberately simple: count how many submissions
fall inside a sliding window and reject when the user is already at the
cap. It sits *alongside* `is_authorized(...)` — authorized users get
throttled, unauthorized ones are still rejected by the access check first.
"""
from __future__ import annotations


def prune_timestamps(
    history: list[float],
    now: float,
    window_seconds: float,
) -> None:
    """Drops timestamps older than the window, mutating the list in
    place (per state.py's rules — callers keep the same list object)."""

    cutoff = now - window_seconds

    history[:] = [ts for ts in history if ts > cutoff]


def is_rate_limited(
    history: list[float],
    now: float,
    max_files: int,
    window_seconds: float,
) -> bool:
    """True when the user has already submitted max_files files within
    the window (i.e. a *new* submission would exceed it). A max_files
    <= 0 disables the limiter entirely — consistent with the project's
    "empty config means feature off" defaults."""

    if max_files <= 0:
        return False

    prune_timestamps(history, now, window_seconds)

    return len(history) >= max_files


def record_submission(
    history: list[float],
    now: float,
) -> None:

    history.append(now)
