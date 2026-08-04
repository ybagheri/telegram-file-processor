import pytest

from keyboards.admin import admin_panel_keyboard, duration_keyboard, confirm_keyboard
from keyboards.files import quality_keyboard, options_keyboard, target_keyboard
from keyboards.photo import photo_confirm_keyboard
from keyboards.constants import DURATION_OPTIONS


def _flat_buttons(markup):
    return [b for row in markup.inline_keyboard for b in row]


def test_admin_panel_keyboard_has_all_six_actions():
    kb = admin_panel_keyboard()
    callback_datas = {b.callback_data for b in _flat_buttons(kb)}
    assert callback_datas == {
        "admin:add_user", "admin:list_users", "admin:renew_user",
        "admin:toggle_user", "admin:delete_user", "admin:broadcast_pending",
    }


def test_duration_keyboard_without_target_id():
    kb = duration_keyboard("add")
    callback_datas = [b.callback_data for b in _flat_buttons(kb)]
    assert callback_datas == [f"admin:dur:add:{days}" for _, days in DURATION_OPTIONS]


def test_duration_keyboard_with_target_id_embeds_it():
    kb = duration_keyboard("renew", target_id=999)
    callback_datas = [b.callback_data for b in _flat_buttons(kb)]
    assert callback_datas == [f"admin:dur:renew:{days}:999" for _, days in DURATION_OPTIONS]


def test_confirm_keyboard_has_confirm_and_cancel():
    kb = confirm_keyboard(confirm_data="do:it", cancel_data="do:cancel")
    buttons = _flat_buttons(kb)
    assert len(buttons) == 2
    assert buttons[0].callback_data == "do:it"
    assert buttons[1].callback_data == "do:cancel"


def test_confirm_keyboard_custom_labels():
    kb = confirm_keyboard(confirm_data="x", cancel_data="y", confirm_label="🗑 حذف", cancel_label="بیخیال")
    buttons = _flat_buttons(kb)
    assert buttons[0].text == "🗑 حذف"
    assert buttons[1].text == "بیخیال"


def test_quality_keyboard_includes_every_quality_option():
    pid = "abc123"
    kb = quality_keyboard(pid)
    callback_datas = {b.callback_data for b in _flat_buttons(kb)}
    for suffix in ("144", "240", "360", "480", "720", "mp3", "m4a", "voice", "thumbs"):
        assert f"q:{pid}:{suffix}" in callback_datas


def test_target_keyboard_has_me_and_new():
    kb = target_keyboard("pid1")
    callback_datas = [b.callback_data for b in _flat_buttons(kb)]
    assert callback_datas == ["t:pid1:me", "t:pid1:new"]


def test_photo_confirm_keyboard_has_four_actions():
    kb = photo_confirm_keyboard("pid1")
    callback_datas = [b.callback_data for b in _flat_buttons(kb)]
    assert callback_datas == [
        "pw:pid1:apply", "pw:pid1:changelogo", "pw:pid1:changepos", "pw:pid1:cancel",
    ]


class FakePendingFile:
    """Minimal stand-in for bot.py's PendingFile dataclass -- only the
    attributes options_keyboard actually reads."""
    def __init__(self, file_type, options=None, is_multipart=False, parts_total=0, part_message_ids=None):
        self.file_type = file_type
        self.options = options or {}
        self.is_multipart = is_multipart
        self.parts_total = parts_total
        self.part_message_ids = part_message_ids or []


def test_options_keyboard_reads_from_injected_pending_files_dict():
    # this is the whole point of the phase-B change: no hidden global,
    # the caller's own dict is what gets read
    pending_files = {"pid1": FakePendingFile(file_type="VIDEO", options={"quality": "360"})}
    kb = options_keyboard("pid1", pending_files)
    callback_datas = {b.callback_data for b in _flat_buttons(kb)}
    assert "o:pid1:go" in callback_datas
    assert "o:pid1:cancel" in callback_datas


def test_options_keyboard_video_shows_watermark_and_upload_as():
    pending_files = {"pid1": FakePendingFile(file_type="VIDEO", options={"quality": "360", "watermark": True})}
    kb = options_keyboard("pid1", pending_files)
    callback_datas = {b.callback_data for b in _flat_buttons(kb)}
    assert "o:pid1:watermark" in callback_datas
    assert "o:pid1:upload_as" in callback_datas


def test_options_keyboard_voice_only_hides_watermark_row():
    # quality == "voice" means no actual video is being produced, so the
    # watermark/upload_as/thumbnail rows shouldn't be offered
    pending_files = {"pid1": FakePendingFile(file_type="VIDEO", options={"quality": "voice"})}
    kb = options_keyboard("pid1", pending_files)
    callback_datas = {b.callback_data for b in _flat_buttons(kb)}
    assert "o:pid1:watermark" not in callback_datas
    assert "o:pid1:upload_as" not in callback_datas


def test_options_keyboard_archive_shows_multipart_and_password_rows():
    pending_files = {"pid1": FakePendingFile(file_type="ARCHIVE", options={})}
    kb = options_keyboard("pid1", pending_files)
    callback_datas = {b.callback_data for b in _flat_buttons(kb)}
    assert "o:pid1:multipart" in callback_datas
    assert "o:pid1:archive_password" in callback_datas


def test_options_keyboard_archive_in_progress_shows_status_not_multipart_button():
    pending_files = {
        "pid1": FakePendingFile(
            file_type="ARCHIVE", options={}, is_multipart=True,
            parts_total=3, part_message_ids=[1, 2],
        )
    }
    kb = options_keyboard("pid1", pending_files)
    callback_datas = {b.callback_data for b in _flat_buttons(kb)}
    assert "o:pid1:multipart" not in callback_datas
    assert "nothing" in callback_datas


def test_options_keyboard_audio_shows_title_row():
    pending_files = {"pid1": FakePendingFile(file_type="AUDIO", options={})}
    kb = options_keyboard("pid1", pending_files)
    callback_datas = {b.callback_data for b in _flat_buttons(kb)}
    assert "o:pid1:title" in callback_datas


def test_logo_position_keyboard_marks_current_position():
    from keyboards.settings import logo_position_keyboard

    kb = logo_position_keyboard("center")
    buttons = _flat_buttons(kb)
    marked = [b for b in buttons if b.text.startswith("✅")]
    assert len(marked) == 1
    assert [b.callback_data for b in buttons if b.callback_data == "slogopos:center"]


def test_settings_text_and_keyboard_uses_injected_store(tmp_path, monkeypatch):
    from services.settings_store import SettingsStore
    import keyboards.settings as settings_kb

    scratch_store = SettingsStore(tmp_path / "settings.db", None)
    monkeypatch.setattr(settings_kb, "settings_store", scratch_store)

    result = settings_kb.settings_text_and_keyboard(12345)
    assert "text" in result and "reply_markup" in result
    assert "۳۶۰" not in result["text"]  # defaults are ASCII digits, e.g. "360p"
    assert "360p" in result["text"]

    callback_datas = {b.callback_data for b in _flat_buttons(result["reply_markup"])}
    assert "s:quality" in callback_datas
    assert "s:watermark" in callback_datas
