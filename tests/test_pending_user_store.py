import pytest

from services.pending_user_store import PendingUserStore


@pytest.fixture
def store(tmp_path):
    return PendingUserStore(tmp_path / "pending_users.db")


def test_get_unknown_user_returns_none(store):
    assert store.get(999999) is None


@pytest.mark.asyncio
async def test_first_start_is_reported_as_new(store):
    is_new = await store.record_start(1, first_name="Ali", username="ali_u")
    assert is_new is True
    info = store.get(1)
    assert info["first_name"] == "Ali"
    assert info["username"] == "ali_u"
    assert info["start_count"] == 1
    assert info["admin_notified_at"] is None


@pytest.mark.asyncio
async def test_repeat_start_is_not_new_and_bumps_count_not_identity(store):
    await store.record_start(1, first_name="Ali", username="ali_u")
    is_new_again = await store.record_start(1, first_name="Different Name Somehow", username="changed")
    assert is_new_again is False

    info = store.get(1)
    assert info["start_count"] == 2
    # repeat starts only bump last_seen_at/start_count -- identity fields
    # from the FIRST start are preserved, not overwritten
    assert info["first_name"] == "Ali"
    assert info["username"] == "ali_u"


@pytest.mark.asyncio
async def test_last_seen_at_advances_on_repeat_start_but_first_seen_at_does_not(store):
    import asyncio

    await store.record_start(1, first_name="Ali")
    first_seen_at = store.get(1)["first_seen_at"]
    last_seen_at_1 = store.get(1)["last_seen_at"]

    await asyncio.sleep(0.01)
    await store.record_start(1, first_name="Ali")

    info = store.get(1)
    assert info["last_seen_at"] > last_seen_at_1
    assert info["first_seen_at"] == first_seen_at


@pytest.mark.asyncio
async def test_mark_notified_sets_timestamp(store):
    await store.record_start(1, first_name="Ali")
    assert store.get(1)["admin_notified_at"] is None
    await store.mark_notified(1)
    assert store.get(1)["admin_notified_at"] is not None


@pytest.mark.asyncio
async def test_remove_deletes_record(store):
    await store.record_start(1, first_name="Ali")
    assert await store.remove(1) is True
    assert store.get(1) is None
    assert await store.remove(1) is False  # already gone


@pytest.mark.asyncio
async def test_list_all_returns_every_pending_user(store):
    await store.record_start(1, first_name="Ali")
    await store.record_start(2, first_name="Sara")
    all_users = store.list_all()
    assert set(all_users.keys()) == {"1", "2"}


@pytest.mark.asyncio
async def test_is_bot_and_boolean_fields_round_trip(store):
    await store.record_start(1, first_name="SomeBot", is_bot=True)
    assert store.get(1)["is_bot"] is True


@pytest.mark.asyncio
async def test_sql_injection_payload_stored_as_inert_text(store):
    payload = "'; DROP TABLE pending_users; --"
    await store.record_start(1, first_name=payload, username=payload)
    info = store.get(1)
    assert info["first_name"] == payload
    assert len(store.list_all()) == 1
