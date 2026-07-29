"""
Standalone QA tool: a REAL Telegram USER account (via Telethon) that sends
test media to the real bot and clicks through its inline keyboards, to
verify the whole pipeline end-to-end against the live, running bot -- not
a stub/simulation like the tests in the main project's tests/ folder.

This is completely separate from worker.py and bot.py: it doesn't import
anything from the main project, doesn't share a session file with it, and
is never imported by them either. It's a black-box client, exactly like a
real user would be.

SETUP (one time):
    cd qa-userbot
    pip install -r requirements.txt
    cp .env.example .env
    # fill in USERBOT_API_ID / USERBOT_API_HASH (from https://my.telegram.org,
    # a real user account -- NOT the bot's own credentials), TARGET_BOT_USERNAME
    # (the real bot you're testing), and TEST_SOURCE_CHAT (a group/channel this
    # user account is a member of, where you manually upload sample media for
    # the script to pick up).

FIRST RUN:
    python userbot_test.py
This will prompt for your phone number, the login code Telegram just sent
you, and your 2FA password if you have one -- do this once, interactively,
in your own terminal (this sandbox can't do it: it has no network route to
Telegram's servers and can't receive your login code). After the first
successful login, a session file is saved next to this script and later
runs are non-interactive.

USAGE:
    python userbot_test.py                  # run every scenario below
    python userbot_test.py video_quality_360  # run just one scenario by name

WHAT IT DOES, per scenario:
    1. Looks through TEST_SOURCE_CHAT for a not-yet-used message whose
       media matches the scenario (a photo, a video, an archive, ...).
    2. Downloads it, then sends it fresh (not forwarded) to TARGET_BOT_USERNAME
       -- exactly like a real user attaching a file.
    3. Waits for the bot to respond OR to edit its last message in place
       (bot.py's callback handlers almost always edit the SAME message
       step-by-step -- e.g. quality keyboard -> options keyboard -> "sent
       for processing" is three edits of one message, not three separate
       messages -- so this script watches for both, not just brand-new
       message ids; see scenario_engine.message_snapshot). Whenever the
       current state has an inline keyboard, it clicks through the
       scenario's ordered list of button texts as they become available.
    4. Prints every bot message/edit and every click to the terminal as it
       happens, so you can watch the whole exchange live and/or scroll
       back through it afterward. This script does NOT try to auto-verify
       that the final delivered file is correct (e.g. that a "voice only"
       request really produced a voice note) -- open the TARGET_BOT_USERNAME
       chat yourself and look at what actually arrived; that's still the
       real pass/fail check.

Adding a new scenario: add an entry to SCENARIOS below with a media filter
(a function taking a Telethon Message, returning True/False) and an
ordered list of button texts to click as the flow progresses. Copy the
exact button text out of bot.py rather than re-typing it by hand --
that's the #1 way a scenario silently stops matching anything.
"""
import asyncio
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from telethon import TelegramClient

from scenario_engine import next_click_target, message_snapshot

load_dotenv()

API_ID = int(os.getenv("USERBOT_API_ID", "0") or "0")
API_HASH = os.getenv("USERBOT_API_HASH", "")
SESSION_NAME = os.getenv("USERBOT_SESSION_NAME", "qa_userbot_session")
TARGET_BOT = os.getenv("TARGET_BOT_USERNAME", "")
SOURCE_CHAT = os.getenv("TEST_SOURCE_CHAT", "")

DOWNLOAD_DIR = Path(__file__).parent / "downloads"
STEP_TIMEOUT_SECONDS = 60   # how long to wait for the bot's next message/edit
                            # (generous: real ffmpeg conversion isn't instant)
POLL_INTERVAL_SECONDS = 2
MAX_STEPS = 15              # safety cap so a stuck flow doesn't loop forever

# scenario name -> (media filter, ordered button-text steps to click).
# Button texts copied verbatim from bot.py -- keep them in sync if bot.py's
# wording ever changes, or the scenario will silently stop clicking anything.
SCENARIOS = {
    "photo_watermark": (
        lambda m: m.photo is not None,
        ["💧 روی این عکس واترمارک بزن"],
    ),
    "video_quality_360": (
        lambda m: m.video is not None,
        ["360p", "✅ آپلود کن"],
    ),
    "video_voice_only": (
        lambda m: m.video is not None,
        ["🎙 وویس", "✅ آپلود کن"],
    ),
    "video_collage_only": (
        lambda m: m.video is not None,
        ["🖼 فقط کولاژ تامبنیل", "✅ آپلود کن"],
    ),
    "archive_mixed_media": (
        lambda m: m.document is not None
        and bool(m.file and m.file.name)
        and m.file.name.lower().endswith((".zip", ".rar", ".7z")),
        ["✅ آپلود کن"],
    ),
}


async def resolve_source_chat(client, source):
    """Resolve TEST_SOURCE_CHAT into an entity Telethon can use.

    Plain @usernames resolve directly via get_entity. A numeric chat id
    for a PRIVATE group/channel often can't be resolved cold that way
    (Telethon needs to already know about the chat via its dialog cache),
    so as a fallback we search this account's own dialog list for a
    matching id -- which works as long as the account has opened that
    chat at least once (true by definition here, since it must be a
    member to read/download the test media in the first place).
    """
    try:
        chat_id = int(source)
    except ValueError:
        return await client.get_entity(source)

    try:
        return await client.get_entity(chat_id)
    except ValueError:
        pass

    print("Looking for the private channel/group in dialogs...")
    async for dialog in client.iter_dialogs():
        if dialog.id == chat_id:
            print(f"Found: {dialog.name} (id={dialog.id})")
            return dialog.entity

    raise ValueError(
        f"Cannot find chat {chat_id}. Make sure this user account is a "
        "member of it and has opened it at least once."
    )


