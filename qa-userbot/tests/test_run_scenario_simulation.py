"""
Simulates the exact bug that was reported: bot.py edits ONE message in
place through several steps (quality keyboard -> options keyboard ->
"sent for processing") and only sends a genuinely NEW message once, at the
very end, for the delivered result. This drives the real
`run_scenario`/`wait_for_bot_update` functions from userbot_test.py against
a fake Telegram client reproducing that exact pattern, proving the fix
works without a live Telegram connection.
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import userbot_test


class FakeButton:
    def __init__(self, text, on_click):
        self.text = text
        self._on_click = on_click

    async def click(self):
        await self._on_click()


class FakeMessage:
    def __init__(self, id, text, buttons, media=None):
        self.id = id
        self.text = text
        self.buttons = buttons
        self.media = media


class FakeBotServer:
    """Reproduces the real bot's actual behavior for the video-quality
    flow: one message gets edited twice in place (quality -> options ->
    "sent for processing"), then a brand-new message arrives later with
    the "delivered" result -- exactly the pattern that broke the original
    polling logic (which only watched for a higher message id)."""

    def __init__(self):
        self.messages = {}
        self._next_id = 0

    def _new_id(self):
        self._next_id += 1
        return self._next_id

    def receive_file(self):
        msg_id = self._new_id()
        self.messages[msg_id] = FakeMessage(
            id=msg_id,
            text="کیفیت را انتخاب کنید:",
            buttons=[
                [FakeButton("360p", self._make_quality_handler(msg_id))],
                [FakeButton("720p", self._make_quality_handler(msg_id))],
            ],
        )
        return msg_id

    def _make_quality_handler(self, msg_id):
        async def handler():
            # mirrors bot.py's quality_pick: edit_text on the SAME message
            self.messages[msg_id] = FakeMessage(
                id=msg_id,
                text="تنظیمات را بررسی کنید:",
                buttons=[[FakeButton("✅ آپلود کن", self._make_confirm_handler(msg_id))]],
            )
        return handler

    def _make_confirm_handler(self, msg_id):
        async def handler():
            # mirrors bot.py's finalize_job: edit_text again, no buttons
            self.messages[msg_id] = FakeMessage(
                id=msg_id, text="✅ فایل برای پردازش ارسال شد.", buttons=None,
            )
            # then the worker's result arrives as a genuinely NEW message
            result_id = self._new_id()
            self.messages[result_id] = FakeMessage(
                id=result_id, text=None, buttons=None, media="the-converted-video",
            )
        return handler


class FakeClient:
    def __init__(self, server):
        self.server = server
        self.downloaded = []
        self.sent_files = []

    async def iter_messages(self, entity, limit=200):
        for m in entity:
            yield m

    async def download_media(self, msg, file=None):
        self.downloaded.append(msg)
        return f"{file}fake_downloaded_file.mp4"

    async def send_file(self, target, path, caption=None):
        self.sent_files.append((target, path, caption))
        # The message WE sent gets its own id, distinct from the bot's
        # reply -- these are two separate messages in a real chat, and
        # `run_scenario` relies on that gap to recognize the bot's first
        # reply as "new" (id > our own sent message's id).
        my_msg_id = self.server._new_id()
        self.server.receive_file()
        return FakeMessage(id=my_msg_id, text=None, buttons=None)

    async def get_messages(self, target, min_id=None, ids=None, limit=None):
        if ids is not None:
            return self.server.messages.get(ids)
        if min_id is not None:
            candidates = sorted(
                (m for m in self.server.messages.values() if m.id > min_id),
                key=lambda m: m.id,
            )
            return candidates[:1]
        raise AssertionError("test double doesn't support this get_messages() call shape")


class FakeSourceMessage:
    def __init__(self, id):
        self.id = id
        self.video = object()
        self.photo = None
        self.document = None
        self.media = object()


def test_run_scenario_clicks_through_edited_messages_and_sees_final_delivery(capsys):
    # Both STEP_TIMEOUT_SECONDS (a bound default arg) and POLL_INTERVAL_SECONDS
    # (read live from the module each call) need to be small, or this test
    # would take the real 60s timeout to finish waiting for "no more updates".
    original_defaults = userbot_test.wait_for_bot_update.__defaults__
    userbot_test.wait_for_bot_update.__defaults__ = (0.3,)
    original_poll = userbot_test.POLL_INTERVAL_SECONDS
    userbot_test.POLL_INTERVAL_SECONDS = 0.02

    try:
        server = FakeBotServer()
        client = FakeClient(server)
        source_chat = [FakeSourceMessage(id=1)]
        used_ids = set()

        asyncio.run(userbot_test.run_scenario(
            client,
            "video_quality_360",
            lambda m: m.video is not None,
            ["360p", "✅ آپلود کن"],
            used_ids,
            source_chat,
        ))
    finally:
        userbot_test.wait_for_bot_update.__defaults__ = original_defaults
        userbot_test.POLL_INTERVAL_SECONDS = original_poll

    out = capsys.readouterr().out

    assert "Clicked: '360p'" in out
    assert "Clicked: '✅ آپلود کن'" in out
    # this is the actual proof the fix works: the reply that had "360p" on
    # it was the SAME message id as the one that later showed "✅ آپلود کن"
    # -- an edit, not a new message -- and the script still caught it.
    assert "edited in place" in out
    assert len(client.sent_files) == 1
    assert len(client.downloaded) == 1
