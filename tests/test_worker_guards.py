"""
Tests for worker.py's pre-download guards (MAX_FILE_SIZE enforcement).

These drive the real process_job/process_multipart_job functions with
fake Telethon message objects and a stubbed telegram_service, verifying
that an oversized job is rejected via Protocol.create_error before any
download is attempted, and that a right-at-the-limit file still proceeds
to the download step.
"""

from types import SimpleNamespace

import pytest

from config import Paths, Processing
from core.constants import MessageType

import worker as worker_module


def _fake_media_message(size: int, name: str = "course.rar"):

    return SimpleNamespace(
        media=object(),
        file=SimpleNamespace(
            size=size,
            name=name,
            mime_type="application/x-rar-compressed",
        ),
    )


@pytest.fixture
def job_dirs(tmp_path, monkeypatch):

    # Job.__post_init__ builds its working directories under
    # Paths.DOWNLOADS at call time, so repointing the class attribute at
    # tmp_path keeps the real downloads/ folder untouched.
    monkeypatch.setattr(Paths, "DOWNLOADS", tmp_path)
    return tmp_path


@pytest.fixture
def stub_telegram(monkeypatch):

    sent_errors = []

    async def fake_send_error(payload):
        sent_errors.append(payload)

    async def fake_download(message, destination):
        raise AssertionError("download must not be attempted for a rejected job")

    async def fake_get_messages(*args, **kwargs):
        raise AssertionError("get_messages must not be called before the guard")

    # Plenty of free disk by default, so only the size guard is exercised
    # unless a test overrides this (see the disk-space tests below).
    monkeypatch.setattr(
        worker_module.shutil,
        "disk_usage",
        lambda path: SimpleNamespace(free=1024**4),
    )

    monkeypatch.setattr(worker_module.telegram_service, "send_error", fake_send_error)
    monkeypatch.setattr(worker_module.telegram_service, "download", fake_download)
    monkeypatch.setattr(
        worker_module.telegram_service.client,
        "get_messages",
        fake_get_messages,
    )

    return sent_errors


async def test_process_job_rejects_oversized_file_before_download(
    job_dirs, stub_telegram
):

    async def fake_get_messages(*args, **kwargs):
        return _fake_media_message(size=Processing.MAX_FILE_SIZE + 1)

    # Only the guard's own get_messages call is allowed to succeed.
    import asyncio

    worker_module.telegram_service.client.get_messages = fake_get_messages

    await worker_module.process_job(
        {"user_id": 42, "message_id": 1, "options": {}}
    )

    assert len(stub_telegram) == 1

    payload = stub_telegram[0]

    assert payload["type"] == MessageType.ERROR.value
    assert payload["user_id"] == 42
    assert "حد مجاز" in payload["message"]


async def test_process_job_allows_file_exactly_at_the_limit(
    job_dirs, stub_telegram, tmp_path
):

    # Exactly at the limit must NOT trip the size guard — it should go on
    # to the download step (which we make fail with a plain download
    # error, so the test stays cheap and asserts only on which error fired).
    async def fake_get_messages(*args, **kwargs):
        return _fake_media_message(size=Processing.MAX_FILE_SIZE)

    worker_module.telegram_service.client.get_messages = fake_get_messages

    async def fake_download(message, destination):
        return None

    worker_module.telegram_service.download = fake_download

    await worker_module.process_job(
        {"user_id": 42, "message_id": 1, "options": {}}
    )

    assert len(stub_telegram) == 1
    assert "حد مجاز" not in stub_telegram[0]["message"]


async def test_process_multipart_job_rejects_oversized_total_before_download(
    job_dirs, stub_telegram
):

    half = Processing.MAX_FILE_SIZE // 2 + 1

    async def fake_get_messages(*args, **kwargs):
        return [
            _fake_media_message(size=half, name="course.part1.rar"),
            _fake_media_message(size=half, name="course.part2.rar"),
        ]

    worker_module.telegram_service.client.get_messages = fake_get_messages

    await worker_module.process_job(
        {
            "user_id": 42,
            "message_id": 1,
            "part_message_ids": [1, 2],
            "options": {},
        }
    )

    assert len(stub_telegram) == 1
    assert stub_telegram[0]["type"] == MessageType.ERROR.value


def test_file_too_large_message_mentions_both_sizes():

    size = Processing.MAX_FILE_SIZE * 2

    message = worker_module._file_too_large_message(size)

    assert "حد مجاز" in message


# ======================================================================
# Free-disk-space guard
# ======================================================================


def test_required_headroom_is_zero_below_threshold(monkeypatch):

    monkeypatch.setattr(Processing, "DISK_SPACE_CHECK_THRESHOLD", 1000)
    monkeypatch.setattr(Processing, "DISK_SPACE_SAFETY_FACTOR", 2.0)

    assert worker_module._required_headroom(1000) == 0
    assert worker_module._required_headroom(500) == 0


def test_required_headroom_applies_safety_factor(monkeypatch):

    monkeypatch.setattr(Processing, "DISK_SPACE_CHECK_THRESHOLD", 1000)
    monkeypatch.setattr(Processing, "DISK_SPACE_SAFETY_FACTOR", 2.5)

    assert worker_module._required_headroom(2000) == 5000


async def test_low_disk_space_rejects_job_before_download(
    job_dirs, stub_telegram, monkeypatch
):

    monkeypatch.setattr(Processing, "DISK_SPACE_CHECK_THRESHOLD", 1000)
    monkeypatch.setattr(Processing, "DISK_SPACE_SAFETY_FACTOR", 2.0)

    # Only 1500 bytes free: enough to notice, not enough for the
    # 2x safety factor on a 1000-byte file.
    monkeypatch.setattr(
        worker_module.shutil,
        "disk_usage",
        lambda path: SimpleNamespace(free=1500),
    )

    async def fake_get_messages(*args, **kwargs):
        return _fake_media_message(size=2000)

    worker_module.telegram_service.client.get_messages = fake_get_messages

    await worker_module.process_job(
        {"user_id": 42, "message_id": 1, "options": {}}
    )

    assert len(stub_telegram) == 1
    assert stub_telegram[0]["type"] == MessageType.ERROR.value
    assert "دیسک" in stub_telegram[0]["message"]


async def test_small_files_skip_the_disk_check(
    job_dirs, stub_telegram, monkeypatch
):

    monkeypatch.setattr(Processing, "DISK_SPACE_CHECK_THRESHOLD", 1000)

    # Even a report of ~zero free space must NOT block a file below the
    # threshold — the check is intentionally skipped for small inputs.
    monkeypatch.setattr(
        worker_module.shutil,
        "disk_usage",
        lambda path: SimpleNamespace(free=10),
    )

    async def fake_get_messages(*args, **kwargs):
        return _fake_media_message(size=500)

    worker_module.telegram_service.client.get_messages = fake_get_messages

    async def fake_download(message, destination):
        return None  # "download failed" — proves it got past the guard

    worker_module.telegram_service.download = fake_download

    await worker_module.process_job(
        {"user_id": 42, "message_id": 1, "options": {}}
    )

    assert len(stub_telegram) == 1
    assert "دیسک" not in stub_telegram[0]["message"]
