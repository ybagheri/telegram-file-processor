import pytest
from aiogram.types import User as TgUser

import bot as bot_module
from services.promo_post_store import promo_post_store

ADMIN_ID = 111


@pytest.fixture(autouse=True)
def clean_state():
    promo_post_store._conn.execute("DELETE FROM promo_post")
    promo_post_store._conn.commit()
    yield
    bot_module.awaiting_state.pop(ADMIN_ID, None)


class FakeCallback:
    def __init__(self, data, user_id):
        self.data = data
        self.from_user = TgUser(id=user_id, is_bot=False, first_name="Admin")
        self.message = FakeMessage()

    async def answer(self, text=None, show_alert=False):
        pass


class FakeMessage:
    def __init__(self, text=None, chat_id=ADMIN_ID, message_id=1):
        self.text = text
        self.from_user = TgUser(id=ADMIN_ID, is_bot=False, first_name="Admin")

        class _Chat:
            id = chat_id

        self.chat = _Chat()
        self.message_id = message_id
        self.answers = []
        self.markups = []

    async def answer(self, text=None, reply_markup=None, **kw):
        self.answers.append(text)
        self.markups.append(reply_markup)
        return self


@pytest.mark.asyncio
async def test_menu_with_no_post_shows_only_the_set_button():
    cb = FakeCallback("admin:promo_menu", ADMIN_ID)
    await bot_module.admin_promo_menu(cb)

    assert "تنظیم نشده" in cb.message.answers[-1]
    markup = cb.message.markups[-1]
    callback_datas = {row[0].callback_data for row in markup.inline_keyboard}
    assert "admin:promo_set" in callback_datas
    assert "admin:promo_preview" not in callback_datas


@pytest.mark.asyncio
async def test_set_post_flow_stores_the_message_reference_and_enables_it():
    cb = FakeCallback("admin:promo_set", ADMIN_ID)
    await bot_module.admin_promo_set(cb)
    assert bot_module.awaiting_state[ADMIN_ID] == "admin_promo_post"

    msg = FakeMessage(text="Check out our channel!", chat_id=ADMIN_ID, message_id=42)
    handled = await bot_module.handle_awaited_input(msg, "admin_promo_post")

    assert handled is True
    assert ADMIN_ID not in bot_module.awaiting_state

    post = promo_post_store.get()
    assert post["source_chat_id"] == ADMIN_ID
    assert post["source_message_id"] == 42
    assert post["enabled"] is True


@pytest.mark.asyncio
async def test_menu_after_setting_a_post_shows_the_full_action_set():
    await promo_post_store.set_post(ADMIN_ID, 42, set_by=ADMIN_ID)

    cb = FakeCallback("admin:promo_menu", ADMIN_ID)
    await bot_module.admin_promo_menu(cb)

    assert "فعال" in cb.message.answers[-1]
    markup = cb.message.markups[-1]
    callback_datas = {row[0].callback_data for row in markup.inline_keyboard}
    assert callback_datas == {
        "admin:promo_set", "admin:promo_preview",
        "admin:promo_toggle", "admin:promo_delete", "admin:panel",
    }


@pytest.mark.asyncio
async def test_preview_copies_the_stored_message_to_the_admin(monkeypatch):
    await promo_post_store.set_post(999, 42, set_by=ADMIN_ID)

    calls = []

    async def fake_copy_message(chat_id, from_chat_id, message_id):
        calls.append((chat_id, from_chat_id, message_id))

    monkeypatch.setattr(bot_module.bot, "copy_message", fake_copy_message)

    cb = FakeCallback("admin:promo_preview", ADMIN_ID)
    await bot_module.admin_promo_preview(cb)

    assert calls == [(ADMIN_ID, 999, 42)]


@pytest.mark.asyncio
async def test_preview_with_no_post_says_so():
    cb = FakeCallback("admin:promo_preview", ADMIN_ID)
    await bot_module.admin_promo_preview(cb)
    assert "تنظیم نشده" in cb.message.answers[-1]


@pytest.mark.asyncio
async def test_toggle_flips_enabled_state():
    await promo_post_store.set_post(ADMIN_ID, 42)
    assert promo_post_store.get()["enabled"] is True

    cb = FakeCallback("admin:promo_toggle", ADMIN_ID)
    await bot_module.admin_promo_toggle(cb)
    assert promo_post_store.get()["enabled"] is False
    assert "غیرفعال" in cb.message.answers[-1]

    cb2 = FakeCallback("admin:promo_toggle", ADMIN_ID)
    await bot_module.admin_promo_toggle(cb2)
    assert promo_post_store.get()["enabled"] is True


@pytest.mark.asyncio
async def test_delete_clears_the_post():
    await promo_post_store.set_post(ADMIN_ID, 42)

    cb = FakeCallback("admin:promo_delete", ADMIN_ID)
    await bot_module.admin_promo_delete(cb)

    assert promo_post_store.get() is None
    assert "حذف" in cb.message.answers[-1]


@pytest.mark.asyncio
async def test_delete_with_no_post_says_so():
    cb = FakeCallback("admin:promo_delete", ADMIN_ID)
    await bot_module.admin_promo_delete(cb)
    assert "وجود نداشت" in cb.message.answers[-1]
