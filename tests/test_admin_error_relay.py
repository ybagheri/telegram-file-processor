"""
Tests for the admin error-report relay: the ADMIN_ERROR bridge message
type (core/protocol.py) and handlers/bridge.py's delivery of the report
to every configured admin. Uses direct handler calls with stubbed
Telegram sends — the bridge router's chat-id filter has no catch-all
precedence risk (see handlers/bridge.py's docstring), so routing-level
wiring is already covered elsewhere.
"""

from types import SimpleNamespace

import pytest

from config import Telegram

from core.protocol import Protocol

import handlers.bridge as bridge


def _bridge_message(text):

    return SimpleNamespace(
        chat=SimpleNamespace(id=Telegram.GROUP_ID),
        message_id=1,
        text=text,
        caption=None,
        document=None,
        video=None,
        audio=None,
        voice=None,
        photo=None,
    )


@pytest.fixture
def sent(monkeypatch):

    calls = []

    async def fake_send_text(chat_id, text, *, parse_mode=None):
        calls.append((chat_id, text, parse_mode))

    monkeypatch.setattr(
        bridge.telegram_service,
        "send_text",
        fake_send_text,
    )

    return calls


def _admin_error_payload(**overrides):

    payload = Protocol.create_admin_error(
        report="🚨 <b>Job failure report</b>\nstage=PROCESSING",
        user_id=777,
        job_id="job123",
    )

    payload.update(overrides)

    return Protocol.encode(payload)


# ======================================================================
# Protocol
# ======================================================================


def test_admin_error_round_trips_through_the_bridge():

    decoded = Protocol.decode(_admin_error_payload())

    assert decoded["type"] == "admin_error"
    assert decoded["job_id"] == "job123"
    assert "Job failure report" in decoded["report"]


# ======================================================================
# Relay
# ======================================================================


@pytest.mark.asyncio
async def test_report_is_delivered_to_every_admin_as_html(monkeypatch, sent):

    monkeypatch.setattr(Telegram, "ADMIN_IDS", [111, 222])

    await bridge.handle_bridge_message(_bridge_message(_admin_error_payload()))

    assert {chat_id for chat_id, _, _ in sent} == {111, 222}

    for _, text, parse_mode in sent:
        assert "Job failure report" in text
        assert parse_mode == "HTML"


@pytest.mark.asyncio
async def test_report_is_never_relayed_to_the_user(monkeypatch, sent):

    monkeypatch.setattr(Telegram, "ADMIN_IDS", [111])

    await bridge.handle_bridge_message(_bridge_message(_admin_error_payload(user_id=777)))

    assert all(chat_id != 777 for chat_id, _, _ in sent)


@pytest.mark.asyncio
async def test_one_failing_admin_does_not_block_the_others(monkeypatch, sent):

    monkeypatch.setattr(Telegram, "ADMIN_IDS", [111, 222, 333])

    async def flaky_send_text(chat_id, text, *, parse_mode=None):
        if chat_id == 222:
            raise RuntimeError("telegram hiccup")
        calls_recorder = sent
        calls_recorder.append((chat_id, text, parse_mode))

    monkeypatch.setattr(
        bridge.telegram_service,
        "send_text",
        flaky_send_text,
    )

    await bridge.handle_bridge_message(_bridge_message(_admin_error_payload()))

    assert {chat_id for chat_id, _, _ in sent} == {111, 333}


@pytest.mark.asyncio
async def test_report_without_user_id_still_reaches_the_admins(monkeypatch, sent):
    """Pre-Job failures have no user_id — the relay runs before the
    bridge's user_id gate."""

    monkeypatch.setattr(Telegram, "ADMIN_IDS", [111])

    await bridge.handle_bridge_message(
        _bridge_message(_admin_error_payload(user_id=None, job_id="unknown:5"))
    )

    assert len(sent) == 1


@pytest.mark.asyncio
async def test_empty_report_is_dropped_silently(monkeypatch, sent):

    monkeypatch.setattr(Telegram, "ADMIN_IDS", [111])

    await bridge.handle_bridge_message(
        _bridge_message(_admin_error_payload(report=""))
    )

    assert sent == []
