"""
Duplicate-submission detection (pure logic — no Telegram imports, same
spirit as utils/rate_limit.py).

Catches the same file/URL being submitted again by the same user while an
earlier submission of it is still pending confirmation or was recently
confirmed — the two most common real-world cases being an accidental
double-tap/resend from the Telegram client, and someone deliberately
flooding the bot with copies of one file. It intentionally does NOT try
to track a submission all the way through worker.py's processing (a
separate process) — see CLAUDE.md's change log for that scope decision.
"""
from __future__ import annotations


def prune_stale(history: dict[str, float], now: float, window_seconds: float) -> None:
    """Drops dedup keys older than the window, mutating in place (same
    rule as state.py's other dicts: callers keep the same dict object)."""

    cutoff = now - window_seconds

    stale = [key for key, ts in history.items() if ts <= cutoff]

    for key in stale:
        del history[key]


def is_duplicate_submission(
    history: dict[str, float],
    key: str,
    now: float,
    window_seconds: float,
) -> bool:
    """True when `key` (a file_unique_id or normalized URL) was recorded
    within the last window_seconds. window_seconds <= 0 disables the
    check entirely."""

    if window_seconds <= 0:
        return False

    prune_stale(history, now, window_seconds)

    return key in history


def record_submission_key(history: dict[str, float], key: str, now: float) -> None:
    history[key] = now


def dedup_key_for_submission(message=None, url: str = "") -> str:
    """A stable key identifying "this exact file/link" for duplicate
    detection — a normalized URL for link submissions, or Telegram's own
    file_unique_id (stable across re-uploads of the identical file) for
    uploads. Returns "" when neither is available, which callers should
    treat as "can't dedup this one, let it through" rather than a match."""

    if url:
        return f"url:{url.strip().lower()}"

    if message is not None:
        file = message.document or message.video or message.audio

        file_unique_id = getattr(file, "file_unique_id", None) if file else None

        if file_unique_id:
            return f"file:{file_unique_id}"

    return ""


def is_duplicate_pending(pending_files: dict, user_id: int, key: str) -> bool:
    """True if this user already has a *different*, still-unconfirmed
    pending_files entry for the same dedup key — the "double-tapped
    send" / "resent while still on the options screen" case. Takes
    pending_files as a parameter (rather than importing state.py)
    specifically so this stays a plain, dependency-free function callers
    can pass their own dict into — including in tests."""

    if not key:
        return False

    for pending in pending_files.values():

        if pending.user_id != user_id:
            continue

        if dedup_key_for_submission(pending.source_message, pending.url) == key:
            return True

    return False
