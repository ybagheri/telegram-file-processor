"""
The bridge side: messages coming back from worker.py through the bridge
group chat. Extracted out of bot.py (phase D, step 4 of the module split
— see CLAUDE.md's change log) as an aiogram 3 Router.

Registration order relative to the other routers doesn't matter for
correctness here (unlike the private-chat routers) — this Router's filter
matches a specific chat id (`Telegram.GROUP_ID`), which is orthogonal to
every private-chat filter elsewhere, so there was never a catch-all-
precedence risk for this one.
"""
from html import escape as html_escape
from pathlib import Path
import time

from aiogram import F, Router
from aiogram.types import Message

from config import Telegram
from core.constants import MessageType
from core.logger import get_logger
from core.protocol import Protocol
from services.promo_post_store import promo_post_store
from services.settings_store import settings_store
from services.telegram import telegram_service
from state import job_folder_links, pending_passwords, worker_last_seen

router = Router(name="bridge")
logger = get_logger(__name__)
bot = telegram_service.bot


async def _resolve_message_link(chat_id: int, message_id: int) -> str:

    try:
        chat = await bot.get_chat(chat_id)
        if getattr(chat, "username", None):
            return f"https://t.me/{chat.username}/{message_id}"
    except Exception:
        pass

    internal = str(chat_id)
    internal = internal[4:] if internal.startswith("-100") else internal.lstrip("-")

    return f"https://t.me/c/{internal}/{message_id}"


async def _relay_report_to_admins(payload: dict):
    """DMs a worker-built failure report to every configured admin,
    following the same one-DM-per-admin pattern as
    notify_admins_of_new_pending_user(). A report is already fully
    sanitized and HTML-safe (core/error_reporting.py), so it goes out
    with parse_mode="HTML" as-is. One unreachable admin must never stop
    the others from getting the report — and a relay failure here must
    never take the bridge handler down, so every send is isolated."""

    report = payload.get("report", "")

    if not report:
        return

    for admin_id in Telegram.ADMIN_IDS:

        try:
            await telegram_service.send_text(
                admin_id,
                report,
                parse_mode="HTML",
            )
        except Exception:
            logger.exception(
                "Failed to deliver admin error report to admin %s",
                admin_id,
            )


@router.message(F.chat.id == Telegram.GROUP_ID)
async def handle_bridge_message(message: Message):

    raw = message.text or message.caption

    if not raw:
        return

    try:
        payload = Protocol.decode(raw)
    except Exception:
        return

    message_type = payload.get("type")
    user_id = payload.get("user_id")

    # Worker liveness ping — handled BEFORE the user_id check (heartbeats
    # carry no user_id by design) and never relayed to anyone: the only
    # effect is updating the "worker last seen" tracker that /status
    # reports from. It must never reach a user as a "result".
    if message_type == MessageType.HEARTBEAT.value:

        worker_last_seen["worker"] = time.time()

        return

    # Structured failure report for the ADMINS (core/error_reporting.py).
    # Handled BEFORE the user_id gate: a pre-Job failure (e.g. the media
    # message never fetched) has no user_id, and the report must still
    # reach the admins. Never relayed to the user.
    if message_type == MessageType.ADMIN_ERROR.value:

        await _relay_report_to_admins(payload)

        return

    if not user_id:
        return

    destination = payload.get("target_chat_id") or user_id

    if message_type == MessageType.RESULT.value:

        if message.document or message.video or message.audio or message.voice or message.photo:

            # copyMessage keeps the ORIGINAL caption when caption is not
            # given at all, so we must always pass an explicit string here
            # — otherwise the raw protocol JSON caption used for the
            # bridge would leak straight through to the user.
            files = payload.get("files") or []
            file_name = files[0] if files else ""
            name_without_ext = Path(file_name).stem if file_name else ""

            custom_caption = settings_store.get(user_id).get("media_caption") or ""

            caption_parts = [p for p in (name_without_ext, custom_caption) if p]
            user_caption = "\n\n".join(caption_parts)

            await telegram_service.copy_message_to(
                chat_id=destination,
                from_chat_id=message.chat.id,
                message_id=message.message_id,
                caption=user_caption,
            )

        return

    if message_type == MessageType.ERROR.value:

        await telegram_service.send_text(
            user_id,
            f"❌ خطا: {payload.get('message', 'پردازش ناموفق بود.')}",
        )

        return

    if message_type == MessageType.PASSWORD_REQUEST.value:

        job_id = payload.get("job_id")
        filename = payload.get("filename", "")

        pending_passwords[user_id] = job_id

        await telegram_service.send_text(
            user_id,
            f"🔒 فایل «{filename}» رمزدار است. لطفاً رمز آن را ارسال کنید.",
        )

        return

    if message_type == MessageType.INFO.value:

        await telegram_service.send_text(
            destination,
            payload.get("message", ""),
        )

        return

    if message_type == MessageType.FOLDER.value:

        job_id = payload.get("job_id")
        folder = payload.get("folder", "")

        sent = await telegram_service.send_text(destination, f"📂 {folder}")

        if sent is not None:
            job_folder_links.setdefault(job_id, []).append((folder, sent.message_id))

        return

    if message_type == MessageType.DONE.value:

        job_id = payload.get("job_id")
        folders = job_folder_links.pop(job_id, None)

        # A TOC with clickable links only makes sense when the files were
        # delivered to a channel/group the user can link back into —
        # there's no public link for a private 1:1 chat with the bot.
        if folders and destination != user_id:

            lines = ["📑 <b>فهرست مطالب</b>\n"]

            for name, message_id in folders:
                url = await _resolve_message_link(destination, message_id)
                lines.append(f'📂 <a href="{url}">{html_escape(name)}</a>')

            try:
                await telegram_service.send_text(
                    destination,
                    "\n".join(lines),
                    parse_mode="HTML",
                )
            except Exception:
                logger.exception("Failed to send TOC for job %s", job_id)

        try:
            await telegram_service.send_text(
                user_id,
                "✅ همه‌ی فایل‌ها با موفقیت ارسال شدند.",
            )
        except Exception:
            logger.exception("Failed to send completion notice for job %s", job_id)

        post = promo_post_store.get()

        if post and post["enabled"]:
            try:
                await bot.copy_message(
                    user_id,
                    from_chat_id=post["source_chat_id"],
                    message_id=post["source_message_id"],
                )
            except Exception:
                # Best-effort advertising, never allowed to look like a
                # job failure to the user — most common cause is simply
                # the user having blocked the bot.
                logger.warning(
                    "Could not deliver the promo post to user %s after job %s",
                    user_id,
                    job_id,
                )

        return
