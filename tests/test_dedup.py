"""Tests for utils/dedup.py: duplicate-submission detection."""

from types import SimpleNamespace

from utils.dedup import (
    dedup_key_for_submission,
    is_duplicate_pending,
    is_duplicate_submission,
    prune_stale,
    record_submission_key,
)


# ======================================================================
# dedup_key_for_submission
# ======================================================================


def test_url_key_is_normalized_lowercase_and_trimmed():

    assert dedup_key_for_submission(url="  HTTPS://Example.com/File.ZIP  ") == (
        "url:https://example.com/file.zip"
    )


def test_url_takes_priority_over_message_when_both_given():

    message = SimpleNamespace(document=None, video=None, audio=None)

    assert dedup_key_for_submission(message=message, url="https://x.com/f") == "url:https://x.com/f"


def test_file_key_uses_file_unique_id():

    message = SimpleNamespace(
        document=SimpleNamespace(file_unique_id="abc123"),
        video=None,
        audio=None,
    )

    assert dedup_key_for_submission(message=message) == "file:abc123"


def test_file_key_checks_video_and_audio_too():

    video_message = SimpleNamespace(
        document=None,
        video=SimpleNamespace(file_unique_id="vid1"),
        audio=None,
    )
    audio_message = SimpleNamespace(
        document=None,
        video=None,
        audio=SimpleNamespace(file_unique_id="aud1"),
    )

    assert dedup_key_for_submission(message=video_message) == "file:vid1"
    assert dedup_key_for_submission(message=audio_message) == "file:aud1"


def test_no_url_and_no_file_returns_empty_key():

    message = SimpleNamespace(document=None, video=None, audio=None)

    assert dedup_key_for_submission(message=message) == ""
    assert dedup_key_for_submission() == ""


def test_missing_file_unique_id_returns_empty_key():

    message = SimpleNamespace(
        document=SimpleNamespace(file_unique_id=None),
        video=None,
        audio=None,
    )

    assert dedup_key_for_submission(message=message) == ""


# ======================================================================
# is_duplicate_pending
# ======================================================================


def _pending(user_id, url="", file_unique_id=None):

    if file_unique_id:
        source_message = SimpleNamespace(
            document=SimpleNamespace(file_unique_id=file_unique_id),
            video=None,
            audio=None,
        )
    else:
        source_message = SimpleNamespace(document=None, video=None, audio=None)

    return SimpleNamespace(user_id=user_id, url=url, source_message=source_message)


def test_is_duplicate_pending_matches_same_user_same_key():

    pending_files = {"pid1": _pending(user_id=1, url="https://x.com/f")}

    assert is_duplicate_pending(pending_files, 1, "url:https://x.com/f") is True


def test_is_duplicate_pending_ignores_a_different_user():

    pending_files = {"pid1": _pending(user_id=1, url="https://x.com/f")}

    assert is_duplicate_pending(pending_files, 2, "url:https://x.com/f") is False


def test_is_duplicate_pending_ignores_a_different_key():

    pending_files = {"pid1": _pending(user_id=1, url="https://x.com/f")}

    assert is_duplicate_pending(pending_files, 1, "url:https://x.com/other") is False


def test_is_duplicate_pending_with_empty_key_is_always_false():

    pending_files = {"pid1": _pending(user_id=1, url="https://x.com/f")}

    assert is_duplicate_pending(pending_files, 1, "") is False


def test_is_duplicate_pending_with_no_entries():

    assert is_duplicate_pending({}, 1, "url:https://x.com/f") is False


def test_is_duplicate_pending_matches_a_file_upload_too():

    pending_files = {"pid1": _pending(user_id=1, file_unique_id="abc123")}

    assert is_duplicate_pending(pending_files, 1, "file:abc123") is True


# ======================================================================
# is_duplicate_submission / record_submission_key / prune_stale
# ======================================================================


def test_freshly_recorded_key_is_a_duplicate_within_the_window():

    history: dict[str, float] = {}
    record_submission_key(history, "file:abc", now=1000.0)

    assert is_duplicate_submission(history, "file:abc", now=1005.0, window_seconds=60) is True


def test_key_outside_the_window_is_not_a_duplicate_and_gets_pruned():

    history: dict[str, float] = {"file:abc": 1000.0}

    assert is_duplicate_submission(history, "file:abc", now=2000.0, window_seconds=60) is False
    assert "file:abc" not in history  # pruned as a side effect


def test_different_key_is_never_a_duplicate():

    history: dict[str, float] = {"file:abc": 1000.0}

    assert is_duplicate_submission(history, "file:xyz", now=1001.0, window_seconds=60) is False


def test_zero_or_negative_window_disables_the_check():

    history: dict[str, float] = {"file:abc": 1000.0}

    assert is_duplicate_submission(history, "file:abc", now=1000.1, window_seconds=0) is False
    assert is_duplicate_submission(history, "file:abc", now=1000.1, window_seconds=-5) is False


def test_empty_key_is_never_a_duplicate():

    history: dict[str, float] = {}

    assert is_duplicate_submission(history, "", now=1000.0, window_seconds=60) is False


def test_prune_stale_removes_only_expired_entries():

    history = {"old": 1000.0, "fresh": 1990.0}

    prune_stale(history, now=2000.0, window_seconds=60)

    assert history == {"fresh": 1990.0}


def test_prune_stale_on_empty_history_is_a_no_op():

    history: dict[str, float] = {}

    prune_stale(history, now=2000.0, window_seconds=60)

    assert history == {}
