"""
Tests for the "/" command-menu registration (utils/bot_commands.py +
bot.py::register_command_menus). The Bot API is faked (set_my_commands
captured), so nothing touches Telegram.
"""

import pytest

import bot as bot_module

from aiogram.types import BotCommandScopeChat, BotCommandScopeDefault

from config import Telegram

from utils.bot_commands import (
    admin_chat_commands,
    admin_commands,
    public_commands,
)


def _commands_of(list_of_bot_commands):

    return [c.command for c in list_of_bot_commands]


# ======================================================================
# Command list definitions
# ======================================================================


def test_public_commands_are_the_expected_set():

    assert _commands_of(public_commands()) == ["start", "settings", "cancel"]


def test_admin_commands_are_the_expected_set():

    assert _commands_of(admin_commands()) == ["admin", "status"]


def test_admin_commands_never_leak_into_public_menu():

    assert not set(_commands_of(admin_commands())) & set(_commands_of(public_commands()))


def test_admin_chat_menu_is_superset_of_public_menu():

    menu = _commands_of(admin_chat_commands())

    for command in _commands_of(admin_commands()) + _commands_of(public_commands()):
        assert command in menu


def test_descriptions_are_non_empty():

    for command in public_commands() + admin_commands():
        assert command.description.strip()


# ======================================================================
# register_command_menus (set_my_commands captured, no Telegram)
# ======================================================================


class FakeBot:

    def __init__(self, fail_for_scopes: set | None = None):
        self.calls = []
        self.fail_for_scopes = fail_for_scopes or set()

    async def set_my_commands(self, commands, scope=None):
        scope_type = type(scope).__name__ if scope is not None else None

        if scope_type in self.fail_for_scopes:
            raise RuntimeError("telegram down")

        self.calls.append(
            (
                scope_type,
                scope.chat_id if isinstance(scope, BotCommandScopeChat) else None,
                [c.command for c in commands],
            )
        )


async def test_registers_public_menu_for_everyone(monkeypatch):

    fake_bot = FakeBot()

    await bot_module.register_command_menus(fake_bot)

    scope_types = [c[0] for c in fake_bot.calls]

    assert "BotCommandScopeDefault" in scope_types

    default_call = next(c for c in fake_bot.calls if c[0] == "BotCommandScopeDefault")

    assert default_call[2] == ["start", "settings", "cancel"]


async def test_registers_admin_menu_per_admin_chat(monkeypatch):

    # conftest.py sets ADMIN_IDS=111,222
    fake_bot = FakeBot()

    await bot_module.register_command_menus(fake_bot)

    admin_calls = [c for c in fake_bot.calls if c[0] == "BotCommandScopeChat"]

    assert sorted(c[1] for c in admin_calls) == sorted(Telegram.ADMIN_IDS)

    for _scope, _chat_id, commands in admin_calls:
        # Admins see admin commands AND the public ones (chat scope
        # replaces the default menu entirely).
        assert "admin" in commands
        assert "status" in commands
        assert "start" in commands


async def test_failure_does_not_stop_registration_or_raise(monkeypatch):

    fake_bot = FakeBot(fail_for_scopes={"BotCommandScopeDefault"})

    # Must not raise, and must still attempt the admin scopes.
    await bot_module.register_command_menus(fake_bot)

    assert any(c[0] == "BotCommandScopeChat" for c in fake_bot.calls)


async def test_one_failing_admin_scope_does_not_block_the_others(monkeypatch):

    fake_bot = FakeBot()

    original = fake_bot.set_my_commands

    async def flaky_set_my_commands(commands, scope=None):
        if isinstance(scope, BotCommandScopeChat) and scope.chat_id == Telegram.ADMIN_IDS[0]:
            raise RuntimeError("telegram hiccup")
        await original(commands, scope=scope)

    fake_bot.set_my_commands = flaky_set_my_commands

    await bot_module.register_command_menus(fake_bot)

    tried_chats = [c[1] for c in fake_bot.calls if c[0] == "BotCommandScopeChat"]

    # The first admin failed, the second still got its menu.
    assert Telegram.ADMIN_IDS[-1] in tried_chats
    assert Telegram.ADMIN_IDS[0] not in tried_chats