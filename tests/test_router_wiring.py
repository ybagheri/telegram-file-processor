"""
Tests that drive REAL aiogram Update objects through the actual
Dispatcher (bot.dp.feed_update), rather than calling handler functions
directly. This is the only way to catch router-wiring/precedence bugs —
see CLAUDE.md's phase-D change log entry for the actual regression this
caught: moving `/admin` onto its own Router initially broke it, because
aiogram checks a router's own directly-decorated handlers (like
`handle_private_message`'s bare private-chat catch-all) BEFORE any
included sub-router, regardless of when `dp.include_router(...)` was
called. Every other test file in this project calls handler functions
directly and would never have caught that class of bug.
"""
import time

import pytest
from aiogram.methods import AnswerCallbackQuery, SendMessage
from aiogram.types import CallbackQuery, Chat, Message, Update
from aiogram.types import User as TgUser

import bot as bot_module
from services.access_store import access_store


@pytest.fixture
def api_calls(monkeypatch):
    """Intercept every outgoing Telegram API call at the layer aiogram's
    Message.answer()/CallbackQuery.answer() actually go through
    (Bot.session.make_request), not just the bot.send_message(...)
    convenience wrapper other tests in this project patch — those are two
    different code paths in aiogram 3, and only patching the wrapper
    would still attempt a real network call for anything routed through
    feed_update()."""
    calls = []

    async def fake_make_request(bot_arg, method, timeout=None):
        calls.append(method)
        if isinstance(method, SendMessage):
            class FakeMsg:
                message_id = 999
            return FakeMsg()
        if isinstance(method, AnswerCallbackQuery):
            return True
        return None

    monkeypatch.setattr(bot_module.bot.session, "make_request", fake_make_request)
    return calls


@pytest.fixture(autouse=True)
def clean_access_store():
    access_store._conn.execute("DELETE FROM authorized_users")
    access_store._conn.commit()


def _sent_messages(calls):
    return [c for c in calls if isinstance(c, SendMessage)]


ADMIN_ID = 111  # from tests/conftest.py's ADMIN_IDS env var


@pytest.mark.asyncio
async def test_admin_command_reaches_admin_router_not_the_catchall(api_calls):
    chat = Chat(id=ADMIN_ID, type="private")
    user = TgUser(id=ADMIN_ID, is_bot=False, first_name="Admin")
    msg = Message(message_id=1, date=int(time.time()), chat=chat, from_user=user, text="/admin")
    update = Update(update_id=1, message=msg)

    await bot_module.dp.feed_update(bot_module.bot, update)

    sent = _sent_messages(api_calls)
    assert len(sent) == 1, f"expected exactly one reply to /admin, got {api_calls}"
    assert sent[0].chat_id == ADMIN_ID
    assert "پنل مدیریت" in sent[0].text
    callback_datas = {b.callback_data for row in sent[0].reply_markup.inline_keyboard for b in row}
    assert "admin:add_user" in callback_datas


@pytest.mark.asyncio
async def test_admin_callback_reaches_admin_router(api_calls):
    chat = Chat(id=ADMIN_ID, type="private")
    user = TgUser(id=ADMIN_ID, is_bot=False, first_name="Admin")
    cb_message = Message(
        message_id=2, date=int(time.time()), chat=chat, from_user=user,
        text="⚙️ پنل مدیریت کاربران:",
    )
    callback = CallbackQuery(
        id="cbq1", from_user=user, chat_instance="abc",
        data="admin:add_user", message=cb_message,
    )
    update = Update(update_id=2, callback_query=callback)

    await bot_module.dp.feed_update(bot_module.bot, update)

    sent = _sent_messages(api_calls)
    answered = [c for c in api_calls if isinstance(c, AnswerCallbackQuery)]
    assert len(sent) == 1, f"expected one message in response to the admin callback, got {api_calls}"
    assert "مدت اعتبار" in sent[0].text
    assert len(answered) == 1


