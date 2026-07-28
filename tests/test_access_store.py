import json
import time

import pytest

from services.access_store import AccessStore


@pytest.fixture
def store(tmp_path):
    return AccessStore(tmp_path / "access.db", None)


@pytest.mark.asyncio
async def test_add_and_get_roundtrip(store):
    await store.add(100, label="Ali", name="Ali", username="ali_u", added_by=1, expires_at=None)
    info = store.get(100)
    assert info["label"] == "Ali"
    assert info["name"] == "Ali"
    assert info["username"] == "ali_u"
    assert info["active"] is True
    assert info["expires_at"] is None


def test_get_unknown_user_returns_none(store):
    assert store.get(999999) is None


@pytest.mark.asyncio
async def test_is_authorized_true_for_unlimited_active_user(store):
    await store.add(1, expires_at=None)
    assert store.is_authorized(1) is True


@pytest.mark.asyncio
async def test_is_authorized_false_when_expired(store):
    await store.add(1, expires_at=time.time() - 10)
    assert store.is_authorized(1) is False
    assert store.is_expired(store.get(1)) is True


@pytest.mark.asyncio
async def test_is_authorized_false_when_inactive(store):
    await store.add(1, expires_at=None)
    await store.set_active(1, False)
    assert store.is_authorized(1) is False


@pytest.mark.asyncio
async def test_update_expiry_on_missing_user_returns_false(store):
    assert await store.update_expiry(424242, time.time() + 1000) is False


@pytest.mark.asyncio
async def test_update_expiry_resets_reminder_marker(store):
    await store.add(1, expires_at=time.time() + 86400)
    info = store.get(1)
    await store.mark_reminded(1, info["expires_at"])
    assert store.get(1)["last_reminder_expiry"] == info["expires_at"]

    new_expiry = time.time() + 2 * 86400
    await store.update_expiry(1, new_expiry)
    assert store.get(1)["last_reminder_expiry"] is None


@pytest.mark.asyncio
async def test_set_active_toggle_and_missing_user(store):
    await store.add(1, expires_at=None)
    assert await store.set_active(1, False) is True
    assert store.get(1)["active"] is False
    assert await store.set_active(1, True) is True
    assert store.get(1)["active"] is True
    assert await store.set_active(555555, False) is False


@pytest.mark.asyncio
async def test_remove_deletes_record(store):
    await store.add(1, expires_at=None)
    assert await store.remove(1) is True
    assert store.get(1) is None
    assert await store.remove(1) is False  # already gone


@pytest.mark.asyncio
async def test_re_adding_reactivates_and_resets_reminder(store):
    await store.add(1, expires_at=time.time() + 86400)
    await store.set_active(1, False)
    await store.mark_reminded(1, store.get(1)["expires_at"])

    await store.add(1, label="Ali again", expires_at=None)
    info = store.get(1)
    assert info["active"] is True, "re-adding should reactivate"
    assert info["last_reminder_expiry"] is None, "re-adding should reset reminder marker"
    assert info["label"] == "Ali again"


@pytest.mark.asyncio
async def test_list_expiring_soon_filters_correctly(store):
    now = time.time()
    await store.add(1, name="Expired", expires_at=now - 3600)
    await store.add(2, name="Soon", expires_at=now + 86400)
    await store.add(3, name="Far", expires_at=now + 30 * 86400)
    await store.add(4, name="Unlimited", expires_at=None)
    await store.add(5, name="Soon but disabled", expires_at=now + 86400)
    await store.set_active(5, False)

    due = store.list_expiring_soon(3 * 86400)
    assert [d["name"] for d in due] == ["Soon"]


@pytest.mark.asyncio
async def test_mark_reminded_suppresses_until_expiry_changes(store):
    now = time.time()
    await store.add(1, name="Soon", expires_at=now + 86400)
    due = store.list_expiring_soon(3 * 86400)
    assert len(due) == 1

    await store.mark_reminded(1, due[0]["expires_at"])
    assert store.list_expiring_soon(3 * 86400) == []

    await store.update_expiry(1, now + 2 * 86400)
    assert len(store.list_expiring_soon(3 * 86400)) == 1


@pytest.mark.asyncio
async def test_sql_injection_payloads_stored_as_inert_text(store):
    payloads = [
        "'; DROP TABLE authorized_users; --",
        "' OR '1'='1",
        "Robert'); DROP TABLE authorized_users;--",
    ]
    for i, payload in enumerate(payloads):
        uid = 9000 + i
        await store.add(uid, label=payload, name=payload, username=payload, expires_at=None)
        info = store.get(uid)
        assert info["name"] == payload  # stored verbatim, never executed

    assert len(store.list_all()) == len(payloads)  # table intact, right row count


@pytest.mark.asyncio
async def test_legacy_json_migration_runs_once_and_backs_up_file(tmp_path):
    legacy_path = tmp_path / "authorized_users.json"
    legacy_data = {
        "111": {"label": "Old", "name": "Old", "active": True, "expires_at": None},
        "222": {"label": "Disabled", "active": False, "expires_at": 1.0},
    }
    legacy_path.write_text(json.dumps(legacy_data), encoding="utf-8")

    db_path = tmp_path / "access.db"
    store = AccessStore(db_path, legacy_path)

    assert store.get(111)["name"] == "Old"
    assert store.get(222)["active"] is False
    assert not legacy_path.exists()
    assert (tmp_path / "authorized_users.json.migrated").exists()

    # re-opening must NOT re-import / duplicate
    store2 = AccessStore(db_path, legacy_path)
    assert len(store2.list_all()) == 2


def test_schema_migration_adds_reminder_column_to_old_db(tmp_path):
    import sqlite3
    db_path = tmp_path / "access.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE authorized_users (
            user_id INTEGER PRIMARY KEY, label TEXT NOT NULL DEFAULT '',
            name TEXT NOT NULL DEFAULT '', username TEXT NOT NULL DEFAULT '',
            added_by INTEGER, added_at REAL NOT NULL, expires_at REAL,
            active INTEGER NOT NULL DEFAULT 1
        )
    """)
    conn.execute(
        "INSERT INTO authorized_users (user_id, added_at, active) VALUES (555, ?, 1)",
        (time.time(),),
    )
    conn.commit()
    conn.close()

    store = AccessStore(db_path, None)  # should ALTER TABLE without losing the row
    info = store.get(555)
    assert info is not None
    assert info["last_reminder_expiry"] is None
