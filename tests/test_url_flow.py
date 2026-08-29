"""
End-to-end-ish tests for the URL-upload feature: the bot-side
submission handler (handlers/core.py::handle_url_submission) and the
worker-side job handler (worker.py::process_url_job). No real network,
no real Telegram — everything is stubbed at the same seams as the other
test files (validate_url, settings_store, telegram_service, downloader).
"""

import time

from types import SimpleNamespace

import pytest

from config import Paths, Processing, RateLimiting

from core.constants import MessageType

import handlers.core as core

from models.pending_file import PendingFile

from services.url_downloader import URLDownloadError

import state as shared_state

import worker as worker_module


DEFAULTS = {
    "quality": "360",
    "watermark": False,
    "upload_as": "video",
    "target_chat_id": 0,
    "target_label": "خودم",
    "artist": "",
    "logo_path": "",
    "logo_position": "bottom_right",
    "sort_mode": "name",
    "sort_order": "asc",
    "exclude_text": "",
}


# ======================================================================
# Bot side: handle_url_submission
# ======================================================================


def _fake_url_message(url: str, user_id: int = 555):

    answers = []

    async def answer(text, **kwargs):
        answers.append((text, kwargs))

    return (
        SimpleNamespace(
            from_user=SimpleNamespace(id=user_id),
            chat=SimpleNamespace(type="private", id=user_id),
            text=url,
            document=None,
            video=None,
            audio=None,
            photo=None,
            answer=answer,
        ),
        answers,
    )


@pytest.fixture
def clean_url_state():

    shared_state.pending_files.clear()
    shared_state.user_submission_times.pop(555, None)
    yield
    shared_state.pending_files.clear()
    shared_state.user_submission_times.pop(555, None)


async def test_private_url_is_rejected_with_security_message(
    monkeypatch, clean_url_state
):

    monkeypatch.setattr(core, "validate_url", lambda url: (False, "private_address"))

    message, answers = _fake_url_message("http://169.254.169.254/latest")

    await core.handle_url_submission(message, "http://169.254.169.254/latest")

    assert "امنیت" in answers[0][0]
    assert shared_state.pending_files == {}


async def test_valid_url_creates_pending_entry_like_an_upload(
    monkeypatch, clean_url_state
):

    monkeypatch.setattr(core, "validate_url", lambda url: (True, "ok"))
    monkeypatch.setattr(core.settings_store, "get", lambda user_id: dict(DEFAULTS))

    url = "https://example.com/videos/lesson1.mp4"

    message, answers = _fake_url_message(url)

    await core.handle_url_submission(message, url)

    assert len(shared_state.pending_files) == 1

    pending = next(iter(shared_state.pending_files.values()))

    assert isinstance(pending, PendingFile)
    assert pending.url == url
    assert pending.file_name == "lesson1.mp4"
    assert pending.file_type == "VIDEO"  # same type detection as uploads
    assert pending.options["quality"] == DEFAULTS["quality"]
    assert pending.user_id == 555

    # The mode-choice screen (direct upload vs. normal processing) comes
    # first now — the quality picker only appears after "process" is
    # chosen (see tests/test_url_mode.py).
    assert "ارسال مستقیم" in answers[0][0]
    assert "پردازش کامل" in answers[0][0]


async def test_unknown_file_type_url_is_rejected(monkeypatch, clean_url_state):

    monkeypatch.setattr(core, "validate_url", lambda url: (True, "ok"))

    message, answers = _fake_url_message("https://example.com/thing.exe")

    await core.handle_url_submission(message, "https://example.com/thing.exe")

    assert "تشخیص" in answers[0][0]
    assert shared_state.pending_files == {}


async def test_url_submissions_respect_the_rate_limit(monkeypatch, clean_url_state):

    monkeypatch.setattr(core, "validate_url", lambda url: (True, "ok"))
    monkeypatch.setattr(RateLimiting, "MAX_FILES", 1)
    monkeypatch.setattr(RateLimiting, "WINDOW_MINUTES", 10)

    # The submission gate is tier-aware now — make sure user 555
    # classifies as trial (the limited tier) no matter what earlier
    # tests left in the shared access DB.
    from services.access_store import access_store

    access_store._conn.execute("DELETE FROM authorized_users")
    access_store._conn.commit()

    # Fill the user's window: next submission must be rejected.
    shared_state.user_submission_times[555] = [time.time()]

    message, answers = _fake_url_message("https://example.com/lesson1.mp4")

    await core.handle_url_submission(message, "https://example.com/lesson1.mp4")

    assert "حد مجاز" in answers[0][0]
    assert shared_state.pending_files == {}


# ======================================================================
# Worker side: process_url_job
# ======================================================================


def _worker_stubs(monkeypatch, tmp_path):

    sent = {"error": [], "result": []}

    async def fake_send_error(payload):
        sent["error"].append(payload)

    async def fake_send_result(payload):
        sent["result"].append(payload)

    async def fake_send_info(payload):
        pass

    async def fake_upload_entry(job, entry):
        entry.uploaded = True
        return True

    monkeypatch.setattr(worker_module.telegram_service, "send_error", fake_send_error)
    monkeypatch.setattr(worker_module.telegram_service, "send_result", fake_send_result)
    monkeypatch.setattr(worker_module.telegram_service, "send_info", fake_send_info)
    monkeypatch.setattr(worker_module, "upload_entry", fake_upload_entry)
    monkeypatch.setattr(worker_module, "_processing_semaphore", None)
    monkeypatch.setattr(Paths, "DOWNLOADS", tmp_path)

    return sent


