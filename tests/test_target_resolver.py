"""
Tests for services/target_resolver.py::resolve_target — specifically the
admin-membership check: resolving *a* chat isn't enough, the requesting
user must actually be an admin/owner of it (see CLAUDE.md's change log
for the vulnerability this closes: without this check, any user who
merely knew another chat's id/@username could redirect their own
uploads into it, as long as the bot happened to already be a member).
"""
from types import SimpleNamespace

import pytest

from services.target_resolver import resolve_target


def _message(text=None, forward_from_chat=None, user_id=555):
    return SimpleNamespace(
        text=text,
        forward_from_chat=forward_from_chat,
        from_user=SimpleNamespace(id=user_id),
    )


def _chat(chat_id=-100123, title="Some Group", username=None):
    return SimpleNamespace(id=chat_id, title=title, username=username)


def _member(status):
    return SimpleNamespace(status=status)


@pytest.fixture
def target_resolver_bot(monkeypatch):
    """A fake bot with get_chat/get_chat_member the tests fully control."""

    import services.target_resolver as target_resolver_module

    fake = SimpleNamespace(
        get_chat=None,
        get_chat_member=None,
    )

    monkeypatch.setattr(target_resolver_module, "bot", fake)

    return fake


# ======================================================================
# The core security check: admin membership
# ======================================================================


@pytest.mark.parametrize("status", ["administrator", "creator"])
async def test_admin_or_creator_is_accepted(target_resolver_bot, status):

    async def get_chat(_):
        return _chat()

    async def get_chat_member(chat_id, user_id):
        return _member(status)

    target_resolver_bot.get_chat = get_chat
    target_resolver_bot.get_chat_member = get_chat_member

    chat_id, label, error = await resolve_target(_message(text="@somegroup"))

    assert error is None
    assert chat_id == -100123
    assert label == "Some Group"


@pytest.mark.parametrize("status", ["member", "restricted", "left", "kicked"])
async def test_non_admin_member_is_rejected(target_resolver_bot, status):

    async def get_chat(_):
        return _chat()

    async def get_chat_member(chat_id, user_id):
        return _member(status)

    target_resolver_bot.get_chat = get_chat
    target_resolver_bot.get_chat_member = get_chat_member

    chat_id, label, error = await resolve_target(_message(text="@somegroup"))

    assert chat_id is None
    assert label is None
    assert error == "not_admin"


async def test_membership_check_failure_fails_closed(target_resolver_bot):
    """If we can't even verify membership, don't guess — reject."""

    async def get_chat(_):
        return _chat()

    async def get_chat_member(chat_id, user_id):
        raise RuntimeError("Telegram API error")

    target_resolver_bot.get_chat = get_chat
    target_resolver_bot.get_chat_member = get_chat_member

    chat_id, label, error = await resolve_target(_message(text="@somegroup"))

    assert chat_id is None
    assert error == "not_admin"


async def test_admin_check_applies_to_a_forwarded_message_too(target_resolver_bot):
    """The forward_from_chat path used to skip verification entirely —
    the exact hole a user setting someone else's group as their own
    target could exploit if they got hold of a forwarded message from
    it, e.g. shared by the actual admin elsewhere."""

    async def get_chat_member(chat_id, user_id):
        return _member("member")  # not an admin

    target_resolver_bot.get_chat_member = get_chat_member

    message = _message(forward_from_chat=_chat(chat_id=-100999, title="Someone Else's Group"))

    chat_id, label, error = await resolve_target(message)

    assert chat_id is None
    assert error == "not_admin"


async def test_admin_forwarding_their_own_group_still_works(target_resolver_bot):

    async def get_chat_member(chat_id, user_id):
        return _member("administrator")

    target_resolver_bot.get_chat_member = get_chat_member

    message = _message(forward_from_chat=_chat(chat_id=-100999, title="My Group"))

    chat_id, label, error = await resolve_target(message)

    assert error is None
    assert chat_id == -100999
    assert label == "My Group"


# ======================================================================
# Chat resolution itself (unchanged behavior, still needs to work)
# ======================================================================


async def test_unresolvable_text_returns_not_found(target_resolver_bot):

    async def get_chat(_):
        raise RuntimeError("chat not found")

    target_resolver_bot.get_chat = get_chat

    chat_id, label, error = await resolve_target(_message(text="@doesnotexist"))

    assert chat_id is None
    assert label is None
    assert error == "not_found"


async def test_no_text_and_no_forward_returns_not_found(target_resolver_bot):

    chat_id, label, error = await resolve_target(_message())

    assert chat_id is None
    assert error == "not_found"


async def test_falls_back_to_a_raw_numeric_chat_id(target_resolver_bot):

    calls = []

    async def get_chat(arg):
        calls.append(arg)
        if isinstance(arg, str):
            raise RuntimeError("not a username")
        return _chat(chat_id=arg, title="Private Group", username=None)

    async def get_chat_member(chat_id, user_id):
        return _member("creator")

    target_resolver_bot.get_chat = get_chat
    target_resolver_bot.get_chat_member = get_chat_member

    chat_id, label, error = await resolve_target(_message(text="-100123456789"))

    assert error is None
    assert chat_id == -100123456789
    assert label == "Private Group"
