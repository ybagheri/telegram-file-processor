import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scenario_engine import find_button, next_click_target, message_snapshot


class FakeButton:
    def __init__(self, text):
        self.text = text


class FakeMessage:
    def __init__(self, buttons=None, text=None):
        self.buttons = buttons  # list[list[FakeButton]] or None, like Telethon's real Message.buttons
        self.text = text


def test_find_button_matches_exact_text():
    msg = FakeMessage(buttons=[[FakeButton("360p"), FakeButton("480p")]])
    found = find_button(msg, "360p")
    assert found is not None and found.text == "360p"


def test_find_button_matches_substring():
    msg = FakeMessage(buttons=[[FakeButton("🖼 فقط کولاژ تامبنیل (بدون تبدیل ویدیو)")]])
    # scenarios intentionally use a shorter substring so minor wording
    # tweaks elsewhere in the label don't break matching
    found = find_button(msg, "🖼 فقط کولاژ تامبنیل")
    assert found is not None


def test_find_button_returns_none_when_missing():
    msg = FakeMessage(buttons=[[FakeButton("144p")]])
    assert find_button(msg, "720p") is None


def test_find_button_handles_no_keyboard():
    assert find_button(FakeMessage(buttons=None), "anything") is None


def test_find_button_handles_empty_row_list():
    assert find_button(FakeMessage(buttons=[]), "anything") is None


def test_next_click_target_finds_the_first_matching_step_in_order():
    msg = FakeMessage(buttons=[[FakeButton("✅ آپلود کن")]])
    remaining = ["360p", "✅ آپلود کن"]
    idx, button = next_click_target(remaining, msg)
    assert idx == 1
    assert button.text == "✅ آپلود کن"


def test_next_click_target_prefers_earliest_remaining_step_present():
    # if BOTH steps happen to be on the same keyboard, the earlier one in
    # `remaining` should win, since it represents what hasn't happened yet
    msg = FakeMessage(buttons=[[FakeButton("360p"), FakeButton("✅ آپلود کن")]])
    remaining = ["360p", "✅ آپلود کن"]
    idx, button = next_click_target(remaining, msg)
    assert idx == 0
    assert button.text == "360p"


def test_next_click_target_returns_none_tuple_when_nothing_matches():
    msg = FakeMessage(buttons=[[FakeButton("❌ لغو")]])
    idx, button = next_click_target(["360p", "✅ آپلود کن"], msg)
    assert idx is None
    assert button is None


def test_next_click_target_with_empty_remaining_never_matches():
    msg = FakeMessage(buttons=[[FakeButton("✅ آپلود کن")]])
    idx, button = next_click_target([], msg)
    assert idx is None
    assert button is None


def test_message_snapshot_equal_for_identical_state():
    msg_a = FakeMessage(text="pick a quality", buttons=[[FakeButton("360p")], [FakeButton("720p")]])
    msg_b = FakeMessage(text="pick a quality", buttons=[[FakeButton("360p")], [FakeButton("720p")]])
    assert message_snapshot(msg_a) == message_snapshot(msg_b)


def test_message_snapshot_differs_after_text_edit():
    before = FakeMessage(text="pick a quality", buttons=[[FakeButton("360p")]])
    after = FakeMessage(text="confirm your options", buttons=[[FakeButton("✅ آپلود کن")]])
    assert message_snapshot(before) != message_snapshot(after)


def test_message_snapshot_differs_when_only_buttons_change():
    # this is exactly what bot.py's `edit_reply_markup`-only calls do: text
    # stays the same, only the keyboard changes
    before = FakeMessage(text="same text", buttons=[[FakeButton("📄 سند")]])
    after = FakeMessage(text="same text", buttons=[[FakeButton("🎬 ویدیو")]])
    assert message_snapshot(before) != message_snapshot(after)


def test_message_snapshot_handles_no_keyboard():
    msg = FakeMessage(text="just a plain status update", buttons=None)
    # should not raise, and should be internally consistent
    assert message_snapshot(msg) == message_snapshot(FakeMessage(text="just a plain status update", buttons=None))
