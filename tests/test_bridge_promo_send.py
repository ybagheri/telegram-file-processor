"""
Tests the one addition to handlers/bridge.py: after a job's DONE
completion notice, the configured promo post (if any and enabled) gets
copied to the same user. Everything else about handle_bridge_message's
DONE handling is pre-existing and out of scope here.
"""
import time

import pytest

import handlers.bridge as bridge_module
from core.protocol import Protocol
from services.promo_post_store import promo_post_store

USER_ID = 555


@pytest.fixture(autouse=True)
def clean_state():
    promo_post_store._conn.execute("DELETE FROM promo_post")
    promo_post_store._conn.commit()
    yield


class FakeChat:
    id = -100999  # the bridge group


class FakeTelethonMessage:
    def __init__(self, payload: dict, message_id: int = 1):
        self.text = Protocol.encode(payload)
        self.caption = None
        self.chat = FakeChat()
        self.message_id = message_id
        self.document = None
        self.video = None
        self.audio = None
        self.voice = None
        self.photo = None


def _done_message():
    return FakeTelethonMessage(Protocol.create_done(user_id=USER_ID, job_id="job1"))


@pytest.mark.asyncio
async def test_no_promo_post_configured_sends_nothing_extra(monkeypatch):

    async def fake_send_text(user_id, text):
        pass

    copy_calls = []

    async def fake_copy_message(chat_id, from_chat_id, message_id):
        copy_calls.append((chat_id, from_chat_id, message_id))

    monkeypatch.setattr(bridge_module.telegram_service, "send_text", fake_send_text)
    monkeypatch.setattr(bridge_module.bot, "copy_message", fake_copy_message)

    await bridge_module.handle_bridge_message(_done_message())

    assert copy_calls == []


@pytest.mark.asyncio
async def test_enabled_promo_post_is_copied_to_the_user_after_done(monkeypatch):

    await promo_post_store.set_post(777, 42, set_by=100)

    async def fake_send_text(user_id, text):
        pass

    copy_calls = []

    async def fake_copy_message(chat_id, from_chat_id, message_id):
        copy_calls.append((chat_id, from_chat_id, message_id))

    monkeypatch.setattr(bridge_module.telegram_service, "send_text", fake_send_text)
    monkeypatch.setattr(bridge_module.bot, "copy_message", fake_copy_message)

    await bridge_module.handle_bridge_message(_done_message())

    assert copy_calls == [(USER_ID, 777, 42)]


@pytest.mark.asyncio
async def test_disabled_promo_post_is_not_sent(monkeypatch):

    await promo_post_store.set_post(777, 42)
    await promo_post_store.set_enabled(False)

    async def fake_send_text(user_id, text):
        pass

    copy_calls = []

    async def fake_copy_message(chat_id, from_chat_id, message_id):
        copy_calls.append((chat_id, from_chat_id, message_id))

    monkeypatch.setattr(bridge_module.telegram_service, "send_text", fake_send_text)
    monkeypatch.setattr(bridge_module.bot, "copy_message", fake_copy_message)

    await bridge_module.handle_bridge_message(_done_message())

    assert copy_calls == []


@pytest.mark.asyncio
async def test_a_failed_promo_send_never_raises_or_blocks_the_done_flow(monkeypatch):

    await promo_post_store.set_post(777, 42)

    sent = []

    async def fake_send_text(user_id, text):
        sent.append((user_id, text))

    async def fake_copy_message(chat_id, from_chat_id, message_id):
        raise Exception("Forbidden: bot was blocked by the user")

    monkeypatch.setattr(bridge_module.telegram_service, "send_text", fake_send_text)
    monkeypatch.setattr(bridge_module.bot, "copy_message", fake_copy_message)

    # Must not raise.
    await bridge_module.handle_bridge_message(_done_message())

    assert sent == [(USER_ID, "✅ همه‌ی فایل‌ها با موفقیت ارسال شدند.")]
