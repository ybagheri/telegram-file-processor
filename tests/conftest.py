"""
Shared pytest setup for the pure-logic test suite.

`config.py` reads required settings from the environment at IMPORT time and
raises if they're missing, so fake values must be in place before anything
in this package imports `config` (directly or transitively). That's why
this happens at module load time here, not inside a fixture.

These tests never touch the real `config_data/` directory. `AccessStore`
and `SettingsStore` compute their default DB path once, at import time
(`DB_FILE = Paths.CONFIG / "..."`), so monkeypatching `Paths.CONFIG` after
import wouldn't affect that already-bound module-level constant — instead,
every test constructs its own store instance pointed directly at a
temp-dir DB path (see the `tmp_access_db`/`tmp_settings_db` fixtures in the
individual test files), rather than importing the pre-built singleton.

These are plain assignments, NOT os.environ.setdefault(): on the production
VPS the real credentials are exported in the shell environment (profile /
systemd), so setdefault would be a silent no-op there and the REAL
ADMIN_IDS/BOT_TOKEN would leak into the suite — the admin-auth tests then
fail (or worse, pass for the wrong reason). The suite must be hermetic
regardless of what the host exports.
"""
import os

os.environ["API_ID"] = "12345"
os.environ["API_HASH"] = "test-hash"
os.environ["BOT_TOKEN"] = "123456:test-token-test-token-test-tok"
os.environ["GROUP_ID"] = "-100123456789"
os.environ["SESSION_NAME"] = "pytest_session"
os.environ["ADMIN_IDS"] = "111,222"
os.environ["ADMIN_CONTACT_USERNAME"] = "@test_admin"

import pytest


@pytest.fixture(autouse=True)
def _no_real_telegram_network(monkeypatch):
    """Nothing in this suite should ever attempt a real network call to
    Telegram. Patched at the same low-level boundary used by
    tests/test_router_wiring.py's own `api_calls` fixture
    (`Bot.session.make_request`) rather than the `bot.send_message(...)`
    convenience wrapper: aiogram 3 routes both `bot.send_message(...)`
    (used directly by services/progress.py's ProgressReporter — see its
    module docstring) and `Message.answer(...)` / `feed_update(...)`
    (used by the router-wiring tests) through this same call, so patching
    here covers every call site without shadowing what those tests
    capture for their own assertions. Test files that need to inspect
    the actual calls (test_router_wiring.py) monkeypatch this same
    attribute again themselves afterwards, which simply overrides this
    default for that one test."""

    from aiogram.methods import DeleteMessage, EditMessageText, SendMessage

    from services.telegram import telegram_service

    class _FakeMessage:
        message_id = 999999

    async def _fake_make_request(bot_arg, method, timeout=None):
        if isinstance(method, SendMessage):
            return _FakeMessage()
        if isinstance(method, EditMessageText):
            return True
        if isinstance(method, DeleteMessage):
            return True
        return None

    monkeypatch.setattr(
        telegram_service.bot.session,
        "make_request",
        _fake_make_request,
    )

