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
"""
import os

os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "test-hash")
os.environ.setdefault("BOT_TOKEN", "123456:test-token-test-token-test-tok")
os.environ.setdefault("GROUP_ID", "-100123456789")
os.environ.setdefault("SESSION_NAME", "pytest_session")
os.environ.setdefault("ADMIN_IDS", "111,222")
os.environ.setdefault("ADMIN_CONTACT_USERNAME", "@test_admin")