@pytest.mark.asyncio
async def test_admin_broadcast_callback_reaches_admin_router(api_calls):
    from services.pending_user_store import pending_user_store
    pending_user_store._conn.execute("DELETE FROM pending_users")
    pending_user_store._conn.commit()
    await pending_user_store.record_start(999888, first_name="Someone")

    chat = Chat(id=ADMIN_ID, type="private")
    user = TgUser(id=ADMIN_ID, is_bot=False, first_name="Admin")
    cb_message = Message(
        message_id=3, date=int(time.time()), chat=chat, from_user=user,
        text="⚙️ پنل مدیریت کاربران:",
    )
    callback = CallbackQuery(
        id="cbq5", from_user=user, chat_instance="abc",
        data="admin:broadcast_pending", message=cb_message,
    )
    update = Update(update_id=5, callback_query=callback)

    await bot_module.dp.feed_update(bot_module.bot, update)

    sent = _sent_messages(api_calls)
    assert len(sent) == 1, f"expected one prompt for the broadcast text, got {api_calls}"
    assert "1" in sent[0].text


@pytest.mark.asyncio
async def test_settings_command_reaches_settings_router_not_the_catchall(api_calls):
    non_admin_id = 555111222
    chat = Chat(id=non_admin_id, type="private")
    user = TgUser(id=non_admin_id, is_bot=False, first_name="Regular")

    from services.access_store import access_store as store
    await store.add(non_admin_id, name="Regular", expires_at=None)  # must be authorized to see settings

    msg = Message(message_id=1, date=int(time.time()), chat=chat, from_user=user, text="/settings")
    update = Update(update_id=1, message=msg)

    await bot_module.dp.feed_update(bot_module.bot, update)

    sent = _sent_messages(api_calls)
    assert len(sent) == 1, f"expected exactly one reply to /settings, got {api_calls}"
    assert sent[0].chat_id == non_admin_id
    assert "تنظیمات پیش‌فرض" in sent[0].text
    callback_datas = {b.callback_data for row in sent[0].reply_markup.inline_keyboard for b in row}
    assert "s:quality" in callback_datas


@pytest.mark.asyncio
async def test_settings_callback_reaches_settings_router(api_calls):
    non_admin_id = 555111222
    chat = Chat(id=non_admin_id, type="private")
    user = TgUser(id=non_admin_id, is_bot=False, first_name="Regular")

    from services.access_store import access_store as store
    await store.add(non_admin_id, name="Regular", expires_at=None)

    cb_message = Message(
        message_id=2, date=int(time.time()), chat=chat, from_user=user,
        text="⚙️ تنظیمات پیش‌فرض شما:",
    )
    callback = CallbackQuery(
        id="cbq2", from_user=user, chat_instance="abc",
        data="s:watermark", message=cb_message,
    )
    update = Update(update_id=2, callback_query=callback)

    await bot_module.dp.feed_update(bot_module.bot, update)

    from aiogram.methods import EditMessageText
    edited = [c for c in api_calls if isinstance(c, EditMessageText)]
    answered = [c for c in api_calls if isinstance(c, AnswerCallbackQuery)]
    assert len(edited) == 1, f"expected settings_watermark to edit the settings message, got {api_calls}"
    assert len(answered) == 1


@pytest.mark.asyncio
async def test_quality_pick_callback_reaches_files_router(api_calls):
    from state import pending_files
    from models.pending_file import PendingFile

    non_admin_id = 555111223
    chat = Chat(id=non_admin_id, type="private")
    user = TgUser(id=non_admin_id, is_bot=False, first_name="Regular")

    pid = "testpid1"
    pending_files[pid] = PendingFile(
        user_id=non_admin_id, chat_id=non_admin_id,
        file_name="test.mp4", file_type="VIDEO", source_message=object(),
    )
    try:
        cb_message = Message(
            message_id=2, date=int(time.time()), chat=chat, from_user=user,
            text="کیفیت را انتخاب کنید:",
        )
        callback = CallbackQuery(
            id="cbq3", from_user=user, chat_instance="abc",
            data=f"q:{pid}:360", message=cb_message,
        )
        update = Update(update_id=3, callback_query=callback)

        await bot_module.dp.feed_update(bot_module.bot, update)

        from aiogram.methods import EditMessageText
        edited = [c for c in api_calls if isinstance(c, EditMessageText)]
        answered = [c for c in api_calls if isinstance(c, AnswerCallbackQuery)]
        assert len(edited) == 1, f"expected quality_pick to edit the message, got {api_calls}"
        assert pending_files[pid].options["quality"] == "360"
        assert len(answered) == 1
    finally:
        pending_files.pop(pid, None)


