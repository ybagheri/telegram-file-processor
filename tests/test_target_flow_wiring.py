"""
Confirms the "not_admin" rejection from services/target_resolver.py
actually surfaces correctly through both places that call it:
handlers/settings.py (a user's default target) and handlers/files.py (a
per-file target override). The resolver's own logic is fully covered by
tests/test_target_resolver.py — this just checks the wiring end to end.
"""
from types import SimpleNamespace

import pytest

import handlers.files as files_module
import handlers.settings as settings_module

from models.pending_file import PendingFile

import state as shared_state


def _message(text, user_id=555):
    async def answer(text, **kwargs):
        answers.append(text)

    answers = []

    message = SimpleNamespace(
        text=text,
        document=None,
        video=None,
        audio=None,
        from_user=SimpleNamespace(id=user_id),
        answer=answer,
    )

    return message, answers


async def test_settings_target_shows_not_admin_message_and_does_not_save(monkeypatch):

    async def fake_resolve_target(message):
        return None, None, "not_admin"

    monkeypatch.setattr(settings_module, "resolve_target", fake_resolve_target)

    saved = []

    async def fake_update(user_id, **kwargs):
        saved.append(kwargs)

    monkeypatch.setattr(settings_module.settings_store, "update", fake_update)

    shared_state.awaiting_state[555] = "settings_target"

    message, answers = _message("@someone_elses_group")

    handled = await settings_module.handle_settings_awaited_input(message, "settings_target")

    assert handled is True
    assert saved == []  # nothing persisted
    assert "مدیر" in answers[0]
    shared_state.awaiting_state.pop(555, None)


async def test_file_target_override_shows_not_admin_message_and_does_not_save(monkeypatch):

    async def fake_resolve_target(message):
        return None, None, "not_admin"

    monkeypatch.setattr(files_module, "resolve_target", fake_resolve_target)

    pid = "test-pid-1"

    pending = PendingFile(
        user_id=555,
        chat_id=555,
        file_name="movie.mp4",
        file_type="VIDEO",
        source_message=None,
    )

    shared_state.pending_files[pid] = pending
    shared_state.awaiting_state[555] = f"file:{pid}:target"

    message, answers = _message("@someone_elses_group")

    handled = await files_module.handle_file_awaited_input(message, f"file:{pid}:target")

    assert handled is True
    assert "target_chat_id" not in pending.options
    assert "مدیر" in answers[0]

    shared_state.pending_files.pop(pid, None)
    shared_state.awaiting_state.pop(555, None)
