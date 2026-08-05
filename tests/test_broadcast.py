import time

import pytest
from aiogram.types import CallbackQuery, Chat, Message
from aiogram.types import User as TgUser

import bot as bot_module
from services.access_store import access_store
from services.pending_user_store import pending_user_store

ADMIN_ID = 111


@pytest.fixture(autouse=True)
def clean_state():
    access_store._conn.execute("DELETE FROM authorized_users")
    access_store._conn.commit()
    pending_user_store._conn.execute("DELETE FROM pending_users")
    pending_user_store._conn.commit()
    yield
    bot_module.admin_flow.pop(ADMIN_ID, None)
    bot_module.awaiting_state.pop(ADMIN_ID, None)


class FakeCallback:
    def __init__(self, data, user_id):
        self.data = data
        self.from_user = TgUser(id=user_id, is_bot=False, first_name="Admin")
        self.message = FakeMessage()

    async def answer(self, text=None, show_alert=False):
        pass


class FakeMessage:
    def __init__(self, text=None, from_user_id=None):
        self.text = text
        self.from_user = TgUser(id=from_user_id or ADMIN_ID, is_bot=False, first_name="Admin")
        self.answers = []
        self.markups = []

    async def answer(self, text=None, reply_markup=None, **kw):
        self.answers.append(text)
        self.markups.append(reply_markup)
        return self


@pytest.mark.asyncio
async def test_notify_admins_uses_html_with_copyable_id(monkeypatch):
    calls = []

    async def fake_send_message(chat_id, text=None, parse_mode=None, **kw):
        calls.append((chat_id, text, parse_mode))

    monkeypatch.setattr(bot_module.bot, "send_message", fake_send_message)

    class FakeTgUser:
        id = 800
        full_name = "Reza <script>"  # deliberately includes an HTML-special char
        username = "reza_u"
        first_name = "Reza"
        last_name = None
        is_bot = False

    await bot_module.notify_admins_of_new_pending_user(FakeTgUser())

    admin_calls = [c for c in calls if c[0] in (111, 222)]
    assert len(admin_calls) == 2
    for _, text, parse_mode in admin_calls:
        assert parse_mode == "HTML"
        assert "<code>800</code>" in text
        # the raw "<script>" must be escaped, not passed through as live HTML
        assert "<script>" not in text
        assert "&lt;script&gt;" in text


@pytest.mark.asyncio
async def test_broadcast_full_flow_sends_only_to_reachable_pending_users(monkeypatch):
    await pending_user_store.record_start(701, first_name="Ali")
    await pending_user_store.record_start(702, first_name="Sara")
    await pending_user_store.record_start(703, first_name="Blocked")

    sent_to = []

    async def fake_send_message(chat_id, text=None, **kw):
        if chat_id == 703:
            raise Exception("Forbidden: bot was blocked by the user")
        sent_to.append((chat_id, text))
        class Fake:
            message_id = 1
        return Fake()

    monkeypatch.setattr(bot_module.bot, "send_message", fake_send_message)

    cb = FakeCallback("admin:broadcast_pending", ADMIN_ID)
    await bot_module.admin_broadcast_pending(cb)
    assert bot_module.awaiting_state[ADMIN_ID] == "admin_broadcast_pending_text"
    assert "3" in cb.message.answers[-1]

    msg = FakeMessage(text="پیام تست همگانی")
    handled = await bot_module.handle_awaited_input(msg, "admin_broadcast_pending_text")
    assert handled
    assert ADMIN_ID not in bot_module.awaiting_state
    assert bot_module.admin_flow[ADMIN_ID]["broadcast_text"] == "پیام تست همگانی"

    confirm_markup = msg.markups[-1]
    confirm_cb_data = confirm_markup.inline_keyboard[0][0].callback_data
    assert confirm_cb_data == "admin:broadcast_confirm"

    cb2 = FakeCallback(confirm_cb_data, ADMIN_ID)
    await bot_module.admin_broadcast_confirm(cb2)

    broadcast_sent = [c for c in sent_to if c[0] in (701, 702, 703)]
    assert len(broadcast_sent) == 2, f"expected 2 successful sends, got {broadcast_sent}"
    assert all(c[1] == "پیام تست همگانی" for c in broadcast_sent)

    result_text = cb2.message.answers[-1]
    assert "موفق: 2" in result_text
    assert "ناموفق: 1" in result_text
    assert ADMIN_ID not in bot_module.admin_flow