@pytest.mark.asyncio
async def test_photo_watermark_callback_reaches_photo_router(api_calls):
    from state import pending_photos
    from models.pending_photo import PendingPhoto

    non_admin_id = 555111224
    chat = Chat(id=non_admin_id, type="private")
    user = TgUser(id=non_admin_id, is_bot=False, first_name="Regular")

    pid = "testpid2"
    pending_photos[pid] = PendingPhoto(user_id=non_admin_id, chat_id=non_admin_id, source_message=object())
    try:
        cb_message = Message(
            message_id=2, date=int(time.time()), chat=chat, from_user=user,
            text="این یک عکسه...",
        )
        callback = CallbackQuery(
            id="cbq4", from_user=user, chat_instance="abc",
            data=f"pw:{pid}:cancel", message=cb_message,
        )
        update = Update(update_id=4, callback_query=callback)

        await bot_module.dp.feed_update(bot_module.bot, update)

        from aiogram.methods import EditMessageText
        edited = [c for c in api_calls if isinstance(c, EditMessageText)]
        answered = [c for c in api_calls if isinstance(c, AnswerCallbackQuery)]
        assert len(edited) == 1, f"expected photo_watermark_action to edit the message, got {api_calls}"
        assert "کاری" in edited[0].text
        assert len(answered) == 1
        assert pid not in pending_photos
    finally:
        pending_photos.pop(pid, None)


@pytest.mark.asyncio
async def test_ordinary_start_still_reaches_its_handler_not_swallowed(api_calls):
    non_admin_id = 987654321
    chat = Chat(id=non_admin_id, type="private")
    user = TgUser(id=non_admin_id, is_bot=False, first_name="Regular")
    msg = Message(message_id=1, date=int(time.time()), chat=chat, from_user=user, text="/start")
    update = Update(update_id=1, message=msg)

    await bot_module.dp.feed_update(bot_module.bot, update)

    sent = _sent_messages(api_calls)
    # a brand-new, non-authorized user's /start gets the trial-tier
    # welcome (no hard "اجازه" block anymore — see utils/permissions.py),
    # plus phase 6's pending-user admin notifications (one per configured
    # admin) still fire from track_pending_user_if_needed.
    reply_to_user = [c for c in sent if c.chat_id == non_admin_id]
    assert len(reply_to_user) == 1
    assert "فایل خود را ارسال کنید" in reply_to_user[0].text


@pytest.mark.asyncio
async def test_plain_text_message_reaches_catchall_router(api_calls):
    from services.pending_user_store import pending_user_store
    pending_user_store._conn.execute("DELETE FROM pending_users")
    pending_user_store._conn.commit()

    # A fresh id: a plain text message produces no reply to the user
    # itself anymore (no not-authorized gate, no URL, no file), so the
    # observable proof that the catchall router ran is the pending-user
    # admin notification that track_pending_user_if_needed fires from
    # inside handle_private_message.
    fresh_user_id = 987654322
    chat = Chat(id=fresh_user_id, type="private")
    user = TgUser(id=fresh_user_id, is_bot=False, first_name="Plain")
    msg = Message(message_id=2, date=int(time.time()), chat=chat, from_user=user, text="hello there")
    update = Update(update_id=2, message=msg)

    await bot_module.dp.feed_update(bot_module.bot, update)

    from config import Telegram

    sent = _sent_messages(api_calls)
    admin_ids = tuple(Telegram.ADMIN_IDS)
    admin_notifications = [c for c in sent if c.chat_id in admin_ids]
    user_replies = [c for c in sent if c.chat_id == fresh_user_id]
    assert len(admin_notifications) == len(admin_ids), f"expected one notification per admin, got {api_calls}"
    assert str(fresh_user_id) in admin_notifications[0].text
    assert user_replies == []


