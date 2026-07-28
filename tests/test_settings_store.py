import json

import pytest

from services.settings_store import SettingsStore, DEFAULTS


@pytest.fixture
def store(tmp_path):
    return SettingsStore(tmp_path / "settings.db", None)


def test_unknown_user_gets_pure_defaults(store):
    assert store.get(999999) == DEFAULTS


@pytest.mark.asyncio
async def test_update_then_get_reflects_change(store):
    await store.update(1, quality="720")
    assert store.get(1)["quality"] == "720"
    assert store.get(1)["watermark"] is True  # untouched fields keep defaults


@pytest.mark.asyncio
async def test_partial_updates_accumulate(store):
    await store.update(1, quality="480")
    await store.update(1, watermark=False, upload_as="document")
    got = store.get(1)
    assert got["quality"] == "480"
    assert got["watermark"] is False
    assert got["upload_as"] == "document"


@pytest.mark.asyncio
async def test_boolean_field_round_trips_correctly(store):
    await store.update(1, watermark=False)
    assert store.get(1)["watermark"] is False
    await store.update(1, watermark=True)
    assert store.get(1)["watermark"] is True


@pytest.mark.asyncio
async def test_unknown_setting_name_raises_instead_of_silently_ignored(store):
    with pytest.raises(ValueError):
        await store.update(1, this_field_does_not_exist="x")
    # and definitely didn't get partially applied
    assert store.get(1) == DEFAULTS


@pytest.mark.asyncio
async def test_update_with_no_changes_is_a_noop(store):
    await store.update(1)  # should not raise, should not create a row unnecessarily
    assert store.get(1) == DEFAULTS


@pytest.mark.asyncio
async def test_sql_injection_payloads_stored_as_inert_text(store):
    payload = "'; DROP TABLE user_settings; --"
    await store.update(1, artist=payload, media_caption=payload, exclude_text=payload)
    got = store.get(1)
    assert got["artist"] == payload
    assert got["media_caption"] == payload
    assert got["exclude_text"] == payload


@pytest.mark.asyncio
async def test_legacy_json_migration_runs_once_and_backs_up_file(tmp_path):
    legacy_path = tmp_path / "user_settings.json"
    legacy_data = {
        "555": {"quality": "720", "watermark": False, "target_label": "کانال من"},
        "666": {"artist": "DJ Test"},
    }
    legacy_path.write_text(json.dumps(legacy_data, ensure_ascii=False), encoding="utf-8")

    db_path = tmp_path / "settings.db"
    store = SettingsStore(db_path, legacy_path)

    s555 = store.get(555)
    assert s555["quality"] == "720"
    assert s555["watermark"] is False
    assert s555["target_label"] == "کانال من"
    assert s555["sort_mode"] == "name"  # untouched field falls back to default

    s666 = store.get(666)
    assert s666["artist"] == "DJ Test"
    assert s666["quality"] == "360"  # default, wasn't in the legacy record

    assert not legacy_path.exists()
    assert (tmp_path / "user_settings.json.migrated").exists()

    # re-opening must NOT re-import / duplicate
    store2 = SettingsStore(db_path, legacy_path)
    assert store2.get(555)["quality"] == "720"
