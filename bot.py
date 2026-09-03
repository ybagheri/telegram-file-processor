"""
Entry point: creates the Dispatcher, registers every feature Router, and
runs polling. This file used to contain the entire bot (~2000 lines) —
see CLAUDE.md's change log (phases 7a-7g) for the full story of how it
was split into utils/, keyboards/, models/, state.py, services/, and
handlers/. What's left here is deliberately thin.

Router registration ORDER matters: every router with a specific filter
(Command(...), F.data == "...", a particular chat id) must be included
BEFORE handlers/core.py's `catchall_router`, whose `handle_private_message`
matches ANY private message with no further discrimination. aiogram
checks a router's own directly-decorated handlers before descending into
any included sub-router, but among sibling sub-routers, inclusion order
is respected — so the catch-all must always go last, or it will swallow
messages meant for a more specific router first. See the phase-7d change
log entry for the real bug this taught us (moving /admin onto its own
router initially broke it, for exactly this reason), and
tests/test_router_wiring.py for the regression tests that guard against
it recurring.
"""
import asyncio

from aiogram import Dispatcher as AiogramDispatcher

from aiogram.types import BotCommandScopeChat, BotCommandScopeDefault

from core.logger import get_logger
from services.access_store import access_store
from services.settings_store import settings_store
from services.telegram import telegram_service
from config import Telegram

from models.pending_file import PendingFile
from models.pending_photo import PendingPhoto
from state import (
    pending_files,
    pending_photos,
    awaiting_state,
    admin_flow,
    pending_passwords,
    job_folder_links,
)
from utils.access_control import (
    is_admin,
    compute_expiry,
    format_expiry,
    _extract_target_from_message,
    _user_display,
    notify_admins_of_new_pending_user,
    track_pending_user_if_needed,
)

from handlers.admin import (
    router as admin_router,
    admin_command,
    admin_panel_back,
    admin_add_user,
    admin_renew_user,
    admin_toggle_user,
    admin_delete_user,
    admin_toggle_confirmed,
    admin_toggle_cancelled,
    admin_delete_confirmed,
    admin_delete_cancelled,
    admin_duration_selected,
    admin_list_users,
    admin_manage_user,
    admin_manage_renew,
    admin_manage_toggle,
    admin_manage_delete,
    admin_list_pending,
    admin_block_user,
    admin_unblock_user,
    admin_list_blocked,
    admin_block_confirmed,
    admin_unblock_confirmed,
    admin_broadcast_start,
    admin_broadcast_confirm,
    admin_broadcast_cancelled,
    admin_promo_menu,
    admin_promo_set,
    admin_promo_preview,
    admin_promo_toggle,
    admin_promo_delete,
    admin_stats,
    handle_admin_awaited_input,
)
from handlers.settings import (
    router as settings_router,
    settings_command,
    settings_quality,
    settings_quality_pick,
    settings_watermark,
    settings_upload_as,
    settings_sort_mode,
    settings_sort_order,
    settings_exclude,
    settings_artist,
    settings_logo,
    settings_logo_position,
    settings_logo_position_pick,
    settings_target,
    settings_target_pick,
    settings_caption,
    handle_settings_awaited_input,
)
from handlers.files import (
    router as files_router,
    quality_pick,
    options_action,
    noop_callback,
    target_pick,
    finalize_job,
    handle_file_awaited_input,
)
from handlers.photo import (
    router as photo_router,
    handle_incoming_photo,
    photo_watermark_action,
    apply_watermark_to_photo,
)
from handlers.bridge import router as bridge_router, handle_bridge_message
from handlers.core import (
    router as core_router,
    catchall_router,
    start,
    cancel_command,
    handle_private_message,
    handle_awaited_input,
)
from services.expiry_reminder import (
    check_and_send_expiry_reminders,
    expiry_reminder_loop,
)
from utils.bot_commands import (
    admin_chat_commands,
    public_commands,
)

logger = get_logger(__name__)

bot = telegram_service.bot
dp = AiogramDispatcher()

# Specific-filter routers first, catch-all last — see the module
# docstring above for why the order matters.
dp.include_router(admin_router)
dp.include_router(settings_router)
dp.include_router(files_router)
dp.include_router(photo_router)
dp.include_router(bridge_router)
dp.include_router(core_router)
dp.include_router(catchall_router)


async def register_command_menus(bot) -> None:
    """Registers the "/" command menus via setMyCommands: the public set
    for everyone (BotCommandScopeDefault), and the admin set + public
    set per configured admin chat (BotCommandScopeChat), so regular
    users never see admin commands in their menu. Deliberately
    best-effort: a Telegram hiccup here must not keep the bot from
    starting (polling works fine without a registered menu)."""

    try:
        await bot.set_my_commands(
            public_commands(),
            scope=BotCommandScopeDefault(),
        )
        logger.info("Registered public command menu")
    except Exception:
        logger.exception("Failed to register the public command menu")

    for admin_id in Telegram.ADMIN_IDS:

        try:
            await bot.set_my_commands(
                admin_chat_commands(),
                scope=BotCommandScopeChat(
                    chat_id=admin_id,
                ),
            )
            logger.info("Registered admin command menu for %s", admin_id)
        except Exception:
            logger.exception(
                "Failed to register the admin command menu for %s",
                admin_id,
            )


async def main():
    logger.info("Bot started")
    asyncio.create_task(expiry_reminder_loop())

    # Native "/" command menu — done BEFORE polling starts so the menus
    # are correct from the very first interaction.
    await register_command_menus(bot)

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
