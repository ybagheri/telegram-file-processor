"""
Tests for the bridge heartbeat: the protocol factory, the bot-side
"worker last seen" tracking in handlers/bridge.py, and the admin
/status staleness formatting (format_worker_status).
"""

import time

from types import SimpleNamespace

from config import Heartbeat
from core.constants import MessageType, PROJECT_IDENTIFIER
from core.protocol import Protocol

import handlers.bridge as bridge

from handlers.admin import format_worker_status

import state as shared_state


# ======================================================================
# Protocol
# ======================================================================


def test_heartbeat_factory_round_trips_through_the_bridge():

    payload = Protocol.create_heartbeat()

    assert payload["type"] == MessageType.HEARTBEAT

    decoded = Protocol.decode(Protocol.encode(payload))

    assert decoded["type"] == MessageType.HEARTBEAT.value
    assert decoded["project"] == PROJECT_IDENTIFIER
    assert "user_id" not in decoded  # by design — never relayed to a user


# ======================================================================
# Bot-side tracking (handlers/bridge.py)
# ======================================================================


def _fake_bridge_message(text: str):

    return SimpleNamespace(
        text=text,
        caption=None,
        # Present but irrelevant — heartbeats return before any
        # media/delivery logic runs.
        document=None,
        video=None,
        audio=None,
        voice=None,
        photo=None,
    )


def test_heartbeat_updates_worker_last_seen_and_relays_nothing(monkeypatch):

    shared_state.worker_last_seen.pop("worker", None)

    sent = []

    async def fake_send_text(chat_id, text, **kwargs):
        sent.append((chat_id, text))

    monkeypatch.setattr(bridge.telegram_service, "send_text", fake_send_text)

    encoded = Protocol.encode(Protocol.create_heartbeat())

    import asyncio

    asyncio.run(bridge.handle_bridge_message(_fake_bridge_message(encoded)))

    assert "worker" in shared_state.worker_last_seen
    assert shared_state.worker_last_seen["worker"] > 0
    assert sent == []  # nothing was relayed anywhere

    shared_state.worker_last_seen.pop("worker", None)


def test_non_heartbeat_payload_without_user_id_does_not_mark_worker_seen(monkeypatch):

    shared_state.worker_last_seen.pop("worker", None)

    sent = []

    async def fake_send_text(chat_id, text, **kwargs):
        sent.append((chat_id, text))

    monkeypatch.setattr(bridge.telegram_service, "send_text", fake_send_text)

    # A valid-protocol message of an unknown/undeliverable shape (no
    # user_id) must be dropped without ever marking the worker seen.
    payload = {"type": MessageType.INFO.value, "message": "no recipient"}

    import asyncio

    asyncio.run(
        bridge.handle_bridge_message(
            _fake_bridge_message(Protocol.encode(payload))
        )
    )

    assert "worker" not in shared_state.worker_last_seen
    assert sent == []


# ======================================================================
# /status staleness formatting (handlers/admin.py)
# ======================================================================


def test_status_never_seen_reports_missing_worker():

    text = format_worker_status(None, now=1000.0, interval_seconds=300)

    assert "هیچ سیگنالی" in text


def test_status_recent_heartbeat_reports_healthy():

    text = format_worker_status(
        last_seen=1000.0 - 30,
        now=1000.0,
        interval_seconds=300,
    )

    assert "✅" in text


def test_status_stale_heartbeat_warns():

    # 10 minutes since the last heartbeat of a 5-minute interval.
    text = format_worker_status(
        last_seen=1000.0 - 600,
        now=1000.0,
        interval_seconds=300,
    )

    assert "⚠️" in text
    assert "10 دقیقه" in text


def test_status_uses_configured_interval_for_the_threshold():

    # Just inside interval + 60s grace: healthy.
    text = format_worker_status(
        last_seen=1000.0 - 350,
        now=1000.0,
        interval_seconds=300,
    )
    assert "✅" in text

    # Just outside it: warning.
    text = format_worker_status(
        last_seen=1000.0 - 400,
        now=1000.0,
        interval_seconds=300,
    )
    assert "⚠️" in text