import pytest

from core.constants import MessageType, PROJECT_IDENTIFIER
from core.protocol import Protocol, PROTOCOL_VERSION


def test_encode_includes_project_and_version():
    text = Protocol.encode({"type": MessageType.JOB, "user_id": 1})
    decoded = Protocol.decode(text)
    assert decoded["project"] == PROJECT_IDENTIFIER
    assert decoded["version"] == PROTOCOL_VERSION
    assert decoded["type"] == MessageType.JOB
    assert decoded["user_id"] == 1


def test_decode_round_trip_preserves_payload():
    payload = {"type": MessageType.RESULT, "user_id": 42, "files": ["a.mp4", "b.mp4"]}
    text = Protocol.encode(payload)
    decoded = Protocol.decode(text)
    for key, value in payload.items():
        assert decoded[key] == value


def test_decode_rejects_wrong_project():
    import json
    text = json.dumps({"project": "someone-elses-app", "version": PROTOCOL_VERSION})
    with pytest.raises(ValueError, match="Invalid project"):
        Protocol.decode(text)


def test_decode_rejects_wrong_version():
    import json
    text = json.dumps({"project": PROJECT_IDENTIFIER, "version": PROTOCOL_VERSION + 1})
    with pytest.raises(ValueError, match="Unsupported protocol version"):
        Protocol.decode(text)


def test_decode_rejects_garbage_json():
    with pytest.raises(Exception):
        Protocol.decode("not json at all { [ ]")


@pytest.mark.parametrize(
    "factory,kwargs,expected_type",
    [
        (Protocol.create_job, dict(user_id=1, options={"quality": "720"}), MessageType.JOB),
        (Protocol.create_result, dict(user_id=1, job_id="j1", files=["x"], target_chat_id=0), MessageType.RESULT),
        (Protocol.create_password_request, dict(user_id=1, job_id="j1", filename="a.rar"), MessageType.PASSWORD_REQUEST),
        (Protocol.create_password_response, dict(user_id=1, job_id="j1", password="secret"), MessageType.PASSWORD_RESPONSE),
        (Protocol.create_error, dict(user_id=1, job_id="j1", message="oops"), MessageType.ERROR),
        (Protocol.create_info, dict(user_id=1, job_id="j1", message="hi"), MessageType.INFO),
        (Protocol.create_folder, dict(user_id=1, job_id="j1", folder="Season 1"), MessageType.FOLDER),
        (Protocol.create_done, dict(user_id=1, job_id="j1"), MessageType.DONE),
    ],
)
def test_message_factories_set_correct_type_and_round_trip(factory, kwargs, expected_type):
    payload = factory(**kwargs)
    assert payload["type"] == expected_type

    # every factory output must itself be a valid encode/decode round trip,
    # since this is exactly what goes over the bridge as a message caption.
    text = Protocol.encode(payload)
    decoded = Protocol.decode(text)
    assert decoded["type"] == expected_type
    for key, value in kwargs.items():
        assert decoded[key] == value


def test_create_job_defaults_options_to_empty_dict_not_none():
    payload = Protocol.create_job(user_id=1, options=None)
    assert payload["options"] == {}
