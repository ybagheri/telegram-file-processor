import pytest

from services.promo_post_store import PromoPostStore


@pytest.fixture
def store(tmp_path):
    return PromoPostStore(tmp_path / "promo_post.db")


def test_no_post_configured_returns_none(store):
    assert store.get() is None


@pytest.mark.asyncio
async def test_set_post_stores_the_reference_and_enables_it(store):
    await store.set_post(555, 42, set_by=100)

    post = store.get()
    assert post["source_chat_id"] == 555
    assert post["source_message_id"] == 42
    assert post["set_by"] == 100
    assert post["enabled"] is True


@pytest.mark.asyncio
async def test_setting_a_new_post_replaces_the_old_one(store):
    await store.set_post(555, 42, set_by=100)
    await store.set_post(777, 99, set_by=200)

    post = store.get()
    assert post["source_chat_id"] == 777
    assert post["source_message_id"] == 99
    assert post["set_by"] == 200


@pytest.mark.asyncio
async def test_setting_a_new_post_re_enables_it_even_if_previously_disabled(store):
    await store.set_post(555, 42)
    await store.set_enabled(False)
    assert store.get()["enabled"] is False

    await store.set_post(777, 99)
    assert store.get()["enabled"] is True


@pytest.mark.asyncio
async def test_set_enabled_toggles_without_touching_the_reference(store):
    await store.set_post(555, 42)

    assert await store.set_enabled(False) is True
    post = store.get()
    assert post["enabled"] is False
    assert post["source_chat_id"] == 555

    assert await store.set_enabled(True) is True
    assert store.get()["enabled"] is True


@pytest.mark.asyncio
async def test_set_enabled_on_no_configured_post_is_a_no_op(tmp_path):
    store = PromoPostStore(tmp_path / "promo2.db")

    assert await store.set_enabled(True) is False
    assert store.get() is None


@pytest.mark.asyncio
async def test_clear_removes_the_post(store):
    await store.set_post(555, 42)
    assert await store.clear() is True
    assert store.get() is None


@pytest.mark.asyncio
async def test_clear_with_no_post_returns_false(store):
    assert await store.clear() is False
