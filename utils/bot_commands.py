"""
The bot's "/" command menu, registered via the Bot API's setMyCommands
on startup (bot.py's main(), before polling begins).

Kept in exactly one place so the Telegram native menu and README's
command table can't drift apart — README's table is REQUIRED maintenance
for every future command addition/change (treat it as part of the change
itself, per CLAUDE.md's conventions).

Scopes: regular users get PUBLIC_COMMANDS via BotCommandScopeDefault;
each configured admin chat gets ADMIN_COMMANDS *plus* the public set via
BotCommandScopeChat (a more-specific scope fully replaces the default
menu for that chat, so the public commands must be repeated there).
Admin commands are therefore never visible in a regular user's menu.
"""
from __future__ import annotations

from aiogram.types import BotCommand


def public_commands() -> list[BotCommand]:
    """Commands available to everyone (BotCommandScopeDefault)."""

    return [
        BotCommand(command="start", description="شروع کار با ربات"),
        BotCommand(
            command="settings",
            description="تنظیمات من (کیفیت، واترمارک، مقصد ارسال و ...)",
        ),
        BotCommand(command="cancel", description="لغو عملیات جاری"),
    ]


def admin_commands() -> list[BotCommand]:
    """Admin-only commands — registered per admin chat via
    BotCommandScopeChat, never in the default scope."""

    return [
        BotCommand(command="admin", description="پنل مدیریت کاربران"),
        BotCommand(
            command="status",
            description="وضعیت Worker (آیا پردازشگر زنده است؟)",
        ),
    ]


def admin_chat_commands() -> list[BotCommand]:
    """The full menu shown in an admin's private chat: admin commands
    first, then the public ones (the chat scope replaces the default
    menu entirely, so the public set must be repeated)."""

    return admin_commands() + public_commands()
