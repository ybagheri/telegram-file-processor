"""
Tests for the blocking layer added to utils/access_control.py: a block
always wins over is_authorized(), regardless of tier/registration
status. The access_store side of is_authorized is already covered
elsewhere; these focus on the new blocked_user_store interaction.
"""

import pytest

from config import Telegram

import utils.access_control as access_control_module

from services.blocked_user_store import BlockedUserStore
from utils.access_control import is_authorized, is_blocked, not_authorized_text


@pytest.fixture
def blocked_store(tmp_path, monkeypatch):
    store = BlockedUserStore(tmp_path / "blocked.db")
    monkeypatch.setattr(access_control_module, "blocked_user_store", store)
    return store


def test_unblocked_user_is_not_blocked(blocked_store, monkeypatch):
    monkeypatch.setattr(Telegram, "ADMIN_IDS", [111])
    assert is_blocked(222) is False


@pytest.mark.asyncio
async def test_blocked_user_is_blocked(blocked_store, monkeypatch):
    monkeypatch.setattr(Telegram, "ADMIN_IDS", [111])
    await blocked_store.block(222)

    assert is_blocked(222) is True


@pytest.mark.asyncio
async def test_admin_can_never_be_blocked_even_if_a_stale_record_exists(blocked_store, monkeypatch):
    monkeypatch.setattr(Telegram, "ADMIN_IDS", [111])
    await blocked_store.block(111)  # e.g. left over from before they became admin

    assert is_blocked(111) is False


@pytest.mark.asyncio
async def test_blocked_user_fails_is_authorized_even_with_no_admins_configured(
    blocked_store, monkeypatch
):
    # With ADMIN_IDS empty, is_authorized normally lets everyone through
    # (access control effectively off) — a block must still override that.
    monkeypatch.setattr(Telegram, "ADMIN_IDS", [])
    await blocked_store.block(222)

    assert is_authorized(222) is False


@pytest.mark.asyncio
async def test_blocked_user_fails_is_authorized_even_if_registered(
    blocked_store, monkeypatch
):
    monkeypatch.setattr(Telegram, "ADMIN_IDS", [111])
    await blocked_store.block(222)

    class _AlwaysAuthorized:
        def is_authorized(self, user_id):
            return True

    monkeypatch.setattr(access_control_module, "access_store", _AlwaysAuthorized())

    assert is_authorized(222) is False


@pytest.mark.asyncio
async def test_not_authorized_text_is_distinct_for_blocked_vs_unregistered(
    blocked_store, monkeypatch
):
    monkeypatch.setattr(Telegram, "ADMIN_IDS", [111])
    monkeypatch.setattr(Telegram, "ADMIN_CONTACT_USERNAME", "@support")

    class _NeverAuthorized:
        def is_authorized(self, user_id):
            return False

        def get(self, user_id):
            return None

    monkeypatch.setattr(access_control_module, "access_store", _NeverAuthorized())

    unregistered_text = not_authorized_text(222)

    await blocked_store.block(222)
    blocked_text = not_authorized_text(222)

    assert blocked_text != unregistered_text
    assert "مسدود" in blocked_text