@pytest.mark.asyncio
async def test_broadcast_cancel_sends_nothing(monkeypatch):
    await pending_user_store.record_start(701, first_name="Ali")

    sent_to = []
    async def fake_send_message(chat_id, text=None, **kw):
        sent_to.append(chat_id)
    monkeypatch.setattr(bot_module.bot, "send_message", fake_send_message)

    cb = FakeCallback("admin:broadcast_pending", ADMIN_ID)
    await bot_module.admin_broadcast_pending(cb)
    msg = FakeMessage(text="test")
    await bot_module.handle_awaited_input(msg, "admin_broadcast_pending_text")

    cb2 = FakeCallback("admin:broadcast_cancel", ADMIN_ID)
    await bot_module.admin_broadcast_cancelled(cb2)

    assert ADMIN_ID not in bot_module.admin_flow
    assert sent_to == []


@pytest.mark.asyncio
async def test_broadcast_with_zero_pending_users_is_handled_cleanly():
    cb = FakeCallback("admin:broadcast_pending", ADMIN_ID)
    await bot_module.admin_broadcast_pending(cb)
    assert ADMIN_ID not in bot_module.awaiting_state
    assert "هیچ" in cb.message.answers[-1]


@pytest.mark.asyncio
async def test_admin_broadcast_button_reaches_admin_router_via_real_dispatch(monkeypatch):
    """Real aiogram Update through feed_update, not a direct function call
    — same discipline as tests/test_router_wiring.py."""
    from aiogram.methods import AnswerCallbackQuery, SendMessage

    calls = []

    async def fake_make_request(bot_arg, method, timeout=None):
        calls.append(method)
        if isinstance(method, SendMessage):
            class FakeMsg:
                message_id = 1
            return FakeMsg()
        if isinstance(method, AnswerCallbackQuery):
            return True
        return None

    monkeypatch.setattr(bot_module.bot.session, "make_request", fake_make_request)

    await pending_user_store.record_start(999, first_name="Someone")

    chat = Chat(id=ADMIN_ID, type="private")
    user = TgUser(id=ADMIN_ID, is_bot=False, first_name="Admin")
    cb_message = Message(
        message_id=1, date=int(time.time()), chat=chat, from_user=user,
        text="⚙️ پنل مدیریت کاربران:",
    )
    from aiogram.types import Update
    callback = CallbackQuery(
        id="cbqX", from_user=user, chat_instance="abc",
        data="admin:broadcast_pending", message=cb_message,
    )
    update = Update(update_id=1, callback_query=callback)

    await bot_module.dp.feed_update(bot_module.bot, update)

    sent = [c for c in calls if isinstance(c, SendMessage)]
    assert len(sent) == 1
    assert "1" in sent[0].text


@pytest.mark.asyncio
async def test_admin_stats_reports_registered_and_pending_breakdown():
    import time as time_module

    await access_store.add(1, name="Active", expires_at=None)
    await access_store.add(2, name="Expired", expires_at=time_module.time() - 10)
    await access_store.add(3, name="Disabled", expires_at=None)
    await access_store.set_active(3, False)

    await pending_user_store.record_start(701, first_name="Pending1")
    await pending_user_store.record_start(702, first_name="Pending2")

    cb = FakeCallback("admin:stats", ADMIN_ID)
    await bot_module.admin_stats(cb)

    text = cb.message.answers[-1]
    assert "کاربران مجاز" in text
    assert "3" in text  # total registered
    assert "فعال: 1" in text
    assert "منقضی: 1" in text
    assert "غیرفعال: 1" in text
    assert "2" in text  # total pending
    # conversion rate: 3 registered out of 5 total seen = 60.0%
    assert "60.0" in text


@pytest.mark.asyncio
async def test_admin_stats_with_no_users_at_all_does_not_crash():
    cb = FakeCallback("admin:stats", ADMIN_ID)
    await bot_module.admin_stats(cb)
    text = cb.message.answers[-1]
    assert "0" in text
    assert "—" in text  # conversion rate undefined with zero users
