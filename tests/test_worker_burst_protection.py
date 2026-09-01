"""
Tests for the burst-protection additions in worker.py:

- Per-user active-jobs cap (_process_job_safe / _active_jobs_by_user):
  a hard, immediate rejection once a user already has their tier's limit
  of jobs running, with slot release guaranteed on both success and
  failure.
- The global "jobs in flight" semaphore (_get_total_jobs_semaphore):
  bounds total concurrent jobs system-wide, separate from
  Processing.MAX_CONCURRENT_JOBS which only bounds the processing stage.

See tests/test_worker_concurrency.py for the pre-existing processing-
stage semaphore this sits alongside (deliberately untouched).
"""

import asyncio

from types import SimpleNamespace

import pytest

from config import Processing, Queue

import worker as worker_module

from utils.permissions import AccountTier


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

    from config import Paths

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

    async def fake_download(message, destination, **kwargs):
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
def fresh_burst_protection_state(monkeypatch):

    monkeypatch.setattr(worker_module, "_processing_semaphore", None)
    monkeypatch.setattr(worker_module, "_total_jobs_semaphore", None)
    monkeypatch.setattr(worker_module, "_active_jobs_by_user", {})

    yield

    worker_module._processing_semaphore = None
    worker_module._total_jobs_semaphore = None
    worker_module._active_jobs_by_user = {}


def _job_payload(user_id: int, tier: AccountTier = AccountTier.TRIAL):

    return {
        "user_id": user_id,
        "message_id": 1,
        "options": {},
        "account_tier": tier.value,
    }


# ======================================================================
# Per-user active-jobs cap
# ======================================================================


async def test_extra_job_beyond_the_trial_cap_is_rejected_cleanly(
    job_dirs, stub_telegram_happy, monkeypatch
):

    monkeypatch.setattr(Queue, "MAX_ACTIVE_JOBS_PER_USER_TRIAL", 2)
    monkeypatch.setattr(Processing, "MAX_CONCURRENT_JOBS", 10)
    monkeypatch.setattr(Queue, "MAX_CONCURRENT_JOBS_TOTAL", 10)

    dispatch_calls = {"n": 0}
    release = asyncio.Event()

    async def fake_dispatch(job):
        dispatch_calls["n"] += 1
        await release.wait()
        job.add_output(job.input_file)
        return True

    monkeypatch.setattr(worker_module.dispatcher, "dispatch", fake_dispatch)

    # Two jobs for the same user start and hang mid-processing (holding
    # their active-job slots)...
    tasks = [
        asyncio.create_task(worker_module._process_job_safe(_job_payload(777)))
        for _ in range(2)
    ]

    await asyncio.sleep(0.05)  # let both actually start dispatching

    # ...a third, for the SAME user, must be rejected immediately rather
    # than queued or attempted.
    await worker_module._process_job_safe(_job_payload(777))

    assert dispatch_calls["n"] == 2  # the third never reached dispatch
    assert len(stub_telegram_happy["error"]) == 1
    assert "همزمان" in stub_telegram_happy["error"][0]["message"]

    release.set()
    await asyncio.gather(*tasks)


async def test_a_different_user_is_never_affected_by_someone_elses_cap(
    job_dirs, stub_telegram_happy, monkeypatch
):

    monkeypatch.setattr(Queue, "MAX_ACTIVE_JOBS_PER_USER_TRIAL", 1)
    monkeypatch.setattr(Processing, "MAX_CONCURRENT_JOBS", 10)
    monkeypatch.setattr(Queue, "MAX_CONCURRENT_JOBS_TOTAL", 10)

    release = asyncio.Event()

    async def fake_dispatch(job):
        await release.wait()
        job.add_output(job.input_file)
        return True

    monkeypatch.setattr(worker_module.dispatcher, "dispatch", fake_dispatch)

    user_a_task = asyncio.create_task(
        worker_module._process_job_safe(_job_payload(111))
    )

    await asyncio.sleep(0.05)

    # user 111 is now at their cap of 1 — user 222 must be unaffected.
    user_b_task = asyncio.create_task(
        worker_module._process_job_safe(_job_payload(222))
    )

    await asyncio.sleep(0.05)

    assert stub_telegram_happy["error"] == []

    release.set()
    await asyncio.gather(user_a_task, user_b_task)


async def test_admin_is_never_subject_to_the_active_job_cap(
    job_dirs, stub_telegram_happy, monkeypatch
):

    monkeypatch.setattr(Queue, "MAX_ACTIVE_JOBS_PER_USER_TRIAL", 1)
    monkeypatch.setattr(Processing, "MAX_CONCURRENT_JOBS", 10)
    monkeypatch.setattr(Queue, "MAX_CONCURRENT_JOBS_TOTAL", 10)

    dispatch_calls = {"n": 0}

    async def fake_dispatch(job):
        dispatch_calls["n"] += 1
        await asyncio.sleep(0.01)
        job.add_output(job.input_file)
        return True

    monkeypatch.setattr(worker_module.dispatcher, "dispatch", fake_dispatch)

    await asyncio.gather(
        *[
            worker_module._process_job_safe(_job_payload(999, AccountTier.ADMIN))
            for _ in range(5)
        ]
    )

    assert dispatch_calls["n"] == 5
    # None of these should be the active-job-limit rejection — admins are
    # unlimited. (Uploads themselves fail in this test harness since the
    # Telethon client isn't actually connected, which also triggers
    # ADMIN_ERROR reports through the same send_error() channel — both
    # are unrelated, expected artifacts of not mocking upload_entry here,
    # same as every other test in this style — see
    # test_worker_concurrency.py.)
    assert not any(
        "همزمان" in e.get("message", "")
        for e in stub_telegram_happy["error"]
    )