@pytest.mark.asyncio
async def test_cancel_command_reaches_core_router(api_calls):
    non_admin_id = 555111225
    chat = Chat(id=non_admin_id, type="private")
    user = TgUser(id=non_admin_id, is_bot=False, first_name="Regular")

    msg = Message(message_id=1, date=int(time.time()), chat=chat, from_user=user, text="/cancel")
    update = Update(update_id=1, message=msg)

    await bot_module.dp.feed_update(bot_module.bot, update)

    sent = _sent_messages(api_calls)
    assert len(sent) == 1, f"expected /cancel to reach cancel_command, got {api_calls}"
    assert "لغو" in sent[0].text


@pytest.mark.asyncio
async def test_bridge_message_reaches_bridge_router(api_calls, monkeypatch):
    from core.protocol import Protocol

    non_admin_id = 555111226
    target_chat = Chat(id=int(bot_module.Telegram.GROUP_ID), type="supergroup")
    from_user = TgUser(id=999, is_bot=False, first_name="Worker")

    payload = Protocol.create_info(user_id=non_admin_id, job_id="job1", message="در حال پردازش...")
    text = Protocol.encode(payload)

    msg = Message(message_id=1, date=int(time.time()), chat=target_chat, from_user=from_user, text=text)
    update = Update(update_id=1, message=msg)

    await bot_module.dp.feed_update(bot_module.bot, update)

    sent = _sent_messages(api_calls)
    assert len(sent) == 1, f"expected bridge INFO message to reach handle_bridge_message, got {api_calls}"
    assert sent[0].chat_id == non_admin_id
    assert "در حال پردازش" in sent[0].text


@pytest.mark.asyncio
async def test_unauthorized_user_sending_a_file_directly_still_notifies_admins(api_calls, monkeypatch):
    """Regression test for a real reported bug: the pending-user admin
    notification originally only lived inside the /start handler. A user
    who never sends /start at all — e.g. sends a file straight away,
    exactly what qa-userbot's scenarios do — went completely unnoticed.
    Fixed by calling track_pending_user_if_needed from every
    is_authorized() check site, not just /start. See CLAUDE.md's change
    log for the full story."""
    from services.pending_user_store import pending_user_store
    pending_user_store._conn.execute("DELETE FROM pending_users")
    pending_user_store._conn.commit()

    from aiogram.types import Document

    new_user_id = 555444333
    chat = Chat(id=new_user_id, type="private")
    user = TgUser(id=new_user_id, is_bot=False, first_name="Brand", last_name="New", username="brandnew_user")
    doc = Document(file_id="fid1", file_unique_id="fuid1", file_name="video.mp4")

    # THE ACTUAL BUG SCENARIO: brand-new, unauthorized user sends a file
    # DIRECTLY -- never sends /start at all.
    msg = Message(message_id=1, date=int(time.time()), chat=chat, from_user=user, document=doc)
    update = Update(update_id=1, message=msg)

    await bot_module.dp.feed_update(bot_module.bot, update)

    sent = _sent_messages(api_calls)
    admin_notifications = [c for c in sent if c.chat_id in (111, 222)]
    user_replies = [c for c in sent if c.chat_id == new_user_id]

    assert len(admin_notifications) == 2, f"expected one notification per admin, got {api_calls}"
    for note in admin_notifications:
        assert "کاربر جدید" in note.text
        assert "Brand New" in note.text
        assert "@brandnew_user" in note.text
        assert str(new_user_id) in note.text

    # Trial users are no longer hard-blocked (see handlers/core.py's
    # "no hard access gate" note): the reply is the normal per-file flow,
    # not an access-denied message — but the admin STILL gets notified
    # about the new pending user, which is what this test guards.
    assert len(user_replies) == 1
    assert "اجازه" not in user_replies[0].text
    assert "کیفیت" in user_replies[0].text

    info = pending_user_store.get(new_user_id)
    assert info is not None, "user must be tracked as pending even though they never sent /start"
    assert info["first_name"] == "Brand"
    assert info["username"] == "brandnew_user"
