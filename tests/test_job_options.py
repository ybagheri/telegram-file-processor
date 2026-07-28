import dataclasses

from core.job_options import JobOptions


def test_defaults_match_documented_values():
    opts = JobOptions()
    assert opts.quality == "360"
    assert opts.watermark is True
    assert opts.upload_as == "video"
    assert opts.target_chat_id == 0
    assert opts.sort_mode == "name"
    assert opts.sort_order == "asc"
    assert opts.exclude_text == ""
    assert opts.remove_words == []
    assert opts.extra == {}


def test_mutable_defaults_are_not_shared_between_instances():
    # dataclass `field(default_factory=...)` bugs (using a bare mutable
    # default instead) are exactly the kind of thing that's invisible until
    # two jobs run back-to-back and start bleeding state into each other.
    a = JobOptions()
    b = JobOptions()

    a.remove_words.append("ad-tag")
    a.extra["foo"] = "bar"

    assert b.remove_words == [], "remove_words default is shared across instances!"
    assert b.extra == {}, "extra default is shared across instances!"


def test_can_override_any_field_via_constructor():
    opts = JobOptions(
        quality="720",
        watermark=False,
        upload_as="document",
        target_chat_id=-1001234567890,
        sort_mode="date",
        sort_order="desc",
        exclude_text="[ads]",
    )
    assert opts.quality == "720"
    assert opts.watermark is False
    assert opts.upload_as == "document"
    assert opts.target_chat_id == -1001234567890
    assert opts.sort_mode == "date"
    assert opts.sort_order == "desc"
    assert opts.exclude_text == "[ads]"


def test_is_a_real_dataclass_with_slots():
    # `slots=True` means no accidental extra attributes can be bolted onto
    # a JobOptions instance at runtime (e.g. a typo'd kwarg elsewhere in
    # the codebase would raise instead of silently creating a new field).
    assert dataclasses.is_dataclass(JobOptions)
    opts = JobOptions()
    try:
        opts.totally_made_up_field = "x"
        assert False, "expected AttributeError due to __slots__"
    except AttributeError:
        pass
