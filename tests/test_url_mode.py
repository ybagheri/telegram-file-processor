"""
Tests for the URL mode choice: when a user sends a file URL they can
either have it delivered as-is ("direct upload", no processing) or run
it through the normal per-file processing flow. Covers the choice
keyboard, all three callback branches (direct/process/cancel), the
finalize_job payload flag, and the worker's direct-upload delivery path.
Same stubbing seams as test_url_flow.py — no real network/Telegram.
"""

from types import SimpleNamespace

import pytest

import handlers.core as core

import handlers.files as files_module

from keyboards.files import url_mode_keyboard

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


@pytest.fixture
def clean_url_state():

    shared_state.pending_files.clear()
    shared_state.user_submission_times.pop(555, None)
    shared_state.awaiting_state.clear()
    yield
    shared_state.pending_files.clear()
    shared_state.user_submission_times.pop(555, None)
    shared_state.awaiting_state.clear()


async def test_url_submission_offers_the_mode_choice(monkeypatch, clean_url_state):

    monkeypatch.setattr(core, "validate_url", lambda url: (True, "ok"))
    monkeypatch.setattr(core.settings_store, "get", lambda user_id: dict(DEFAULTS))

    message, answers = _fake_url_message("https://example.com/videos/lesson1.mp4")

    await core.handle_url_submission(message, "https://example.com/videos/lesson1.mp4")

    text, kwargs = answers[0]

    # Both behaviors must be clearly explained, not just offered as two
    # anonymous buttons.
    assert "ارسال مستقیم" in text
    assert "پردازش کامل" in text
    assert "بدون تبدیل" in text
    assert "واترمارک" in text

    markup = kwargs["reply_markup"]
    data = [b.callback_data for row in markup.inline_keyboard for b in row]

    assert len(shared_state.pending_files) == 1
    pid = next(iter(shared_state.pending_files))

    assert f"urlmode:{pid}:direct" in data
    assert f"urlmode:{pid}:process" in data
    assert f"urlmode:{pid}:cancel" in data


def test_url_mode_keyboard_shape():

    markup = url_mode_keyboard("abc123")

    data = [b.callback_data for row in markup.inline_keyboard for b in row]

    assert data == [
        "urlmode:abc123:direct",
        "urlmode:abc123:process",
        "urlmode:abc123:cancel",
    ]


async def test_direct_choice_finalizes_with_direct_upload_flag(
    monkeypatch, clean_url_state
):

    sent_jobs = []

    async def fake_send_job(job_data):
        sent_jobs.append(job_data)

    monkeypatch.setattr(files_module.telegram_service, "send_job", fake_send_job)

    pid = _register_pending(file_type="VIDEO")

    edits = []

    async def edit_text(text, **kwargs):
        edits.append((text, kwargs))

    callback = _fake_callback(f"urlmode:{pid}:direct", edits)

    await files_module.url_mode_pick(callback)

    assert len(sent_jobs) == 1


async def test_process_choice_for_video_shows_quality_keyboard(clean_url_state):

    pid = _register_pending(file_type="VIDEO")

    edits = []

    async def edit_text(text, **kwargs):
        edits.append((text, kwargs))

    callback = _fake_callback(f"urlmode:{pid}:process", edits)

    await files_module.url_mode_pick(callback)

    assert "کیفیت" in edits[0][0]

    markup = edits[0][1]["reply_markup"]
    data = [b.callback_data for row in markup.inline_keyboard for b in row]

    assert f"q:{pid}:360" in data  # quality row for the normal flow

    # Still pending — the user hasn't confirmed anything yet.
    assert pid in shared_state.pending_files


async def test_process_choice_for_non_video_shows_options_keyboard(clean_url_state):

    pid = _register_pending(file_type="ARCHIVE")

    edits = []

    async def edit_text(text, **kwargs):
        edits.append((text, kwargs))

    callback = _fake_callback(f"urlmode:{pid}:process", edits)

    await files_module.url_mode_pick(callback)

    assert "تنظیمات این فایل" in edits[0][0]


async def test_cancel_choice_removes_the_pending_url(clean_url_state):

    pid = _register_pending(file_type="VIDEO")

    edits = []

    async def edit_text(text, **kwargs):
        edits.append((text, kwargs))

    callback = _fake_callback(f"urlmode:{pid}:cancel", edits)

    await files_module.url_mode_pick(callback)

    assert pid not in shared_state.pending_files
    assert "لغو" in edits[0][0]