async def test_paid_tier_gets_the_paid_limit_not_the_trial_one(
    job_dirs, stub_telegram_happy, monkeypatch
):

    monkeypatch.setattr(Queue, "MAX_ACTIVE_JOBS_PER_USER_TRIAL", 1)
    monkeypatch.setattr(Queue, "MAX_ACTIVE_JOBS_PER_USER_PAID", 3)
    monkeypatch.setattr(Processing, "MAX_CONCURRENT_JOBS", 10)
    monkeypatch.setattr(Queue, "MAX_CONCURRENT_JOBS_TOTAL", 10)

    dispatch_calls = {"n": 0}
    release = asyncio.Event()

    async def fake_dispatch(job):
        dispatch_calls["n"] += 1
        await release.wait()
        job.add_output(job.input_file)
        return True

    monkeypatch.setattr(worker_module.dispatcher, "dispatch", fake_dispatch)

    tasks = [
        asyncio.create_task(
            worker_module._process_job_safe(_job_payload(555, AccountTier.PAID))
        )
        for _ in range(3)
    ]

    await asyncio.sleep(0.05)

    # A 4th would be over the paid cap of 3 (but well under what would
    # be allowed if this were mistakenly treated as unlimited).
    assert dispatch_calls["n"] == 3

    release.set()
    await asyncio.gather(*tasks)


async def test_active_job_slot_is_released_after_a_dispatch_failure(
    job_dirs, stub_telegram_happy, monkeypatch
):
    """The finally-block guarantee: a job that raises must still free its
    slot, or a single failure would permanently eat into a user's cap."""

    monkeypatch.setattr(Queue, "MAX_ACTIVE_JOBS_PER_USER_TRIAL", 1)
    monkeypatch.setattr(Processing, "MAX_CONCURRENT_JOBS", 10)
    monkeypatch.setattr(Queue, "MAX_CONCURRENT_JOBS_TOTAL", 10)

    async def fake_dispatch(job):
        raise RuntimeError("boom")

    monkeypatch.setattr(worker_module.dispatcher, "dispatch", fake_dispatch)

    await worker_module._process_job_safe(_job_payload(42))

    assert worker_module._active_jobs_by_user.get(42, 0) == 0

    # The slot being free means a second job for the same user goes
    # through cleanly (not incorrectly rejected as "still active").
    await worker_module._process_job_safe(_job_payload(42))

    assert worker_module._active_jobs_by_user.get(42, 0) == 0


def test_max_active_jobs_for_tier(monkeypatch):

    monkeypatch.setattr(Queue, "MAX_ACTIVE_JOBS_PER_USER_TRIAL", 2)
    monkeypatch.setattr(Queue, "MAX_ACTIVE_JOBS_PER_USER_PAID", 6)

    assert worker_module._max_active_jobs_for_tier(AccountTier.ADMIN) == 0
    assert worker_module._max_active_jobs_for_tier(AccountTier.PAID) == 6
    assert worker_module._max_active_jobs_for_tier(AccountTier.TRIAL) == 2


# ======================================================================
# Global in-flight-jobs semaphore
# ======================================================================


async def test_total_jobs_semaphore_bounds_concurrency_across_all_users(
    job_dirs, stub_telegram_happy, monkeypatch
):

    monkeypatch.setattr(Queue, "MAX_CONCURRENT_JOBS_TOTAL", 2)
    monkeypatch.setattr(Processing, "MAX_CONCURRENT_JOBS", 10)  # not the bottleneck
    monkeypatch.setattr(Queue, "MAX_ACTIVE_JOBS_PER_USER_TRIAL", 10)  # not the bottleneck

    tracker = {"current": 0, "max": 0}

    async def fake_dispatch(job):
        tracker["current"] += 1
        tracker["max"] = max(tracker["max"], tracker["current"])
        await asyncio.sleep(0.05)
        tracker["current"] -= 1
        job.add_output(job.input_file)
        return True

    monkeypatch.setattr(worker_module.dispatcher, "dispatch", fake_dispatch)

    # 6 different users, so the per-user cap can't be what's limiting this.
    await asyncio.gather(
        *[
            worker_module._process_job_safe(_job_payload(user_id))
            for user_id in range(1, 7)
        ]
    )

    assert tracker["max"] == 2


def test_total_jobs_semaphore_respects_configured_limit(monkeypatch):

    monkeypatch.setattr(Queue, "MAX_CONCURRENT_JOBS_TOTAL", 4)

    semaphore = worker_module._get_total_jobs_semaphore()

    assert semaphore._value == 4


def test_total_jobs_semaphore_never_created_with_zero_or_negative_limit(monkeypatch):

    monkeypatch.setattr(Queue, "MAX_CONCURRENT_JOBS_TOTAL", 0)

    semaphore = worker_module._get_total_jobs_semaphore()

    assert semaphore._value == 1