async def test_process_url_job_happy_path(monkeypatch, tmp_path):

    sent = _worker_stubs(monkeypatch, tmp_path)

    downloaded = []

    async def fake_download_to_disk(url, destination, max_size, **kwargs):
        downloaded.append((url, max_size))
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"fake video bytes")
        return destination

    monkeypatch.setattr(worker_module, "download_to_disk", fake_download_to_disk)

    dispatched = []

    async def fake_dispatch(job):
        dispatched.append(job)
        job.add_output(job.input_file)
        return True

    monkeypatch.setattr(worker_module.dispatcher, "dispatch", fake_dispatch)

    url = "https://example.com/videos/lesson1.mp4"

    await worker_module.process_url_job(
        {"user_id": 555, "url": url, "options": {}},
        url,
    )

    assert downloaded == [(url, Processing.MAX_FILE_SIZE)]
    assert len(dispatched) == 1

    job = dispatched[0]

    assert job.file_type == "VIDEO"
    assert job.original_name == "lesson1.mp4"
    assert job.file_size == len(b"fake video bytes")
    assert len(sent["result"]) == 1  # delivered through the shared pipeline


async def test_process_url_job_too_large_gets_persian_error(monkeypatch, tmp_path):

    sent = _worker_stubs(monkeypatch, tmp_path)

    async def fake_download_to_disk(url, destination, max_size, **kwargs):
        raise URLDownloadError("too_large", str(Processing.MAX_FILE_SIZE + 5))

    monkeypatch.setattr(worker_module, "download_to_disk", fake_download_to_disk)

    await worker_module.process_url_job(
        {"user_id": 555, "url": "https://example.com/huge.mp4", "options": {}},
        "https://example.com/huge.mp4",
    )

    # Two error sends now: the structured admin report (ADMIN_ERROR) and
    # the user-facing Persian message (ERROR).
    assert len(sent["error"]) == 2

    admin_report, user_error = sent["error"]

    assert admin_report["type"] == MessageType.ADMIN_ERROR.value
    assert admin_report["user_id"] == 555
    assert "DOWNLOAD" in admin_report["report"]
    assert "DOWNLOAD_FAILED" in admin_report["report"]

    assert user_error["type"] == MessageType.ERROR.value
    assert "حد مجاز" in user_error["message"]


async def test_process_url_job_network_failure_gets_persian_error(
    monkeypatch, tmp_path
):

    sent = _worker_stubs(monkeypatch, tmp_path)

    async def fake_download_to_disk(url, destination, max_size, **kwargs):
        raise URLDownloadError("network", "HTTP 404")

    monkeypatch.setattr(worker_module, "download_to_disk", fake_download_to_disk)

    await worker_module.process_url_job(
        {"user_id": 555, "url": "https://example.com/gone.mp4", "options": {}},
        "https://example.com/gone.mp4",
    )

    assert len(sent["error"]) == 2

    admin_report, user_error = sent["error"]

    assert admin_report["type"] == MessageType.ADMIN_ERROR.value
    assert "DOWNLOAD" in admin_report["report"]

    assert user_error["type"] == MessageType.ERROR.value
    assert "دانلود فایل از لینک ناموفق بود" in user_error["message"]


async def test_process_url_job_timeout_gets_persian_error(monkeypatch, tmp_path):

    sent = _worker_stubs(monkeypatch, tmp_path)

    async def fake_download_to_disk(url, destination, max_size, **kwargs):
        raise URLDownloadError("timeout", "timed out")

    monkeypatch.setattr(worker_module, "download_to_disk", fake_download_to_disk)

    await worker_module.process_url_job(
        {"user_id": 555, "url": "https://example.com/slow.mp4", "options": {}},
        "https://example.com/slow.mp4",
    )

    assert len(sent["error"]) == 2
    assert sent["error"][0]["type"] == MessageType.ADMIN_ERROR.value
    assert "طول کشید" in sent["error"][1]["message"]


async def test_process_url_job_never_touches_bridge_messages(monkeypatch, tmp_path):

    # A URL job has no bridge media message — get_messages must never be
    # called for it.
    _worker_stubs(monkeypatch, tmp_path)

    async def fake_get_messages(*args, **kwargs):
        raise AssertionError("get_messages must not be called for URL jobs")

    monkeypatch.setattr(
        worker_module.telegram_service.client,
        "get_messages",
        fake_get_messages,
    )

    async def fake_download_to_disk(url, destination, max_size, **kwargs):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"x")
        return destination

    monkeypatch.setattr(worker_module, "download_to_disk", fake_download_to_disk)

    async def fake_dispatch(job):
        return True  # no output -> goes down the plain error path, fine

    monkeypatch.setattr(worker_module.dispatcher, "dispatch", fake_dispatch)

    await worker_module.process_url_job(
        {"user_id": 555, "url": "https://example.com/file.mp4", "options": {}},
        "https://example.com/file.mp4",
    )