# ======================================================================
# Worker side: direct-upload delivery path
# ======================================================================


def _worker_stubs(monkeypatch, tmp_path):

    calls = {"dispatched": [], "uploads": []}

    async def fake_dispatch(job):
        calls["dispatched"].append(job)
        return True

    async def fake_upload_entry(job, entry):
        calls["uploads"].append((job, entry))
        entry.uploaded = True

        # Capture the delivered bytes now — the job's working directories
        # are cleaned up by the time the test body sees the entry.
        if entry.path.exists():
            calls["uploaded_bytes"] = entry.path.read_bytes()

    async def fake_send_payload(payload):
        pass

    monkeypatch.setattr(worker_module.dispatcher, "dispatch", fake_dispatch)
    monkeypatch.setattr(worker_module, "upload_entry", fake_upload_entry)
    monkeypatch.setattr(worker_module.telegram_service, "send_info", fake_send_payload)
    monkeypatch.setattr(worker_module.telegram_service, "send_error", fake_send_payload)
    monkeypatch.setattr(worker_module, "_processing_semaphore", None)
    monkeypatch.setattr(worker_module.Paths, "DOWNLOADS", tmp_path)

    return calls


async def test_direct_upload_skips_processing_and_delivers_untouched(
    monkeypatch, tmp_path
):

    calls = _worker_stubs(monkeypatch, tmp_path)

    async def fake_download_to_disk(url, destination, max_size):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"raw file bytes")
        return destination

    monkeypatch.setattr(worker_module, "download_to_disk", fake_download_to_disk)

    url = "https://example.com/videos/lesson1.mp4"

    await worker_module.process_url_job(
        {
            "user_id": 555,
            "url": url,
            "file_type": "VIDEO",
            "file_name": "lesson1.mp4",
            "direct_upload": True,
            "options": {},
        },
        url,
    )

    # The processing pipeline must never be touched in direct mode.
    assert calls["dispatched"] == []
    assert len(calls["uploads"]) == 1

    job, entry = calls["uploads"][0]

    assert entry.kind == "document"
    assert entry.path.name == "lesson1.mp4"
    assert calls["uploaded_bytes"] == b"raw file bytes"
    assert job.has_output


async def test_normal_processing_url_still_goes_through_dispatch(
    monkeypatch, tmp_path
):

    calls = _worker_stubs(monkeypatch, tmp_path)

    async def fake_download_to_disk(url, destination, max_size):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"raw file bytes")
        return destination

    monkeypatch.setattr(worker_module, "download_to_disk", fake_download_to_disk)

    url = "https://example.com/videos/lesson1.mp4"

    await worker_module.process_url_job(
        {
            "user_id": 555,
            "url": url,
            "file_type": "VIDEO",
            "file_name": "lesson1.mp4",
            "options": {},
        },
        url,
    )

    assert len(calls["dispatched"]) == 1
    assert calls["uploads"] == []  # dispatch stub produced no output


# ======================================================================
# Helpers
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


def _register_pending(file_type: str, user_id: int = 555) -> str:

    pid = "testpid99"

    shared_state.pending_files[pid] = SimpleNamespace(
        user_id=user_id,
        chat_id=user_id,
        file_name="lesson1.mp4",
        file_type=file_type,
        source_message=object(),
        url="https://example.com/videos/lesson1.mp4",
        direct_upload=False,
        options=dict(DEFAULTS),
        is_multipart=False,
        parts_total=0,
        part_message_ids=[],
    )

    return pid


def _fake_callback(data: str, edits: list):

    async def edit_text(text, **kwargs):
        edits.append((text, kwargs))

    async def answer(*args, **kwargs):
        pass

    return SimpleNamespace(
        data=data,
        message=SimpleNamespace(edit_text=edit_text),
        answer=answer,
    )


    job_data = sent_jobs[0]

    assert job_data["direct_upload"] is True
    assert job_data["url"] == "https://example.com/videos/lesson1.mp4"

    # Skipped straight past the options flow.
    assert pid not in shared_state.pending_files
    assert "ارسال شد" in edits[0][0]
