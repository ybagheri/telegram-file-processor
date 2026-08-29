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

