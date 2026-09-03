import pytest
from aiogram.types import User as TgUser

import bot as bot_module
from services.access_store import access_store
from services.blocked_user_store import blocked_user_store
from services.pending_user_store import pending_user_store

ADMIN_ID = 111


@pytest.fixture(autouse=True)
def clean_state():
    access_store._conn.execute("DELETE FROM authorized_users")
    access_store._conn.commit()
    pending_user_store._conn.execute("DELETE FROM pending_users")
    pending_user_store._conn.commit()
    blocked_user_store._conn.execute("DELETE FROM blocked_users")
    blocked_user_store._conn.commit()
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
    def __init__(self, text=None, from_user_id=None, forward_from=None):
        self.text = text
        self.from_user = TgUser(id=from_user_id or ADMIN_ID, is_bot=False, first_name="Admin")
        self.forward_from = forward_from
        self.answers = []
        self.markups = []

    async def answer(self, text=None, reply_markup=None, **kw):
        self.answers.append(text)
        self.markups.append(reply_markup)
        return self


# ======================================================================
# Listing pending users
# ======================================================================


@pytest.mark.asyncio
async def test_list_pending_shows_every_pending_user_with_a_block_button():
    await pending_user_store.record_start(701, first_name="Ali", username="ali_u")
    await pending_user_store.record_start(702, first_name="Sara")

    cb = FakeCallback("admin:list_pending", ADMIN_ID)
    await bot_module.admin_list_pending(cb)

    text = cb.message.answers[-1]
    assert "701" in text
    assert "702" in text

    markup = cb.message.markups[-1]
    callback_datas = {row[0].callback_data for row in markup.inline_keyboard}
    assert callback_datas == {"admin:block_confirm:701", "admin:block_confirm:702"}


@pytest.mark.asyncio
async def test_list_pending_with_no_users_says_so():
    cb = FakeCallback("admin:list_pending", ADMIN_ID)
    await bot_module.admin_list_pending(cb)
    assert "هیچ" in cb.message.answers[-1]


# ======================================================================
# Blocking via the forward/ID flow
# ======================================================================


@pytest.mark.asyncio
async def test_block_user_flow_by_numeric_id_end_to_end():
    cb = FakeCallback("admin:block_user", ADMIN_ID)
    await bot_module.admin_block_user(cb)
    assert bot_module.awaiting_state[ADMIN_ID] == "admin_block_target"

    msg = FakeMessage(text="555")
    handled = await bot_module.handle_awaited_input(msg, "admin_block_target")
    assert handled is True
    assert ADMIN_ID not in bot_module.awaiting_state

    confirm_markup = msg.markups[-1]
    confirm_cb_data = confirm_markup.inline_keyboard[0][0].callback_data
    assert confirm_cb_data == "admin:block_confirm:555"

    cb2 = FakeCallback(confirm_cb_data, ADMIN_ID)
    await bot_module.admin_block_confirmed(cb2)

    assert blocked_user_store.is_blocked(555) is True
    assert "555" in cb2.message.answers[-1]


@pytest.mark.asyncio
async def test_blocking_removes_the_user_from_the_pending_list():
    await pending_user_store.record_start(701, first_name="Ali")

    cb = FakeCallback("admin:block_confirm:701", ADMIN_ID)
    await bot_module.admin_block_confirmed(cb)

    assert pending_user_store.get(701) is None


@pytest.mark.asyncio
async def test_blocking_an_already_blocked_user_says_so_and_does_not_error():
    await blocked_user_store.block(555)

    cb = FakeCallback("admin:block_confirm:555", ADMIN_ID)
    await bot_module.admin_block_confirmed(cb)

    assert "قبل" in cb.message.answers[-1]


@pytest.mark.asyncio
async def test_unrecognized_target_reprompts_without_crashing():
    cb = FakeCallback("admin:block_user", ADMIN_ID)
    await bot_module.admin_block_user(cb)

    msg = FakeMessage(text="not a valid id or forward")
    handled = await bot_module.handle_awaited_input(msg, "admin_block_target")

    assert handled is True
    assert ADMIN_ID in bot_module.awaiting_state  # still waiting, not crashed


# ======================================================================
# Unblocking
# ======================================================================


@pytest.mark.asyncio
async def test_unblock_flow_end_to_end():
    await blocked_user_store.block(555)

    cb = FakeCallback("admin:unblock_user", ADMIN_ID)
    await bot_module.admin_unblock_user(cb)
    assert bot_module.awaiting_state[ADMIN_ID] == "admin_unblock_target"

    msg = FakeMessage(text="555")
    await bot_module.handle_awaited_input(msg, "admin_unblock_target")

    confirm_markup = msg.markups[-1]
    confirm_cb_data = confirm_markup.inline_keyboard[0][0].callback_data
    assert confirm_cb_data == "admin:unblock_confirm:555"

    cb2 = FakeCallback(confirm_cb_data, ADMIN_ID)
    await bot_module.admin_unblock_confirmed(cb2)

    assert blocked_user_store.is_blocked(555) is False


@pytest.mark.asyncio
async def test_unblocking_a_non_blocked_user_says_so():
    msg = FakeMessage(text="999")
    await bot_module.admin_unblock_user(FakeCallback("admin:unblock_user", ADMIN_ID))
    await bot_module.handle_awaited_input(msg, "admin_unblock_target")

    assert "نبود" in msg.answers[-1]


@pytest.mark.asyncio
async def test_list_blocked_shows_every_blocked_user_with_an_unblock_button():
    await blocked_user_store.block(555, blocked_by=ADMIN_ID, note="spam")
    await blocked_user_store.block(666)

    cb = FakeCallback("admin:list_blocked", ADMIN_ID)
    await bot_module.admin_list_blocked(cb)

    text = cb.message.answers[-1]
    assert "555" in text
    assert "666" in text
    assert "spam" in text

    markup = cb.message.markups[-1]
    callback_datas = {row[0].callback_data for row in markup.inline_keyboard}
    assert callback_datas == {"admin:unblock_confirm:555", "admin:unblock_confirm:666"}


# ======================================================================
# A blocked user actually loses access (end-to-end with is_authorized)
# ======================================================================


@pytest.mark.asyncio
async def test_a_blocked_registered_user_loses_authorization():
    from config import Telegram
    from utils.access_control import is_authorized

    await access_store.add(777, name="WasFine", expires_at=None)
    assert is_authorized(777) is True

    await blocked_user_store.block(777)
    assert is_authorized(777) is False

    await blocked_user_store.unblock(777)
    assert is_authorized(777) is True
