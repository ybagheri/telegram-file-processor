"""
/settings: the command, every s:*/sq:*/slogopos:*/starget:* callback, and
the settings_* awaited-input states. Extracted out of bot.py (phase D,
step 2 of the module split — see CLAUDE.md's change log) as an aiogram 3
Router, registered from bot.py via `dp.include_router(router)`.

`handle_settings_awaited_input()` is called from bot.py's top-level
`handle_awaited_input()` dispatcher for any state starting with
"settings_" — not a `@router` handler itself, same pattern as
`handlers/admin.py`'s `handle_admin_awaited_input()`.
"""
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from config import Paths
from keyboards.constants import POSITION_LABELS_FA
from keyboards.settings import logo_position_keyboard, settings_text_and_keyboard
from services.settings_store import settings_store
from services.target_resolver import resolve_target
from services.telegram import telegram_service
from utils.access_control import is_authorized, not_authorized_text, track_pending_user_if_needed
from state import awaiting_state

router = Router(name="settings")
bot = telegram_service.bot


# ======================================================================
# Command
# ======================================================================

@router.message(Command("settings"), F.chat.type == "private")
async def settings_command(message: Message):
    await track_pending_user_if_needed(message)
    if not is_authorized(message.from_user.id):
        await message.answer(not_authorized_text(message.from_user.id))
        return
    await message.answer(**settings_text_and_keyboard(message.from_user.id))


# ======================================================================
# Global settings callbacks
# ======================================================================

@router.callback_query(F.data == "s:quality")
async def settings_quality(callback: CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="144p", callback_data="sq:144"),
         InlineKeyboardButton(text="240p", callback_data="sq:240")],
        [InlineKeyboardButton(text="360p", callback_data="sq:360"),
         InlineKeyboardButton(text="480p", callback_data="sq:480")],
        [InlineKeyboardButton(text="720p", callback_data="sq:720")],
    ])
    await callback.message.edit_text("کیفیت پیش‌فرض جدید را انتخاب کنید:", reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("sq:"))
async def settings_quality_pick(callback: CallbackQuery):
    value = callback.data.split(":", 1)[1]
    await settings_store.update(callback.from_user.id, quality=value)
    await callback.message.edit_text(**settings_text_and_keyboard(callback.from_user.id))
    await callback.answer("بروزرسانی شد")


@router.callback_query(F.data == "s:watermark")
async def settings_watermark(callback: CallbackQuery):
    s = settings_store.get(callback.from_user.id)
    await settings_store.update(callback.from_user.id, watermark=not s["watermark"])
    await callback.message.edit_text(**settings_text_and_keyboard(callback.from_user.id))
    await callback.answer("بروزرسانی شد")


@router.callback_query(F.data == "s:upload_as")
async def settings_upload_as(callback: CallbackQuery):
    s = settings_store.get(callback.from_user.id)
    new_val = "video" if s["upload_as"] == "document" else "document"
    await settings_store.update(callback.from_user.id, upload_as=new_val)
    await callback.message.edit_text(**settings_text_and_keyboard(callback.from_user.id))
    await callback.answer("بروزرسانی شد")


@router.callback_query(F.data == "s:sortmode")
async def settings_sort_mode(callback: CallbackQuery):
    s = settings_store.get(callback.from_user.id)
    new_val = "date" if s["sort_mode"] == "name" else "name"
    await settings_store.update(callback.from_user.id, sort_mode=new_val)
    await callback.message.edit_text(**settings_text_and_keyboard(callback.from_user.id))
    await callback.answer("بروزرسانی شد")


@router.callback_query(F.data == "s:sortorder")
async def settings_sort_order(callback: CallbackQuery):
    s = settings_store.get(callback.from_user.id)
    new_val = "desc" if s["sort_order"] == "asc" else "asc"
    await settings_store.update(callback.from_user.id, sort_order=new_val)
    await callback.message.edit_text(**settings_text_and_keyboard(callback.from_user.id))
    await callback.answer("بروزرسانی شد")


@router.callback_query(F.data == "s:exclude")
async def settings_exclude(callback: CallbackQuery):
    awaiting_state[callback.from_user.id] = "settings_exclude"
    await callback.message.answer(
        "متنی که می‌خواهید از نام همه‌ی فایل‌ها حذف شود را بفرستید "
        "(مثلاً یک تبلیغ یا واترمارک متنی مثل «[www.site.com]»).\n"
        "برای غیرفعال کردن این قابلیت، کلمه‌ی «حذف» را بفرستید."
    )
    await callback.answer()


@router.callback_query(F.data == "s:artist")
async def settings_artist(callback: CallbackQuery):
    awaiting_state[callback.from_user.id] = "settings_artist"
    await callback.message.answer("نام خواننده/هنرمند پیش‌فرض را بفرستید:")
    await callback.answer()


@router.callback_query(F.data == "s:logo")
async def settings_logo(callback: CallbackQuery):
    awaiting_state[callback.from_user.id] = "settings_logo"
    await callback.message.answer("تصویر لوگو را به‌صورت عکس بفرستید:")
    await callback.answer()


