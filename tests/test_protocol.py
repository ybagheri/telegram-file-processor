import pytest

from core.protocol import Protocol
from core.constants import MessageType


def test_round_trip():
    payload = Protocol.create_result(user_id=1, job_id="abc", files=["a.mp4"])
    encoded = Protocol.encode(payload)
    decoded = Protocol.decode(encoded)

    assert decoded["user_id"] == 1
    assert decoded["job_id"] == "abc"
    assert decoded["files"] == ["a.mp4"]
    assert decoded["type"] == MessageType.RESULT.value


def test_rejects_foreign_project_messages():
    tampered = '{"project":"someone_else","version":1,"type":"job"}'
    with pytest.raises(ValueError):
        Protocol.decode(tampered)


def test_rejects_unknown_protocol_version():
    tampered = '{"project":"IELTS","version":999,"type":"job"}'
    with pytest.raises(ValueError):
        Protocol.decode(tampered)


def test_create_error_carries_target_chat_id():
    payload = Protocol.create_error(
        user_id=1, job_id="x", message="boom", target_chat_id=-100999,
    )
    assert payload["target_chat_id"] == -100999