async def find_source_media(client, media_filter, already_used_ids, source_entity):
    async for msg in client.iter_messages(source_entity, limit=200):
        if msg.id in already_used_ids:
            continue
        if msg.media and media_filter(msg):
            return msg
    return None


async def wait_for_bot_update(client, last_new_id, watch_msg_id, watch_snapshot, timeout=STEP_TIMEOUT_SECONDS):
    """Wait for either of two things, whichever happens first:
      - a brand-new message from the bot (id > last_new_id), or
      - the currently-watched message (`watch_msg_id`) being edited in
        place -- its (text, button-labels) fingerprint differs from
        `watch_snapshot`.
    Returns (message, is_new) or (None, None) on timeout. Polling both is
    necessary because this bot mixes the two: most inline-flow steps edit
    the same message, but the final delivered result (and some
    progress/info notices from the worker via the bridge) arrive as new
    messages.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        new_msgs = await client.get_messages(TARGET_BOT, min_id=last_new_id, limit=1)
        if new_msgs:
            return new_msgs[0], True

        if watch_msg_id is not None:
            current = await client.get_messages(TARGET_BOT, ids=watch_msg_id)
            if current is not None and message_snapshot(current) != watch_snapshot:
                return current, False

        await asyncio.sleep(POLL_INTERVAL_SECONDS)
    return None, None


async def run_scenario(client, name, media_filter, click_sequence, used_ids, source_entity):
    print(f"\n=== Scenario: {name} ===")

    src_msg = await find_source_media(client, media_filter, used_ids, source_entity)
    if src_msg is None:
        print("  SKIP — no matching, not-yet-used test media found in the source chat.")
        return
    used_ids.add(src_msg.id)

    DOWNLOAD_DIR.mkdir(exist_ok=True)
    local_path = await client.download_media(src_msg, file=f"{DOWNLOAD_DIR}/")
    print(f"  Downloaded test media -> {local_path}")

    sent = await client.send_file(TARGET_BOT, local_path, caption=f"[qa-test:{name}]")
    print(f"  Sent to {TARGET_BOT} (message id {sent.id})")

    remaining = list(click_sequence)
    last_new_id = sent.id
    watch_msg_id = None
    watch_snapshot = None

    for _ in range(MAX_STEPS):
        reply, is_new = await wait_for_bot_update(client, last_new_id, watch_msg_id, watch_snapshot)
        if reply is None:
            print("  (no further response/update within timeout — assuming the flow finished)")
            break

        if is_new:
            last_new_id = reply.id

        media_note = " [+ media attached]" if reply.media else ""
        edit_note = "" if is_new else " (edited in place)"
        print(f"  Bot{edit_note}: {reply.text!r}{media_note}")

        # Keep watching THIS message going forward by default -- if it's
        # about to be edited again (e.g. quality keyboard -> options
        # keyboard), we need its pre-edit snapshot to detect that.
        watch_msg_id = reply.id
        watch_snapshot = message_snapshot(reply)

        if not remaining:
            continue  # nothing left to click; keep observing until it goes quiet

        idx, button = next_click_target(remaining, reply)
        if button is None:
            if reply.buttons:
                labels = [b.text for row in reply.buttons for b in row]
                print(f"    (keyboard shown, but none of {remaining} matched. Buttons were: {labels})")
            continue

        await button.click()
        clicked_text = remaining.pop(idx)
        print(f"    Clicked: {clicked_text!r}")

    print(f"  Done with {name}. Check the {TARGET_BOT} chat yourself to confirm the delivered result is correct.")


async def main():
    missing = [
        var for var, val in [
            ("USERBOT_API_ID", API_ID),
            ("USERBOT_API_HASH", API_HASH),
            ("TARGET_BOT_USERNAME", TARGET_BOT),
            ("TEST_SOURCE_CHAT", SOURCE_CHAT),
        ] if not val
    ]
    if missing:
        sys.exit(
            "Missing config: " + ", ".join(missing) + ".\n"
            "Copy .env.example to .env in this folder and fill these in."
        )

    client = TelegramClient(SESSION_NAME, API_ID, API_HASH)
    await client.start()  # first run: prompts for phone/code/2FA interactively

    source_entity = await resolve_source_chat(client, SOURCE_CHAT)
    print(f"Using source chat: {getattr(source_entity, 'title', source_entity)}")

    only = sys.argv[1] if len(sys.argv) > 1 else None
    if only and only not in SCENARIOS:
        sys.exit(f"Unknown scenario {only!r}. Known: {', '.join(SCENARIOS)}")

    used_ids = set()
    for name, (media_filter, click_sequence) in SCENARIOS.items():
        if only and name != only:
            continue
        await run_scenario(client, name, media_filter, click_sequence, used_ids, source_entity)

    await client.disconnect()
    print("\nAll requested scenarios finished. Review the bot chat directly for the final verdict on each.")


if __name__ == "__main__":
    asyncio.run(main())