@router.callback_query(F.data == "s:logopos")
async def settings_logo_position(callback: CallbackQuery):
    current = settings_store.get(callback.from_user.id)["logo_position"]
    await callback.message.edit_text(
        "📍 محل قرارگیری واترمارک روی ویدیو را انتخاب کنید:",
        reply_markup=logo_position_keyboard(current),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("slogopos:"))
async def settings_logo_position_pick(callback: CallbackQuery):
    position = callback.data.split(":", 1)[1]
    await settings_store.update(callback.from_user.id, logo_position=position)
    await callback.message.edit_text(**settings_text_and_keyboard(callback.from_user.id))
    await callback.answer(f"موقعیت: {POSITION_LABELS_FA.get(position, position)}")


@router.callback_query(F.data == "s:target")
async def settings_target(callback: CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👤 ارسال به خودم", callback_data="starget:me")],
        [InlineKeyboardButton(text="➕ تنظیم کانال/گروه جدید", callback_data="starget:new")],
    ])
    await callback.message.edit_text(
        "مقصد پیش‌فرض ارسال فایل‌ها را انتخاب کنید:",
        reply_markup=kb,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("starget:"))
async def settings_target_pick(callback: CallbackQuery):
    choice = callback.data.split(":", 1)[1]

    if choice == "me":
        await settings_store.update(callback.from_user.id, target_chat_id=0, target_label="خودم")
        await callback.message.edit_text(**settings_text_and_keyboard(callback.from_user.id))
        await callback.answer("بروزرسانی شد")
        return

    awaiting_state[callback.from_user.id] = "settings_target"
    await callback.message.answer(
        "یک پیام از چت مقصد برای من فوروارد کنید، @username یا آیدی عددی آن را بفرستید.\n"
        "⚠️ ربات باید عضو آن گروه/کانال باشد.\n"
        "برای انصراف /cancel را بفرستید."
    )
    await callback.answer()


@router.callback_query(F.data == "s:caption")
async def settings_caption(callback: CallbackQuery):
    awaiting_state[callback.from_user.id] = "settings_caption"
    await callback.message.answer(
        "متن کپشن پیش‌فرض برای مدیاهای تحویلی را بفرستید.\n"
        "برای حذف کپشن (بدون کپشن)، کلمه‌ی «حذف» را بفرستید."
    )
    await callback.answer()


# ======================================================================
# Awaited-input states (settings_*) — called from bot.py's handle_awaited_input
# ======================================================================

async def handle_settings_awaited_input(message: Message, state: str) -> bool:
    """Handles every state prefixed "settings_". Returns True if handled
    (whether it advanced or just re-prompted), False if this state isn't
    one of the settings ones at all."""

    user_id = message.from_user.id

    if state == "settings_artist":
        if not message.text:
            return False
        await settings_store.update(user_id, artist=message.text.strip())
        awaiting_state.pop(user_id, None)
        await message.answer(**settings_text_and_keyboard(user_id))
        return True

    if state == "settings_logo":
        if not message.photo:
            return False
        path = Paths.CONFIG / "logos" / f"{user_id}.jpg"
        path.parent.mkdir(parents=True, exist_ok=True)
        await bot.download(message.photo[-1], destination=path)
        await settings_store.update(user_id, logo_path=str(path))
        awaiting_state.pop(user_id, None)
        current = settings_store.get(user_id)["logo_position"]
        await message.answer(
            "✅ لوگو ذخیره شد.\n"
            "حالا محل قرارگیری واترمارک روی ویدیو را انتخاب کنید:",
            reply_markup=logo_position_keyboard(current),
        )
        return True

    if state == "settings_target":
        chat_id, label = await resolve_target(message)
        if chat_id is None:
            if message.document or message.video or message.audio:
                # They clearly want to work on a new file now, not finish
                # setting a target — don't leave them stuck waiting.
                awaiting_state.pop(user_id, None)
                await message.answer("⏹ تنظیم مقصد لغو شد؛ این فایل را به‌عنوان کار جدید در نظر می‌گیرم.")
                return False
            await message.answer(
                "چت را نشناختم. یک پیام از آن فوروارد کنید، @username یا آیدی عددی چت را بفرستید.\n"
                "برای انصراف /cancel را بفرستید."
            )
            return True
        await settings_store.update(user_id, target_chat_id=chat_id, target_label=label)
        awaiting_state.pop(user_id, None)
        await message.answer(**settings_text_and_keyboard(user_id))
        return True

    if state == "settings_caption":
        if not message.text:
            return False
        new_caption = "" if message.text.strip() in ("حذف", "-", "none", "None") else message.text
        await settings_store.update(user_id, media_caption=new_caption)
        awaiting_state.pop(user_id, None)
        await message.answer(**settings_text_and_keyboard(user_id))
        return True

    if state == "settings_exclude":
        if not message.text:
            return False
        new_value = "" if message.text.strip() in ("حذف", "-", "none", "None") else message.text.strip()
        await settings_store.update(user_id, exclude_text=new_value)
        awaiting_state.pop(user_id, None)
        await message.answer(**settings_text_and_keyboard(user_id))
        return True

    return False
