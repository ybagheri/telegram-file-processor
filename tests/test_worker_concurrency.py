"""
Tests for worker.py's per-job concurrency limit (asyncio.Semaphore around
the processing step), plus regression coverage that a failing job releases
its slot and that jobs still start processing in arrival order.
"""

import asyncio

from types import SimpleNamespace

import pytest

from config import Paths, Processing

import worker as worker_module


def _fake_media_message(size: int = 1000, name: str = "video.mp4"):

    return SimpleNamespace(
        media=object(),
        file=SimpleNamespace(
            size=size,
            name=name,
            mime_type="video/mp4",
        ),
    )


@pytest.fixture
def job_dirs(tmp_path, monkeypatch):

    monkeypatch.setattr(Paths, "DOWNLOADS", tmp_path)
    return tmp_path


@pytest.fixture
def stub_telegram_happy(monkeypatch):

    sent = {"error": [], "result": []}

    async def fake_send_error(payload):
        sent["error"].append(payload)

    async def fake_send_result(payload):
        sent["result"].append(payload)

    async def fake_send_info(payload):
        pass

    async def fake_get_messages(*args, **kwargs):
        return _fake_media_message()

    async def fake_download(message, destination):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"x")
        return destination

    monkeypatch.setattr(worker_module.telegram_service, "send_error", fake_send_error)
    monkeypatch.setattr(worker_module.telegram_service, "send_result", fake_send_result)
    monkeypatch.setattr(worker_module.telegram_service, "send_info", fake_send_info)
    monkeypatch.setattr(worker_module.telegram_service, "download", fake_download)
    monkeypatch.setattr(
        worker_module.telegram_service.client,
        "get_messages",
        fake_get_messages,
    )

    return sent


@pytest.fixture(autouse=True)
def fresh_semaphore(monkeypatch):

    # Every test builds its own semaphore from the MAX_CONCURRENT_JOBS it
    # monkeypatches into Processing.
    monkeypatch.setattr(worker_module, "_processing_semaphore", None)
    yield
    worker_module._processing_semaphore = None


def _job_payload(user_id: int):

    return {"user_id": user_id, "message_id": 1, "options": {}}


async def test_semaphore_bounds_concurrent_dispatch(
    job_dirs, stub_telegram_happy, monkeypatch
):

    monkeypatch.setattr(Processing, "MAX_CONCURRENT_JOBS", 2)

    tracker = {"current": 0, "max": 0}

    async def fake_dispatch(job):
        tracker["current"] += 1
        tracker["max"] = max(tracker["max"], tracker["current"])
        await asyncio.sleep(0.05)
        tracker["current"] -= 1
        return True

    monkeypatch.setattr(worker_module.dispatcher, "dispatch", fake_dispatch)

    await asyncio.gather(
        *[
            worker_module.process_job(_job_payload(user_id))
            for user_id in range(1, 6)
        ]
    )

    assert tracker["max"] == 2


async def test_failing_job_releases_its_semaphore_slot(
    job_dirs, stub_telegram_happy, monkeypatch
):

    monkeypatch.setattr(Processing, "MAX_CONCURRENT_JOBS", 1)

    calls = {"n": 0}

    async def fake_dispatch(job):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("ffmpeg exploded")
        await asyncio.sleep(0.01)
        # Register the (faked) downloaded input as an output so the
        # success path's result-summary ping fires like it would live.
        job.add_output(job.input_file)
        return True

    monkeypatch.setattr(worker_module.dispatcher, "dispatch", fake_dispatch)

    # The first job fails; the other two must still be processed.
    await asyncio.gather(
        *[
            worker_module.process_job(_job_payload(user_id))
            for user_id in range(1, 4)
        ]
    )

    assert calls["n"] == 3
    assert len(stub_telegram_happy["error"]) >= 1
    assert len(stub_telegram_happy["result"]) == 2


async def test_jobs_start_processing_in_arrival_order(
    job_dirs, stub_telegram_happy, monkeypatch
):

    monkeypatch.setattr(Processing, "MAX_CONCURRENT_JOBS", 1)

    started = []

    async def fake_dispatch(job):
        started.append(job.user_id)
        await asyncio.sleep(0.01)
        return True

    monkeypatch.setattr(worker_module.dispatcher, "dispatch", fake_dispatch)

    tasks = [
        asyncio.create_task(worker_module.process_job(_job_payload(user_id)))
        for user_id in (11, 22, 33)
    ]

    await asyncio.gather(*tasks)

    assert started == [11, 22, 33]


def test_semaphore_respects_configured_limit(monkeypatch):

    monkeypatch.setattr(Processing, "MAX_CONCURRENT_JOBS", 3)

    semaphore = worker_module._get_processing_semaphore()

    assert semaphore._value == 3


def test_semaphore_never_created_with_zero_or_negative_limit(monkeypatch):

    monkeypatch.setattr(Processing, "MAX_CONCURRENT_JOBS", 0)

    semaphore = worker_module._get_processing_semaphore()

    assert semaphore._value == 1
