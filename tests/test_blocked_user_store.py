import pytest

from services.blocked_user_store import BlockedUserStore


@pytest.fixture
def store(tmp_path):
    return BlockedUserStore(tmp_path / "blocked_users.db")


def test_unknown_user_is_not_blocked(store):
    assert store.is_blocked(999999) is False
    assert store.get(999999) is None


@pytest.mark.asyncio
async def test_block_makes_user_blocked(store):
    await store.block(1, blocked_by=100, note="spamming")

    assert store.is_blocked(1) is True
    info = store.get(1)
    assert info["blocked_by"] == 100
    assert info["note"] == "spamming"
    assert info["blocked_at"] is not None


@pytest.mark.asyncio
async def test_blocking_twice_updates_rather_than_duplicates(store):
    await store.block(1, blocked_by=100, note="first reason")
    await store.block(1, blocked_by=200, note="second reason")

    info = store.get(1)
    assert info["blocked_by"] == 200
    assert info["note"] == "second reason"
    assert len(store.list_all()) == 1


@pytest.mark.asyncio
async def test_unblock_removes_the_block(store):
    await store.block(1)
    assert await store.unblock(1) is True
    assert store.is_blocked(1) is False


@pytest.mark.asyncio
async def test_unblocking_a_non_blocked_user_returns_false(store):
    assert await store.unblock(999999) is False


@pytest.mark.asyncio
async def test_list_all_returns_every_blocked_user(store):
    await store.block(1)
    await store.block(2)

    assert set(store.list_all().keys()) == {"1", "2"}


@pytest.mark.asyncio
async def test_block_and_unblock_are_independent_per_user(store):
    await store.block(1)
    await store.block(2)
    await store.unblock(1)

    assert store.is_blocked(1) is False
    assert store.is_blocked(2) is True
