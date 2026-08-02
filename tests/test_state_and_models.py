from aiogram.types import Message

from models.pending_file import PendingFile
from models.pending_photo import PendingPhoto
import state


def test_pending_file_defaults():
    pf = PendingFile(
        user_id=1, chat_id=1, file_name="a.mp4", file_type="VIDEO",
        source_message=object(),
    )
    assert pf.options == {}
    assert pf.is_multipart is False
    assert pf.parts_total == 0
    assert pf.part_message_ids == []


def test_pending_file_mutable_defaults_not_shared_between_instances():
    a = PendingFile(user_id=1, chat_id=1, file_name="a", file_type="VIDEO", source_message=object())
    b = PendingFile(user_id=2, chat_id=2, file_name="b", file_type="VIDEO", source_message=object())

    a.options["quality"] = "360"
    a.part_message_ids.append(42)

    assert b.options == {}, "options default is shared across PendingFile instances!"
    assert b.part_message_ids == [], "part_message_ids default is shared across PendingFile instances!"


def test_pending_photo_basic_fields():
    pp = PendingPhoto(user_id=1, chat_id=1, source_message=object())
    assert pp.user_id == 1
    assert pp.chat_id == 1


def test_state_module_exposes_all_expected_dicts():
    for name in (
        "pending_files", "pending_photos", "awaiting_state",
        "admin_flow", "pending_passwords", "job_folder_links",
    ):
        assert hasattr(state, name), f"state.py is missing {name}"
        assert isinstance(getattr(state, name), dict)


def test_state_dicts_are_mutable_and_persist_across_imports():
    # simulates what multiple handler modules importing `state` will rely
    # on: mutating through one import is visible through another
    import state as state_again

    state.pending_files["probe-pid"] = "sentinel"
    assert state_again.pending_files["probe-pid"] == "sentinel"
    del state.pending_files["probe-pid"]
    assert "probe-pid" not in state_again.pending_files


def test_bot_module_shares_the_same_dict_objects_as_state():
    # the actual regression this phase is guarding against: bot.py must
    # import (not copy) these dicts, or handler modules split out later
    # would silently stop seeing each other's mutations.
    import bot

    assert bot.pending_files is state.pending_files
    assert bot.pending_photos is state.pending_photos
    assert bot.awaiting_state is state.awaiting_state
    assert bot.admin_flow is state.admin_flow
    assert bot.pending_passwords is state.pending_passwords
    assert bot.job_folder_links is state.job_folder_links
    assert bot.PendingFile is PendingFile
    assert bot.PendingPhoto is PendingPhoto
